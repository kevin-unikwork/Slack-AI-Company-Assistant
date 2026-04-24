from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middleware.logging import RequestLoggingMiddleware
from app.api.routes.health import router as health_router
from app.api.routes.hr import router as hr_router
from app.api.routes.slack import router as slack_router
from app.config import settings
from app.db.chroma import close_chroma, get_policy_collection
from app.db.session import close_db, init_db
from app.utils.logger import get_logger, setup_logging
from app.scheduler import start_scheduler, stop_scheduler

setup_logging("DEBUG" if settings.debug else "INFO")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle."""
    logger.info("Starting up Slack Company Bot", extra={"env": settings.app_env})
    app.state.db_ready = False
    app.state.chroma_ready = False

    try:
        await init_db()
        app.state.db_ready = True
        logger.info("Database ready")
    except Exception:
        logger.exception("Database init failed; running in degraded mode")

    try:
        collection = get_policy_collection()
        app.state.chroma_ready = True
        logger.info("ChromaDB ready", extra={"collection": collection.name, "count": collection.count()})
    except Exception as exc:
        logger.warning("ChromaDB init warning; continuing", extra={"error": str(exc)})

    # Start APScheduler
    start_scheduler()

    logger.info(
        "Bot ready",
        extra={"db_ready": app.state.db_ready, "chroma_ready": app.state.chroma_ready},
    )
    yield

    logger.info("Shutting down Slack Company Bot")
    # Stop APScheduler
    stop_scheduler()
    
    if getattr(app.state, "db_ready", False):
        await close_db()
    close_chroma()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Slack Company Bot",
        description="Production-ready company-wide Slack AI assistant",
        version="1.0.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception",
            extra={"path": request.url.path, "method": request.method},
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)},
        )

    app.include_router(health_router)
    app.include_router(slack_router)
    app.include_router(hr_router)
    return app


app = create_app()
