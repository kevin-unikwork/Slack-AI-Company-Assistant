from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# NullPool is recommended for async; avoids connection pool issues across event loops
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool,
    future=True,
    connect_args={"server_settings": {"timezone": "UTC"}},
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Create all tables on startup (idempotent)."""
    from app.db.models import Base  # avoid circular import

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialised")


async def close_db() -> None:
    """Dispose engine connection pool on shutdown."""
    await engine.dispose()
    logger.info("Database engine disposed")


async def get_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()