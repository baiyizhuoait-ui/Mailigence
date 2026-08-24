"""Async database engine and session factory (SQLAlchemy 2.0 + psycopg v3)."""
from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

_log = logging.getLogger(__name__)


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
    # gssencmode=disable suppresses the parallel GSSENCRequest probe for the
    # same reason (psycopg's default "prefer" still sends it).
    connect_args={"sslmode": "disable", "gssencmode": "disable"},
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
        ai_memory,
        ai_provider_profile,
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
        # User-customizable mailbox accent color (hex string, nullable).
        await conn.exec_driver_sql(
            "ALTER TABLE email_accounts "
            "ADD COLUMN IF NOT EXISTS color VARCHAR(16)"
        )
        # Semantic-search embedding model config (optional, empty -> auto/off).
        await conn.exec_driver_sql(
            "ALTER TABLE app_settings "
            "ADD COLUMN IF NOT EXISTS ai_embedding_model VARCHAR(120)"
        )
        # AI reply-draft cache (JSON drafts + invalidation hash + timestamp).
        await conn.exec_driver_sql(
            "ALTER TABLE unified_emails "
            "ADD COLUMN IF NOT EXISTS cached_draft_replies JSONB"
        )
        await conn.exec_driver_sql(
            "ALTER TABLE unified_emails "
            "ADD COLUMN IF NOT EXISTS cached_at TIMESTAMP WITH TIME ZONE"
        )
        await conn.exec_driver_sql(
            "ALTER TABLE unified_emails "
            "ADD COLUMN IF NOT EXISTS draft_cache_hash VARCHAR(64)"
        )
        # pg_trgm extension + trigram GIN indexes power the similarity-based
        # cross-mailbox search used by the AI chat QA endpoint.
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_emails_subject_trgm "
            "ON unified_emails USING gin (subject gin_trgm_ops)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_emails_snippet_trgm "
            "ON unified_emails USING gin (body_snippet gin_trgm_ops)"
        )
        # Full-text search: a generated tsvector over subject + body snippet,
        # maintained by PostgreSQL (english config boosts Latin-script ranking;
        # CJK queries are covered by the trigram indexes above).
        await conn.exec_driver_sql(
            "ALTER TABLE unified_emails "
            "ADD COLUMN IF NOT EXISTS search_vector tsvector "
            "GENERATED ALWAYS AS ("
            "to_tsvector('english', coalesce(subject,'') || ' ' || coalesce(body_snippet,''))"
            ") STORED"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_emails_search_vector "
            "ON unified_emails USING gin (search_vector)"
        )
        # Semantic search: pgvector extension + embedding column (dimension must
        # match the configured embedding model — default text-embedding-3-small
        # is 1536; for a different model (e.g. nomic-embed-text = 768 via
        # Ollama) set EMBEDDING_DIM and drop/recreate the column once).
        # The bundled portable PostgreSQL may not ship pgvector — in that case
        # degrade gracefully (semantic search auto-disables, keyword search
        # keeps working) instead of failing startup. A savepoint isolates the
        # failure so the remaining migrations / seeds still run.
        try:
            async with conn.begin_nested():
                await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
                await conn.exec_driver_sql(
                    f"ALTER TABLE unified_emails "
                    f"ADD COLUMN IF NOT EXISTS embedding vector({settings.embedding_dim})"
                )
                await conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_emails_embedding "
                    "ON unified_emails USING ivfflat (embedding vector_cosine_ops) "
                    "WITH (lists = 100)"
                )
        except Exception as exc:
            _log.warning(
                "pgvector not available — semantic search disabled (%s). "
                "Install the pgvector extension to enable it.",
                exc,
            )
        # Migrate the legacy single AI config (AppSetting row) into the new
        # ai_provider_profiles table as its first (active) record, so existing
        # setups keep working unchanged. No-op once profiles exist.
        await conn.exec_driver_sql(
            """
            INSERT INTO ai_provider_profiles
                (label, provider_type, base_url, api_key_encrypted, model,
                 is_active, created_at, updated_at)
            SELECT
                CASE WHEN app_settings.ai_provider = 'anthropic'
                     THEN 'Anthropic Claude' ELSE '我的 AI Provider' END,
                CASE WHEN app_settings.ai_provider = 'anthropic'
                     THEN 'anthropic' ELSE 'openai_compatible' END,
                app_settings.ai_base_url, app_settings.ai_api_key_encrypted,
                app_settings.ai_model, TRUE,
                COALESCE(app_settings.updated_at, NOW()),
                COALESCE(app_settings.updated_at, NOW())
            FROM app_settings
            WHERE NOT EXISTS (SELECT 1 FROM ai_provider_profiles)
              AND (app_settings.ai_base_url <> ''
                   OR app_settings.ai_model <> ''
                   OR app_settings.ai_api_key_encrypted <> '')
            """
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
