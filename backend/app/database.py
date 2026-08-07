"""Async database engine and session factory (SQLAlchemy 2.0 + asyncpg)."""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional async session."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables. Used for quick start; migrations via Alembic are
    recommended for production schema evolution (Stage 2+)."""
    # Import models so they register on Base.metadata before create_all.
    from app.models import (  # noqa: F401
        app_setting,
        blocked_sender,
        email,
        email_account,
        import_job,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight inline migrations for columns added after initial create_all.
        # create_all won't ALTER existing tables, so we patch missing columns here.
        await conn.exec_driver_sql(
            "ALTER TABLE unified_emails "
            "ADD COLUMN IF NOT EXISTS handled_at TIMESTAMP WITH TIME ZONE"
        )
