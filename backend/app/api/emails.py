"""Unified mailbox view — lists mails across all accounts, protocol-agnostic."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email import MailDirection, UnifiedEmail
from app.models.email_account import AuthType, EmailAccount
from app.schemas.email import EmailListResponse, EmailOut, FullBodyOut

router = APIRouter(prefix="/api/emails", tags=["emails"])

# Batch actions supported by POST /api/emails/batch
BATCH_ACTIONS = ("read", "unread", "archive", "unarchive", "star", "unstar", "delete")


class BatchActionRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: str


@router.get("", response_model=EmailListResponse)
async def list_emails(
    account_id: int | None = None,
    platform: str | None = None,
    category: str | None = None,
    q: str | None = None,
    unread_only: bool = False,
    starred_only: bool = False,
    archived: bool = False,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> EmailListResponse:
    """List unified emails, newest first. Filters compose.

    By default archived mails are hidden; pass ``archived=1`` to view them.
    """
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
    if starred_only:
        stmt = stmt.where(UnifiedEmail.is_starred.is_(True))
        count_stmt = count_stmt.where(UnifiedEmail.is_starred.is_(True))
    if archived:
        stmt = stmt.where(UnifiedEmail.is_archived.is_(True))
        count_stmt = count_stmt.where(UnifiedEmail.is_archived.is_(True))
    else:
        stmt = stmt.where(UnifiedEmail.is_archived.is_(False))
        count_stmt = count_stmt.where(UnifiedEmail.is_archived.is_(False))
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


@router.post("/batch", response_model=dict)
async def batch_action(
    payload: BatchActionRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Apply a bulk action (read/unread/archive/star/delete) to a set of mails."""
    if payload.action not in BATCH_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Invalid action: {payload.action}")

    if payload.action == "delete":
        # Physical delete keeps the mailbox clean.
        stmt = select(UnifiedEmail).where(UnifiedEmail.id.in_(payload.ids))
        rows = (await db.execute(stmt)).scalars().all()
        for row in rows:
            await db.delete(row)
        await db.commit()
        return {"updated": len(rows)}

    # Field-to-value map for the other actions.
    field_value = {
        "read": (UnifiedEmail.is_read, True),
        "unread": (UnifiedEmail.is_read, False),
        "archive": (UnifiedEmail.is_archived, True),
        "unarchive": (UnifiedEmail.is_archived, False),
        "star": (UnifiedEmail.is_starred, True),
        "unstar": (UnifiedEmail.is_starred, False),
    }
    col, value = field_value[payload.action]
    stmt = update(UnifiedEmail).where(UnifiedEmail.id.in_(payload.ids)).values({col: value})
    result = await db.execute(stmt)
    await db.commit()
    return {"updated": result.rowcount or 0}


@router.get("/{email_id}", response_model=EmailOut)
async def get_email(email_id: int, db: AsyncSession = Depends(get_db)) -> UnifiedEmail:
    """Fetch a single email's full metadata (including AI analysis fields)."""
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@router.get("/{email_id}/full", response_model=FullBodyOut)
async def get_email_full_body(
    email_id: int, db: AsyncSession = Depends(get_db)
) -> FullBodyOut:
    """Fetch the full message body on demand from the source mailbox.

    Only a snippet is persisted; this endpoint goes back to the provider
    (IMAP or Microsoft Graph) and returns the complete body so the user can
    read the mail without leaving the app.
    """
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    account = await db.get(EmailAccount, email.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    from app.services import mail_sync, ms_graph
    from app.services.imap_client import open_connection

    sent = email.direction == MailDirection.SENT
    try:
        if account.auth_type == AuthType.OAUTH_MICROSOFT:
            credential = await mail_sync.resolve_credential(account)
            body = await ms_graph.fetch_message_body(
                credential, email.message_id, sent=sent
            )
        else:
            credential = await mail_sync.resolve_credential(account)
            client = await open_connection(account, credential)
            try:
                body = await asyncio.to_thread(
                    client.fetch_full_body, email.message_id, sent=sent
                )
            finally:
                await asyncio.to_thread(client.logout)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch the full message: {exc}",
        ) from exc

    if not body.get("html") and not body.get("text"):
        raise HTTPException(
            status_code=404, detail="Message body was not found on the server"
        )
    return FullBodyOut(html=body.get("html", ""), text=body.get("text", ""))


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
