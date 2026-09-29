"""Analysis orchestration: run AI analysis over emails that need it.

* ``run_analysis`` processes emails where ``analyzed_at IS NULL`` **or**
  ``category IS NULL``, calling the AI analyzer once per email and persisting
  the structured result. The second clause means mails left uncategorized by a
  category delete are automatically re-classified.
* Dedup cache: if a *previously analyzed* email from the same sender with the
  same subject exists, its analysis is reused — so bulk marketing mail with
  identical subjects doesn't re-invoke the LLM. OFF by default (AI mode means
  always calling the AI); enable with ``AI_DEDUP_CACHE=1``. Cache is only
  applied to never-analyzed mail; re-queued (previously analyzed) mail always
  gets a fresh classification.
* ``AnalysisManager`` runs the batch as an asyncio background task with an
  in-memory status dict the frontend can poll. Triggered automatically when an
  import completes, on category delete, by the periodic sweep in main.py, and
  manually via the analysis API.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.models.classification_feedback import ClassificationFeedback
from app.models.email import UnifiedEmail
from app.models.email_category import EmailCategory
from app.services.ai_analyzer import AnalysisResult, analyze_email
from app.services.ai_config import load_ai_config
from app.services.categories import list_category_names
from app.services.memories import get_memory_texts

# Max emails analyzed per background run. Large on purpose: the batch now
# calls the LLM in parallel (AI_CONCURRENCY) instead of serially, so even a
# few hundred pending mails drain in roughly one request-round-trip.
ANALYSIS_BATCH = 200
LOW_CONFIDENCE_THRESHOLD = 0.5  # surface these to the UI as "待确认" (P1-5)
# Below this the model is too unsure to invent a NEW category (falls back to
# other); reusing an existing one is still fine.
CATEGORY_INVENTION_THRESHOLD = 0.7


# --- core -------------------------------------------------------------------

async def run_analysis(
    db: AsyncSession,
    *,
    account_id: Optional[int] = None,
    limit: int = ANALYSIS_BATCH,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[int, int, int]:
    """Analyze up to ``limit`` emails that need it. Returns (analyzed, total,
    low_confidence).

    Two queues are covered:
    * never-analyzed mails (``analyzed_at IS NULL``) — new imports / syncs;
    * uncategorized mails (``category IS NULL``) — e.g. after the user deletes
      a category, those mails re-enter this queue automatically and the AI
      assigns them a fresh classification.
    ``low_confidence`` counts results whose self-reported confidence is below
    ``LOW_CONFIDENCE_THRESHOLD`` so the UI can badge them "待确认" (P1-5).
    """
    stmt = select(UnifiedEmail).where(
        (UnifiedEmail.analyzed_at.is_(None))
        | (UnifiedEmail.category.is_(None))
    )
    if account_id is not None:
        stmt = stmt.where(UnifiedEmail.account_id == account_id)
    stmt = stmt.order_by(
        UnifiedEmail.received_at.desc().nullslast(), UnifiedEmail.id.desc()
    ).limit(limit)

    emails = (await db.execute(stmt)).scalars().all()
    total = len(emails)
    analyzed = 0
    low_confidence = 0

    # Resolve AI config once per batch (DB settings + .env).
    cfg = await load_ai_config(db)
    # Existing category names — the LLM reuses them and only invents new ones
    # when nothing fits; new names are auto-registered below.
    category_names = await list_category_names(db)
    # User's distilled preferences (AI memory) injected into the LLM prompt.
    memories = await get_memory_texts(db)

    # Bulk-prefetch user corrections (P1) for every sender in the batch with
    # ONE query, so the parallel LLM phase below never touches the DB. Keep at
    # most 5 per sender (rows arrive newest-first).
    sender_emails = {e.sender_email for e in emails if e.sender_email}
    feedback_map: dict[str, list[dict]] = {}
    if sender_emails:
        rows = (
            await db.execute(
                select(ClassificationFeedback)
                .where(ClassificationFeedback.sender_email.in_(sender_emails))
                .order_by(
                    ClassificationFeedback.created_at.desc(),
                    ClassificationFeedback.id.desc(),
                )
            )
        ).scalars().all()
        for row in rows:
            bucket = feedback_map.setdefault(row.sender_email, [])
            if len(bucket) < 5:
                bucket.append({
                    "subject": row.subject or "",
                    "corrected_category": row.corrected_category,
                    "corrected_ad": row.corrected_ad,
                })

    # Optional dedup cache (cost control, off by default). Only applies to
    # never-analyzed mail without corrections; re-queued mails always get a
    # fresh LLM classification instead of copying the stale one.
    cached_map: dict[int, UnifiedEmail] = {}
    if settings.ai_dedup_cache:
        for email in emails:
            if email.analyzed_at is None and not feedback_map.get(email.sender_email):
                cached = await _find_cached(db, email)
                if cached is not None:
                    cached_map[email.id] = cached

    # --- parallel LLM phase (pure HTTP calls, no DB session usage) ----------
    results: dict[int, AnalysisResult] = {}
    failures: list[str] = []
    to_llm = [e for e in emails if e.id not in cached_map]
    sem = asyncio.Semaphore(max(1, settings.ai_concurrency))

    async def _one(email: UnifiedEmail) -> tuple[int, AnalysisResult]:
        feedback = feedback_map.get(email.sender_email) or None
        async with sem:
            r = await analyze_email(
                email.subject,
                email.body_snippet,
                email.raw_headers,
                config=cfg,
                categories=category_names,
                memories=memories,
                sender=email.sender or email.sender_email,
                feedback=feedback,
            )
        return email.id, r

    if to_llm:
        outcomes = await asyncio.gather(
            *(_one(e) for e in to_llm), return_exceptions=True
        )
        for email, out in zip(to_llm, outcomes):
            if isinstance(out, Exception):
                failures.append(f"{(email.subject or '')[:50]}: {out}")
            else:
                results[out[0]] = out[1]

    # --- sequential persist phase (DB writes + per-email progress) ----------
    for email in emails:
        cached = cached_map.get(email.id)
        if cached is not None:
            result = AnalysisResult(
                category=cached.category or "other",
                is_advertisement=bool(cached.is_advertisement),
                priority_score=cached.priority_score if cached.priority_score is not None else 50,
                summary=cached.summary or "",
                suggested_action=cached.suggested_action or "note",
            )
        else:
            result = results.get(email.id)
            if result is None:
                continue  # this email's LLM call failed; leave it unanalyzed
            is_low = (
                result.confidence is not None
                and result.confidence < LOW_CONFIDENCE_THRESHOLD
            )
            low_confidence += 1 if is_low else 0
            category_names = await _register_categories(
                db,
                category_names,
                [result.category],
                allow_new=result.confidence is None
                or result.confidence >= CATEGORY_INVENTION_THRESHOLD,
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

    if failures:
        # Surface AI failures (especially ai_only mode) instead of silently
        # leaving mail unanalyzed. Successful results are already persisted.
        raise RuntimeError(
            f"{len(failures)}/{total} analyses failed; first: {failures[0]}"
        )

    return analyzed, total, low_confidence


async def _register_categories(
    db: AsyncSession,
    known: list[str],
    names: list[str],
    *,
    allow_new: bool = True,
) -> list[str]:
    """Auto-register any category name the AI introduced that isn't known yet.

    Mutates ``known`` (returns it) so the batch keeps using the updated list.
    ``allow_new=False`` (low-confidence analysis) falls back to ``other``
    instead of polluting the category list with a guess — invented categories
    must be deliberate, keeping ``other`` rare-but-honest.
    """
    from sqlalchemy.exc import IntegrityError

    for name in names:
        name = (name or "").strip()
        if not name or name in known:
            continue
        if not allow_new:
            fallback = "other" if "other" in known else None
            if fallback is None:
                # ``other`` missing on this deployment — register nothing and
                # let the email keep the raw label rather than inventing one.
                continue
            name = fallback
            if name in known:
                continue
        known.append(name)
        db.add(EmailCategory(name=name, label=name, is_system=False))
        try:
            await db.flush()
        except IntegrityError:
            # A concurrent analysis run created it first — reuse the row.
            await db.rollback()
            existing = (
                await db.execute(
                    select(EmailCategory.name).where(EmailCategory.name == name)
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
    return known


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
    """Count emails waiting for analysis (unanalyzed or uncategorized)."""
    from sqlalchemy import func as _func

    stmt = select(_func.count(UnifiedEmail.id)).where(
        (UnifiedEmail.analyzed_at.is_(None))
        | (UnifiedEmail.category.is_(None))
    )
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
        # Serialize batch runs globally: the per-account (key>0) and all-account
        # (key=0) scans select from the same pending queue, so only ever let one
        # analysis task run at a time to avoid re-analyzing the same mails.
        for other in self._tasks.values():
            if not other.done():
                return False
        self._status[key] = {
            "running": True, "total": 0, "analyzed": 0, "low_confidence": 0, "error": ""
        }

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
                analyzed, total, low_confidence = await run_analysis(
                    db, account_id=account_id, limit=ANALYSIS_BATCH, on_progress=on_progress
                )
            # Fresh classifications change what the dashboard schedule shows;
            # drop its cache so the next poll reflects the new results instead
            # of serving the stale 60s-cached entry.
            from app.services.schedule_analyzer import invalidate_cache
            invalidate_cache()
            self._status[key] = {
                "running": False,
                "total": total,
                "analyzed": analyzed,
                "low_confidence": low_confidence,
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
