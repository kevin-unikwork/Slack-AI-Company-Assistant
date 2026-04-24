from fastapi import APIRouter, Request
from datetime import datetime, timezone

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Simple liveness probe."""
    db_ready = bool(getattr(request.app.state, "db_ready", False))
    chroma_ready = bool(getattr(request.app.state, "chroma_ready", False))
    return {
        "status": "ok" if db_ready and chroma_ready else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "slack-company-bot",
        "dependencies": {
            "database": "up" if db_ready else "down",
            "chroma": "up" if chroma_ready else "down",
        },
    }
