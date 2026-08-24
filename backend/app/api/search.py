"""Full-text mailbox search.

GET /api/search retrieves emails through the shared three-way retrieval layer
(``app.services.email_search``): keyword (pg_trgm + tsvector) and semantic
(pgvector) routes fused with Reciprocal Rank Fusion, both constrained by the
structured filters (account / category / date range). When no embeddings
endpoint is configured, it degrades to keyword-only automatically.

Snippets are highlighted in Python (not ``ts_headline``) so trigram-only and
semantic-only matches are highlighted too.
"""
from __future__ import annotations

import html
import re
from datetime import date as date_type
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import email_search
from app.services.ai_config import load_ai_config

router = APIRouter(prefix="/api/search", tags=["search"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
# Characters of context kept on each side of a keyword hit.
HIGHLIGHT_RADIUS = 60
# Windows to include in a highlighted snippet (first N merged spans).
MAX_HIGHLIGHT_WINDOWS = 2


class SearchResultOut(BaseModel):
    id: int
    subject: str
    sender: str
    account_id: int
    category: str | None = None
    priority_score: int | None = None
    received_at: Any = None
    snippet_html: str = ""
    # Which recall routes contributed: ["关键词"] and/or ["语义"].
    match: list[str] = []


class SearchResponse(BaseModel):
    total: int
    results: list[SearchResultOut]


def _validate_date(value: str | None, name: str) -> str | None:
    if not value:
        return None
    try:
        date_type.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} 必须是 ISO 日期（YYYY-MM-DD）")
    return value


def _match_spans(text: str, query: str) -> list[tuple[int, int]]:
    """Case-insensitive (start, end) spans of every query token in ``text``."""
    tokens = [tok for tok in re.split(r"\s+", (query or "").strip()) if tok]
    if not tokens:
        return []
    lower = text.lower()
    spans: list[tuple[int, int]] = []
    for tok in tokens:
        tok_lower = tok.lower()
        start = 0
        while True:
            idx = lower.find(tok_lower, start)
            if idx == -1:
                break
            spans.append((idx, idx + len(tok)))
            start = idx + len(tok)
    spans.sort()
    return spans


def _merge_spans(spans: list[tuple[int, int]], radius: int) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] <= radius:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def highlight_snippet(subject: str, body: str, query: str, radius: int = HIGHLIGHT_RADIUS) -> str:
    """Build an HTML snippet (±``radius`` chars) with keyword hits in <mark>.

    All non-mark text is HTML-escaped — the only tags emitted are the <mark>
    wrappers we add, so the output is safe to render with innerHTML.
    """
    source = (body or subject or "").strip()
    if not source:
        return ""
    spans = _merge_spans(_match_spans(source, query), radius)
    if not spans:
        # No literal hit (e.g. semantic-only match) — show the head.
        head = html.escape(source[: radius * 3])
        return head + ("…" if len(source) > radius * 3 else "")

    windows = []
    for start, end in spans[:MAX_HIGHLIGHT_WINDOWS]:
        ws, we = max(0, start - radius), min(len(source), end + radius)
        windows.append((ws, we, [(s, e) for s, e in spans if ws <= s < we]))

    parts = []
    for ws, we, active in windows:
        seg = source[ws:we]
        out: list[str] = []
        pos = 0
        for s, e in active:
            cs, ce = max(s, ws), min(e, we)
            out.append(html.escape(seg[pos : cs - ws]))
            out.append("<mark>" + html.escape(seg[cs - ws : ce - ws]) + "</mark>")
            pos = ce - ws
        out.append(html.escape(seg[pos:]))
        rendered = "".join(out)
        if ws > 0:
            rendered = "…" + rendered
        if we < len(source):
            rendered = rendered + "…"
        parts.append(rendered)
    return "…".join(parts)


@router.get("", response_model=SearchResponse)
async def search_emails(
    q: str = Query(..., description="Search keywords (required)"),
    account_id: int | None = None,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    date_from = _validate_date(date_from, "date_from")
    date_to = _validate_date(date_to, "date_to")

    cfg = await load_ai_config(db)
    results, total = await email_search.search_emails(
        db,
        q,
        limit=limit,
        offset=offset,
        account_id=account_id,
        category=category or None,
        date_from=date_from,
        date_to=date_to,
        cfg=cfg,
    )

    return SearchResponse(
        total=total,
        results=[
            SearchResultOut(
                id=item["id"],
                subject=item["subject"],
                sender=item["sender"],
                account_id=item["account_id"],
                category=item["category"],
                priority_score=item["priority_score"],
                received_at=item["date"],
                snippet_html=highlight_snippet(item["subject"], item["snippet"], q),
                match=item["match"],
            )
            for item in results
        ],
    )
