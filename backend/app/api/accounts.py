"""Email account management: test, create, list, delete, sync."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email_account import EmailAccount
from app.schemas.email_account import (
    AccountCreateRequest,
    AccountOut,
    SyncResult,
    TestConnectionRequest,
)
from app.services import crypto, mail_sync

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.post("/test", summary="Validate IMAP credentials before saving")
async def test_connection(req: TestConnectionRequest) -> dict:
    try:
        await mail_sync.test_connection(
            auth_type=req.auth_type,
            platform=req.platform,
            email_addr=str(req.email),
            imap_server=req.imap_server,  # type: ignore[arg-type]
            imap_port=req.imap_port,
            credential=req.credential or "",
        )
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connection test failed: {exc}",
        )


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(req: AccountCreateRequest, db: AsyncSession = Depends(get_db)) -> EmailAccount:
    # Reject duplicates per (platform, email)
    existing = await db.execute(
        select(EmailAccount).where(EmailAccount.email == str(req.email), EmailAccount.platform == req.platform)
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Account already exists")

    # Verify the credential actually works before persisting (fail fast).
    try:
        await mail_sync.test_connection(
            auth_type=req.auth_type,
            platform=req.platform,
            email_addr=str(req.email),
            imap_server=req.imap_server,  # type: ignore[arg-type]
            imap_port=req.imap_port,
            credential=req.credential,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Connection test failed: {exc}")

    account = EmailAccount(
        platform=req.platform,
        email=str(req.email),
        auth_type=req.auth_type,
        credential_secret=crypto.encrypt(req.credential),
        imap_server=req.imap_server,  # type: ignore[arg-type]
        imap_port=req.imap_port,
        smtp_server=req.smtp_server or "",
        smtp_port=req.smtp_port,
        display_name=req.display_name or "",
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@router.get("", response_model=list[AccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db)) -> list[EmailAccount]:
    return await mail_sync.list_accounts(db)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)) -> None:
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    await db.delete(account)
    await db.commit()


@router.post("/{account_id}/sync", response_model=SyncResult)
async def sync_account(
    account_id: int,
    days: int = 7,
    limit: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> SyncResult:
    from datetime import date, timedelta

    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        count = await mail_sync.sync_account(
            db, account, since=date.today() - timedelta(days=days), limit=limit
        )
        return SyncResult(account_id=account_id, synced=count, status=account.sync_status)
    except Exception as exc:
        return SyncResult(
            account_id=account_id, synced=0, status=account.sync_status, error=str(exc)
        )
