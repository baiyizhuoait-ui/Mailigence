"""Cross-mailbox email retrieval — three-way recall fused with RRF.

Two ranked recall routes are combined with Reciprocal Rank Fusion (RRF):

1. **keyword** — pg_trgm similarity + tsvector over ``subject``/``body_snippet``
   (good for concrete names, project IDs, Chinese phrases),
2. **semantic** — pgvector cosine similarity of the query embedding against
   the stored mail embeddings (good for "topic-related but no literal overlap"
   and cross-language queries).

Structured filters (account / category / date) constrain both routes. When no
embeddings endpoint is configured the semantic route is skipped silently and
search degrades to keyword-only — never an error.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.ai_config import AiConfig
from app.services.embedding_service import embed_text

_log = logging.getLogger(__name__)

RRF_K = 60  # RRF constant (larger = rank differences matter less)
KEYWORD_MATCH = "关键词"
SEMANTIC_MATCH = "语义"
# Semantic route returns up to this many nearest neighbours.
SEMANTIC_TOP = 20
# Extra keyword results fetched beyond the page, so RRF has a wider pool.
KEYWORD_OVERFETCH = 20
# pg_trgm threshold used inside this transaction (short CJK phrases score
# ~0.23 against long subjects — far below the 0.3 default).
TRIGRAM_THRESHOLD = 0.1


async def search_emails(
    db: AsyncSession,
    query: str,
    limit: int = 15,
    offset: int = 0,
    *,
    account_id: int | None = None,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    cfg: AiConfig | None = None,
) -> tuple[list[dict], int]:
    """Three-way recall + RRF fusion. Returns ``(results, total)``.

    Each result dict: ``id``, ``subject``, ``sender``, ``sender_email``,
    ``date`` (received_at), ``snippet`` (body_snippet), ``score`` and
    ``match`` (list of recall-route names that contributed).
    """
    query = (query or "").strip()
    if not query:
        return [], 0

    # Lower the trigram match threshold for this transaction only (auto-resets
    # when the request's transaction ends; value is a constant, so inlined).
    await db.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {TRIGRAM_THRESHOLD}"))

    filter_sql, filter_params = _filter_clause(account_id, category, date_from, date_to)
    candidates: dict[int, dict] = {}

    # ---- Route 1: keyword (trigram + tsvector) ----
    kw_params = {
        "q": query,
        "wr": settings.search_relevance_weight,
        "wp": settings.search_priority_weight,
        "wrec": settings.search_recency_weight,
        "n": offset + limit + KEYWORD_OVERFETCH,
        **filter_params,
    }
    kw_rows = (
        await db.execute(text(_KEYWORD_SQL.format(filters=filter_sql)), kw_params)
    ).mappings().all()
    for rank, row in enumerate(kw_rows, start=1):
        item = _upsert(candidates, row)
        item["match"].append(KEYWORD_MATCH)
        item["rrf"] += 1 / (RRF_K + rank)

    # ---- Route 2: semantic (pgvector) — optional ----
    if cfg is not None and cfg.embedding_enabled:
        try:
            from app.services.embedding_service import vector_available

            if not await vector_available(db):
                raise RuntimeError("pgvector extension not installed")
            qvec = await embed_text(cfg, query)
            vec = "[" + ",".join(f"{v:.6f}" for v in qvec) + "]"
            sem_params = {"v": vec, "n": SEMANTIC_TOP, **filter_params}
            sem_rows = (
                await db.execute(text(_SEMANTIC_SQL.format(filters=filter_sql)), sem_params)
            ).mappings().all()
            for rank, row in enumerate(sem_rows, start=1):
                item = _upsert(candidates, row)
                item["match"].append(SEMANTIC_MATCH)
                item["rrf"] += 1 / (RRF_K + rank)
        except Exception as exc:
            # Never break search because the embedder is unreachable or the
            # vector extension is missing — degrade to keyword-only.
            _log.warning("Semantic search unavailable, keyword-only: %s", exc)

    ordered = sorted(candidates.values(), key=lambda x: x["rrf"], reverse=True)
    total = len(ordered)
    page = ordered[offset : offset + limit]
    for item in page:
        item["score"] = round(item["rrf"], 6)
    return page, total


def _filter_clause(
    account_id: int | None,
    category: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[str, dict]:
    """Build the ``AND ...`` filter fragment plus its bind params."""
    parts: list[str] = []
    params: dict = {}
    if account_id is not None:
        parts.append(" AND account_id = :account_id")
        params["account_id"] = account_id
    if category:
        parts.append(" AND category = :category")
        params["category"] = category
    if date_from:
        parts.append(" AND received_at >= (:date_from)::date")
        params["date_from"] = date_from
    if date_to:
        parts.append(" AND received_at < (:date_to)::date + INTERVAL '1 day'")
        params["date_to"] = date_to
    return "".join(parts), params


def _upsert(candidates: dict[int, dict], row) -> dict:
    item = candidates.get(row["id"])
    if item is None:
        item = {
            "id": row["id"],
            "subject": row["subject"] or "",
            "sender": row["sender"] or "",
            "sender_email": row["sender_email"] or "",
            "account_id": row["account_id"],
            "category": row["category"],
            "priority_score": row["priority_score"],
            "date": row["received_at"],
            "snippet": row["body_snippet"] or "",
            "score": 0.0,
            "rrf": 0.0,
            "match": [],
        }
        candidates[row["id"]] = item
    return item


# Keyword route: weighted trigram/tsvector relevance + priority + recency.
_KEYWORD_SQL = """
    SELECT id, subject, sender, sender_email, account_id, category,
           priority_score, received_at, body_snippet,
           (greatest(
               greatest(similarity(coalesce(subject, ''), :q),
                        similarity(coalesce(body_snippet, ''), :q)),
               least(1.0, ts_rank(search_vector, plainto_tsquery('english', :q)))
            ) * :wr
            + coalesce(priority_score, 50) / 100.0 * :wp
            + (1.0 / (1.0 + (extract(epoch from (now() - coalesce(received_at, now()))) / 86400.0) / 30.0)) * :wrec
           ) AS score
    FROM unified_emails
    WHERE (subject % :q OR body_snippet % :q
           OR search_vector @@ plainto_tsquery('english', :q))
    {filters}
    ORDER BY score DESC
    LIMIT :n
"""

# Semantic route: nearest neighbours by cosine distance (<=>).
_SEMANTIC_SQL = """
    SELECT id, subject, sender, sender_email, account_id, category,
           priority_score, received_at, body_snippet
    FROM unified_emails
    WHERE embedding IS NOT NULL
    {filters}
    ORDER BY embedding <=> (:v)::vector
    LIMIT :n
"""
