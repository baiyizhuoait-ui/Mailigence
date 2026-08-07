"""Async database engine and session factory (SQLAlchemy 2.0 + psycopg v3)."""
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
    # Recycle pooled connections so they never go stale (portable PostgreSQL
    # builds can drop long-idle connections); sized for the multi-account
    # background sync + analysis tasks running alongside API requests.
    pool_recycle=1800,
    pool_size=10,
    max_overflow=20,
    # psycopg sends an SSLRequest by default, which crashes some portable
    # PostgreSQL builds on Windows. A local tool doesn't need TLS on the
    # loopback link, so disable it explicitly (sslmode in the URL is NOT
    # forwarded to psycopg by SQLAlchemy's dialect — it must go here).
    connect_args={"sslmode": "disable"},
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
        email_category,
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
        await conn.exec_driver_sql(
            "ALTER TABLE unified_emails "
            "ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE"
        )
        await conn.exec_driver_sql(
            "ALTER TABLE unified_emails "
            "ADD COLUMN IF NOT EXISTS is_starred BOOLEAN NOT NULL DEFAULT FALSE"
        )
        # Dynamic AI categories may exceed the original 32-char column.
        await conn.exec_driver_sql(
            "ALTER TABLE unified_emails "
            "ALTER COLUMN category TYPE VARCHAR(64)"
        )
        await _seed_categories(conn)


# Built-in categories seeded on first startup. `name` is the key stored on
# UnifiedEmail.category (kept in English for the legacy rule-based analyzer);
# `label` is what the UI shows. Users may delete any of them later.
_BUILTIN_CATEGORIES: list[tuple[str, str, str]] = [
    ("work", "工作", "#3b82f6"),
    ("meeting", "会议", "#06b6d4"),
    ("finance", "财务账单", "#10b981"),
    ("notification", "系统通知", "#a855f7"),
    ("social", "社交", "#ec4899"),
    ("travel", "旅行", "#f59e0b"),
    ("shopping", "购物", "#d97706"),
    ("marketing", "营销广告", "#ef4444"),
    ("newsletter", "订阅简报", "#6366f1"),
    ("personal", "个人", "#14b8a6"),
    ("other", "其他", "#6b7280"),
]


async def _seed_categories(conn) -> None:
    """Insert built-in categories if the table is empty (idempotent)."""
    # %s placeholders: psycopg uses client-side pyformat, unlike asyncpg's $1.
    for name, label, color in _BUILTIN_CATEGORIES:
        await conn.exec_driver_sql(
            "INSERT INTO email_categories (name, label, color, is_system, created_at) "
            "VALUES (%s, %s, %s, TRUE, NOW()) "
            "ON CONFLICT (name) DO NOTHING",
            (name, label, color),
        )
