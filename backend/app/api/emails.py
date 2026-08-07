"""Unified mailbox view — lists mails across all accounts, protocol-agnostic."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email import UnifiedEmail
from app.schemas.email import EmailListResponse, EmailOut

router = APIRouter(prefix="/api/emails", tags=["emails"])


@router.get("", response_model=EmailListResponse)
async def list_emails(
    account_id: int | None = None,
    platform: str | None = None,
    category: str | None = None,
    q: str | None = None,
    unread_only: bool = False,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> EmailListResponse:
    """List unified emails, newest first. Filters compose."""
    stmt = select(UnifiedEmail)
    count_stmt = select(func.count(UnifiedEmail.id))

    if account_id is not None:
        stmt = stmt.where(UnifiedEmail.account_id == account_id)
        count_stmt = count_stmt.where(UnifiedEmail.account_id == account_id)
    if platform:
        stmt = stmt.where(UnifiedEmail.platform == platform)
        count_stmt = count_stmt.where(UnifiedEmail.platform == platform)
    if category:
        stmt = stmt.where(UnifiedEmail.category == category)
        count_stmt = count_stmt.where(UnifiedEmail.category == category)
    if unread_only:
        stmt = stmt.where(UnifiedEmail.is_read.is_(False))
        count_stmt = count_stmt.where(UnifiedEmail.is_read.is_(False))
    if q:
        pattern = f"%{q}%"
        cond = (
            UnifiedEmail.subject.ilike(pattern)
            | UnifiedEmail.sender.ilike(pattern)
            | UnifiedEmail.sender_email.ilike(pattern)
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    stmt = stmt.order_by(UnifiedEmail.received_at.desc().nullslast(), UnifiedEmail.id.desc())
    stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    items = [EmailOut.model_validate(row) for row in result.scalars().all()]
    total = (await db.execute(count_stmt)).scalar_one()
    return EmailListResponse(total=total, items=items)


@router.get("/{email_id}", response_model=EmailOut)
async def get_email(email_id: int, db: AsyncSession = Depends(get_db)) -> UnifiedEmail:
    """Fetch a single email's full metadata (including AI analysis fields)."""
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@router.patch("/{email_id}/read", response_model=EmailOut)
async def mark_email_read(email_id: int, db: AsyncSession = Depends(get_db)) -> UnifiedEmail:
    """Mark an email as read."""
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    if not email.is_read:
        email.is_read = True
        await db.commit()
        await db.refresh(email)
    return email
