"""Semantic embeddings (pgvector) for the mail retrieval layer.

Powers the vector recall channel: every stored email gets an ``embedding``
vector (subject + body snippet, ~1000 chars), and queries are embedded the same
way and matched with cosine distance. Combined with the trigram/tsvector
keyword channel via RRF in ``app.services.email_search``.

The channel is fully optional: when the AI config has no usable embeddings
endpoint (rules-only mode, anthropic provider, missing key/url) it is skipped
silently and search degrades to keyword-only — never an error.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.services.ai_config import AiConfig, load_ai_config

_log = logging.getLogger(__name__)

# Fallback model name for OpenAI-compatible / Ollama endpoints.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
# Subject + body text fed to the embedder. 2000 chars (P3) captures the
# meaningful body head instead of summary-level only; new/updated mails are
# embedded with the longer input (existing vectors refresh via re-sync).
EMBED_INPUT_MAX_CHARS = 2000
# Emails embedded per background run.
BACKFILL_BATCH = 20
# Network timeout for one embeddings call.
EMBED_TIMEOUT = 60

# Lazy cache of whether the DB has the pgvector extension (the bundled
# portable PostgreSQL may not ship it — then semantic search auto-disables).
_vector_available: bool | None = None


async def vector_available(db: AsyncSession) -> bool:
    """True when the ``vector`` extension is installed in the connected DB."""
    global _vector_available
    if _vector_available is None:
        try:
            result = await db.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            )
            _vector_available = result.scalar_one_or_none() is not None
        except Exception:
            _vector_available = False
    return _vector_available


def is_embedding_available(cfg: AiConfig) -> bool:
    """Shortcut for ``cfg.embedding_enabled`` (reads better at call sites)."""
    return cfg.embedding_enabled


def build_embed_input(subject: str | None, body: str | None) -> str:
    """Combine subject + body into the embeddable text (truncated)."""
    text_value = f"{subject or ''}\n{body or ''}".strip()
    return text_value[:EMBED_INPUT_MAX_CHARS]


def _vector_literal(values: list[float]) -> str:
    """Render a vector as pgvector's text literal (``'[0.1,0.2,...]'``)."""
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


async def embed_text(cfg: AiConfig, text_value: str) -> list[float]:
    """Embed ``text_value`` via the configured OpenAI-compatible endpoint.

    Raises on failure so callers decide whether to degrade (search) or log
    and skip (background backfill).
    """
    model = cfg.effective_embedding_model
    if not model:
        raise RuntimeError(
            "未配置 embedding 模型：当前 provider 未显式设置语义搜索模型，"
            "且不是 OpenAI 官方端点，无法自动选用默认模型。"
        )
    url = cfg.base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    body = {"model": model, "input": text_value}
    async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Embedding HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    try:
        return [float(x) for x in data["data"][0]["embedding"]]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("Unexpected embeddings response shape") from exc


# --- background backfill ----------------------------------------------------

async def pending_embedding_count(db: AsyncSession) -> int:
    if not await vector_available(db):
        return 0
    result = await db.execute(
        text("SELECT count(*) FROM unified_emails WHERE embedding IS NULL")
    )
    return int(result.scalar_one())


async def run_embedding_backfill(db: AsyncSession, cfg: AiConfig, limit: int = BACKFILL_BATCH) -> tuple[int, int]:
    """Embed up to ``limit`` emails that lack a vector. Returns (embedded, remaining).

    Uses raw SQL so the pgvector column stays out of the ORM model (no extra
    python dependency) — vectors are passed as text literals cast to ``vector``.
    """
    if not cfg.embedding_enabled or not await vector_available(db):
        return 0, await pending_embedding_count(db)

    rows = (
        await db.execute(
            text(
                "SELECT id, subject, body_snippet FROM unified_emails "
                "WHERE embedding IS NULL ORDER BY id DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).mappings().all()

    embedded = 0
    for row in rows:
        vector = await embed_text(cfg, build_embed_input(row["subject"], row["body_snippet"]))
        await db.execute(
            text("UPDATE unified_emails SET embedding = (:vec)::vector WHERE id = :id"),
            {"vec": _vector_literal(vector), "id": row["id"]},
        )
        embedded += 1
    await db.commit()

    remaining = await pending_embedding_count(db)
    if embedded:
        _log.info("Embedded %d emails (%d still pending)", embedded, remaining)
    return embedded, remaining


class EmbeddingManager:
    """One background task at a time; kicks off the backfill sweep."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> bool:
        if self._task is not None and not self._task.done():
            return False  # already running
        self._task = asyncio.create_task(self._run(), name="embedding-backfill")
        return True

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        try:
            async with SessionLocal() as db:
                cfg = await load_ai_config(db)
                await run_embedding_backfill(db, cfg)
        except Exception as exc:
            # A failed batch must never crash the sweep loop.
            _log.warning("Embedding backfill failed: %s", exc)


manager = EmbeddingManager()
