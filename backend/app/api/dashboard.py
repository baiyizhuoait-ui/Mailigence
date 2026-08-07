"""Dashboard API — pending mail overview & AI schedule analysis.

This is the landing page's data source: it returns the emails that need
user action, an AI-generated priority queue, and a quick summary for
real-time polling.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email import MailDirection, UnifiedEmail
from app.models.email_account import EmailAccount
from app.schemas.email import EmailOut
from app.services import mail_sync
from app.services.schedule_analyzer import (
    analyze_schedule,
    auto_handle_emails,
    get_pending_emails,
    mark_handled,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.post("/sync")
async def sync_all_accounts(
    days: int = 1,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Sync all accounts for recent mail, then trigger AI analysis.

    This is the 'force refresh' endpoint: it pulls new mail from every
    configured account (default: last 1 day) so the dashboard reflects
    the latest inbox state without waiting for IDLE push.
    """
    result = await db.execute(select(EmailAccount))
    accounts = list(result.scalars().all())
    since = date.today() - timedelta(days=days)
    total_synced = 0
    errors: list[dict] = []

    for acct in accounts:
        try:
            count = await mail_sync.sync_account(db, acct, since=since)
            total_synced += count
        except Exception as exc:
            errors.append({"account_id": acct.id, "error": str(exc)[:200]})

    # Kick off AI analysis for any newly imported mail.
    if total_synced > 0:
        try:
            from app.services.analysis_service import manager as analysis_mgr
            for acct in accounts:
                analysis_mgr.start(acct.id)
        except Exception:
            pass  # analysis is best-effort, not blocking

    return {
        "synced": total_synced,
        "accounts": len(accounts),
        "errors": errors,
    }


@router.get("/pending", response_model=list[EmailOut])
async def dashboard_pending(
    account_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
) -> list[UnifiedEmail]:
    """Return unhandled emails needing action (reply/review), sorted by priority."""
    # Auto-handle stale emails first so they don't appear in the pending list.
    await auto_handle_emails(db)
    emails = await get_pending_emails(db, limit=30)
    if account_id is not None:
        emails = [e for e in emails if e.account_id == account_id]
    return emails


@router.get("/schedule")
async def dashboard_schedule(
    account_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """AI-analyzed schedule & priority queue for pending emails.

    Auto-handles stale emails first (replied, read+FYI, no-action),
    then returns the analysis. Cached for 3 minutes server-side.
    """
    result = await analyze_schedule(db, account_id)
    return {
        "schedule_items": result.schedule_items,
        "priority_queue": result.priority_queue,
        "daily_brief": result.daily_brief,
        "source": result.source,
    }


@router.post("/{email_id}/handle")
async def handle_email(
    email_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark an email as handled on the dashboard (dismiss from priority queue)."""
    success = await mark_handled(db, email_id)
    return {"handled": success, "email_id": email_id}


@router.post("/auto-handle")
async def auto_handle(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Auto-mark emails that no longer need dashboard attention as handled."""
    count = await auto_handle_emails(db)
    return {"auto_handled": count}


@router.get("/summary")
async def dashboard_summary(
    account_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Quick stats for the dashboard header — designed for frequent polling.

    All counts exclude handled emails (handled_at IS NULL).
    """
    base_cond = (
        UnifiedEmail.direction == MailDirection.INBOX,
        UnifiedEmail.handled_at.is_(None),
        UnifiedEmail.is_advertisement.is_(False)
        | (UnifiedEmail.is_advertisement.is_(None)),
    )
    if account_id is not None:
        base_cond = base_cond + (UnifiedEmail.account_id == account_id,)

    # Pending count (reply/review, not handled)
    pending_count = (
        await db.execute(
            select(func.count(UnifiedEmail.id)).where(
                *base_cond,
                UnifiedEmail.suggested_action.in_(["reply", "review"]),
            )
        )
    ).scalar_one()

    # Urgent (pending + high priority)
    urgent_count = (
        await db.execute(
            select(func.count(UnifiedEmail.id)).where(
                *base_cond,
                UnifiedEmail.suggested_action.in_(["reply", "review"]),
                UnifiedEmail.priority_score >= 70,
            )
        )
    ).scalar_one()

    # Unread (non-ad, not handled)
    unread_count = (
        await db.execute(
            select(func.count(UnifiedEmail.id)).where(
                *base_cond,
                UnifiedEmail.is_read.is_(False),
            )
        )
    ).scalar_one()

    # Last mail timestamp (for polling change detection)
    last_mail_at = (
        await db.execute(
            select(func.max(UnifiedEmail.received_at)).where(*base_cond)
        )
    ).scalar_one()

    # Today count
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (
        await db.execute(
            select(func.count(UnifiedEmail.id)).where(
                *base_cond,
                UnifiedEmail.received_at >= today_start,
            )
        )
    ).scalar_one()

    return {
        "pending_count": pending_count,
        "urgent_count": urgent_count,
        "unread_count": unread_count,
        "today_count": today_count,
        "last_mail_at": last_mail_at.isoformat() if last_mail_at else None,
    }
