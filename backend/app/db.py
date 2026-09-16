"""Database setup: SQLAlchemy async engine + session factory."""

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=10,
    pool_timeout=10,  # fail fast instead of hanging when the pool is drained
    pool_recycle=300,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Lightweight schema upgrades. The project creates tables via
# Base.metadata.create_all, which is idempotent for NEW tables but does NOT add
# a column to an already-existing table — so a newly-added ORM column would
# break queries against an existing deployment. These idempotent ADD COLUMN
# (IF NOT EXISTS) statements bridge that gap without introducing alembic.
_SCHEMA_UPGRADES: tuple[str, ...] = (
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS profile JSON",
)


async def ensure_schema() -> None:
    """Apply idempotent column additions missing from an existing database."""
    async with engine.begin() as conn:
        for ddl in _SCHEMA_UPGRADES:
            try:
                await conn.execute(text(ddl))
            except Exception:  # a non-Postgres backend may lack IF NOT EXISTS
                existing = await conn.run_sync(
                    lambda sync_conn: {
                        c["name"] for c in inspect(sync_conn).get_columns("sources")
                    }
                )
                if "profile" not in existing:
                    await conn.execute(text("ALTER TABLE sources ADD COLUMN profile JSON"))


async def get_db() -> AsyncSession:
    """FastAPI dependency yielding a database session."""
    async with async_session() as session:
        yield session
