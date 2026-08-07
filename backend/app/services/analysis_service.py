"""Analysis orchestration: run AI analysis over unanalyzed emails.

* ``run_analysis`` processes emails where ``analyzed_at IS NULL``, calling the
  AI analyzer once per email and persisting the structured result.
* Dedup cache: if a *previously analyzed* email from the same sender with the
  same subject exists, its analysis is reused — so bulk marketing mail with
  identical subjects doesn't re-invoke the LLM (cost control per spec).
* ``AnalysisManager`` runs the batch as an asyncio background task with an
  in-memory status dict the frontend can poll. Triggered automatically when an
  import completes, and manually via the analysis API.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.email import UnifiedEmail
from app.services.ai_analyzer import AnalysisResult, analyze_email
from app.services.ai_config import load_ai_config

ANALYSIS_BATCH = 50  # max emails analyzed per background run


# --- core -------------------------------------------------------------------

async def run_analysis(
    db: AsyncSession,
    *,
    account_id: Optional[int] = None,
    limit: int = ANALYSIS_BATCH,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[int, int]:
    """Analyze up to ``limit`` unanalyzed emails. Returns (analyzed, total)."""
    stmt = select(UnifiedEmail).where(UnifiedEmail.analyzed_at.is_(None))
    if account_id is not None:
        stmt = stmt.where(UnifiedEmail.account_id == account_id)
    stmt = stmt.order_by(
        UnifiedEmail.received_at.desc().nullslast(), UnifiedEmail.id.desc()
    ).limit(limit)

    emails = (await db.execute(stmt)).scalars().all()
    total = len(emails)
    analyzed = 0

    # Resolve AI config once per batch (DB settings + .env).
    cfg = await load_ai_config(db)

    for email in emails:
        cached = await _find_cached(db, email)
        if cached is not None:
            result = AnalysisResult(
                category=cached.category or "其他",
                is_advertisement=bool(cached.is_advertisement),
                priority_score=cached.priority_score if cached.priority_score is not None else 50,
                summary=cached.summary or "",
                suggested_action=cached.suggested_action or "仅需知晓",
            )
        else:
            result = await analyze_email(
                email.subject, email.body_snippet, email.raw_headers, config=cfg
            )

        email.category = result.category
        email.is_advertisement = result.is_advertisement
        email.priority_score = result.priority_score
        email.summary = result.summary
        email.suggested_action = result.suggested_action
        email.analyzed_at = datetime.now(timezone.utc)
        await db.commit()

        analyzed += 1
        if on_progress:
            on_progress(analyzed, total)

    return analyzed, total


async def _find_cached(db: AsyncSession, email: UnifiedEmail) -> UnifiedEmail | None:
    """Return a previously-analyzed email with same sender + subject, if any."""
    if not email.sender_email or not email.subject:
        return None
    result = await db.execute(
        select(UnifiedEmail)
        .where(
            UnifiedEmail.analyzed_at.is_not(None),
            UnifiedEmail.sender_email == email.sender_email,
            func.lower(UnifiedEmail.subject) == email.subject.lower(),
            UnifiedEmail.id != email.id,
        )
        .order_by(UnifiedEmail.analyzed_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def pending_count(db: AsyncSession, account_id: Optional[int] = None) -> int:
    from sqlalchemy import func as _func

    stmt = select(_func.count(UnifiedEmail.id)).where(UnifiedEmail.analyzed_at.is_(None))
    if account_id is not None:
        stmt = stmt.where(UnifiedEmail.account_id == account_id)
    return (await db.execute(stmt)).scalar_one()


# --- background manager -----------------------------------------------------

StatusKey = int  # account_id, or 0 for "all accounts"


class AnalysisManager:
    def __init__(self) -> None:
        self._tasks: dict[StatusKey, asyncio.Task] = {}
        self._status: dict[StatusKey, dict] = {}

    def start(self, account_id: Optional[int]) -> bool:
        key = account_id or 0
        task = self._tasks.get(key)
        if task is not None and not task.done():
            return False  # already running
        self._status[key] = {"running": True, "total": 0, "analyzed": 0, "error": ""}

        def on_progress(analyzed: int, total: int) -> None:
            st = self._status.get(key)
            if st is not None:
                st["total"] = total
                st["analyzed"] = analyzed

        task = asyncio.create_task(self._run(account_id, on_progress), name=f"analysis-{key}")
        self._tasks[key] = task
        task.add_done_callback(lambda _t, k=key: self._tasks.pop(k, None))
        return True

    def is_running(self, account_id: Optional[int]) -> bool:
        key = account_id or 0
        task = self._tasks.get(key)
        return task is not None and not task.done()

    def get_status(self, account_id: Optional[int]) -> dict:
        key = account_id or 0
        return self._status.get(
            key, {"running": False, "total": 0, "analyzed": 0, "error": ""}
        )

    async def _run(self, account_id: Optional[int], on_progress) -> None:
        key = account_id or 0
        try:
            async with SessionLocal() as db:
                analyzed, total = await run_analysis(
                    db, account_id=account_id, limit=ANALYSIS_BATCH, on_progress=on_progress
                )
            self._status[key] = {
                "running": False,
                "total": total,
                "analyzed": analyzed,
                "error": "",
            }
        except Exception as exc:
            self._status[key] = {
                "running": False,
                "total": 0,
                "analyzed": 0,
                "error": str(exc)[:200],
            }


manager = AnalysisManager()
