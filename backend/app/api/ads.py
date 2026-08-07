"""Ad-mail detection, batch operations & blocked-sender management.

Two routers live here because the paths split across two prefixes:
``/api/ads`` for ad stats / batch / block-by-email / unsubscribe, and
``/api/blocked-senders`` for the blocked-sender CRUD. Both are registered
from ``app.main``.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.blocked_sender import BlockedSender
from app.models.email import UnifiedEmail
from app.schemas.blocked_sender import (
    AdStatsOut,
    BatchAdActionRequest,
    BatchAdActionResult,
    BlockSenderRequest,
    BlockedSenderOut,
    UnsubscribeInfo,
)

router = APIRouter(prefix="/api/ads", tags=["ads"])
blocked_senders_router = APIRouter(prefix="/api/blocked-senders", tags=["ads"])


def _parse_unsubscribe(header_value: str) -> tuple[Optional[str], Optional[str]]:
    """Extract (url, mailto) from a List-Unsubscribe header value.

    Handles both angle-bracketed form (``<https://...>``, ``<mailto:...>``)
    and bare URLs. Returns (None, None) when nothing is found.
    """
    if not header_value:
        return None, None
    angle = re.findall(r"<([^>]+)>", header_value)
    url: Optional[str] = None
    mailto: Optional[str] = None
    for item in angle:
        if item.lower().startswith("http"):
            url = item
        elif item.lower().startswith("mailto:"):
            mailto = item
    if not url:
        m = re.search(r"https?://[^\s,]+", header_value)
        if m:
            url = m.group(0)
    return url, mailto


# ---------------------------------------------------------------------------
# Blocked-sender management
# ---------------------------------------------------------------------------


@blocked_senders_router.get("", response_model=list[BlockedSenderOut])
async def list_blocked_senders(
    account_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
) -> list[BlockedSender]:
    """Return all blocked-sender records, optionally filtered by account."""
    stmt = select(BlockedSender).order_by(
        BlockedSender.created_at.desc(), BlockedSender.id.desc()
    )
    if account_id is not None:
        stmt = stmt.where(BlockedSender.account_id == account_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@blocked_senders_router.post(
    "",
    response_model=BlockedSenderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_blocked_sender(
    req: BlockSenderRequest, db: AsyncSession = Depends(get_db)
) -> BlockedSender:
    """Add a blocked sender. ``account_id=None`` means a global block.

    Returns 409 if the (account_id, sender_email) pair already exists.
    """
    stmt = select(BlockedSender).where(BlockedSender.sender_email == req.sender_email)
    if req.account_id is None:
        stmt = stmt.where(BlockedSender.account_id.is_(None))
    else:
        stmt = stmt.where(BlockedSender.account_id == req.account_id)
    existing = await db.execute(stmt)
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Sender already blocked")

    blocked = BlockedSender(
        account_id=req.account_id,
        sender_email=req.sender_email,
        sender_name=req.sender_name,
        reason=req.reason,
    )
    db.add(blocked)
    await db.commit()
    await db.refresh(blocked)
    return blocked


@blocked_senders_router.delete(
    "/{sender_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def delete_blocked_sender(
    sender_id: int, db: AsyncSession = Depends(get_db)
) -> None:
    """Remove a blocked-sender record."""
    blocked = await db.get(BlockedSender, sender_id)
    if not blocked:
        raise HTTPException(status_code=404, detail="Blocked sender not found")
    await db.delete(blocked)
    await db.commit()


# ---------------------------------------------------------------------------
# Ad-mail stats & batch operations
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=AdStatsOut)
async def ad_stats(db: AsyncSession = Depends(get_db)) -> AdStatsOut:
    """Aggregate counts of ad mail and blocked senders."""
    total_ads = (
        await db.execute(
            select(func.count(UnifiedEmail.id)).where(
                UnifiedEmail.is_advertisement.is_(True)
            )
        )
    ).scalar_one()

    blocked_senders = (
        await db.execute(select(func.count(BlockedSender.id)))
    ).scalar_one()

    category_rows = await db.execute(
        select(UnifiedEmail.category, func.count(UnifiedEmail.id))
        .where(UnifiedEmail.is_advertisement.is_(True))
        .group_by(UnifiedEmail.category)
    )
    ads_by_category: dict[str, int] = {}
    for category, count in category_rows.all():
        key = category if category is not None else "uncategorized"
        ads_by_category[key] = count

    return AdStatsOut(
        total_ads=total_ads,
        blocked_senders=blocked_senders,
        ads_by_category=ads_by_category,
    )


@router.post("/batch", response_model=BatchAdActionResult)
async def batch_ad_action(
    req: BatchAdActionRequest, db: AsyncSession = Depends(get_db)
) -> BatchAdActionResult:
    """Batch delete or mark-read on ad mail.

    When ``email_ids`` is empty the action applies to every mail with
    ``is_advertisement=true``; otherwise it applies to the given ids.
    Returns the number of affected rows.
    """
    if req.email_ids:
        cond = UnifiedEmail.id.in_(req.email_ids)
    else:
        cond = UnifiedEmail.is_advertisement.is_(True)

    if req.action == "delete":
        stmt = delete(UnifiedEmail).where(cond)
    else:  # "mark_read"
        stmt = update(UnifiedEmail).where(cond).values(is_read=True)

    result = await db.execute(stmt)
    affected = result.rowcount or 0
    await db.commit()
    return BatchAdActionResult(affected=affected, action=req.action)


@router.post(
    "/{email_id}/block",
    response_model=BlockedSenderOut,
    status_code=status.HTTP_201_CREATED,
)
async def block_sender_by_email(
    email_id: int, db: AsyncSession = Depends(get_db)
) -> BlockedSender:
    """Block the sender of a given email and mark all their mail as read."""
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")

    sender_email = email.sender_email or ""
    if not sender_email:
        raise HTTPException(
            status_code=400, detail="Email has no sender_email to block"
        )

    stmt = select(BlockedSender).where(BlockedSender.sender_email == sender_email)
    if email.account_id is None:
        stmt = stmt.where(BlockedSender.account_id.is_(None))
    else:
        stmt = stmt.where(BlockedSender.account_id == email.account_id)
    existing = await db.execute(stmt)
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Sender already blocked")

    blocked = BlockedSender(
        account_id=email.account_id,
        sender_email=sender_email,
        sender_name=email.sender or "",
        reason="advertisement",
    )
    db.add(blocked)

    # Mark every mail from this sender as read in the same transaction.
    await db.execute(
        update(UnifiedEmail)
        .where(UnifiedEmail.sender_email == sender_email)
        .values(is_read=True)
    )

    await db.commit()
    await db.refresh(blocked)
    return blocked


@router.get("/{email_id}/unsubscribe", response_model=UnsubscribeInfo)
async def get_unsubscribe_info(
    email_id: int, db: AsyncSession = Depends(get_db)
) -> UnsubscribeInfo:
    """Extract List-Unsubscribe URL/mailto from a mail's raw headers."""
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")

    raw_headers = email.raw_headers
    header_value = ""
    if isinstance(raw_headers, dict):
        header_value = raw_headers.get("List-Unsubscribe", "") or ""

    url, mailto = _parse_unsubscribe(header_value)
    return UnsubscribeInfo(
        email_id=email_id,
        has_unsubscribe=bool(url or mailto),
        url=url,
        mailto=mailto,
    )
