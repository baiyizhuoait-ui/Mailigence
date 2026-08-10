"""Sent-reply tracking endpoints.

Synchronises the IMAP "Sent" folder into ``UnifiedEmail`` rows tagged with
``direction=SENT`` and marks INBOX mails as ``has_reply=True`` when a sent mail
references them via the ``In-Reply-To`` header. Also exposes the resulting
"awaiting reply" list and conversation-thread views.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email import MailDirection, UnifiedEmail
from app.models.email_account import EmailAccount
from app.schemas.email import EmailOut
from app.services.imap_client import ImapClient, MailDirection
from app.services.mail_sync import open_connection, resolve_credential, upsert_mails

router = APIRouter(prefix="/api/replies", tags=["replies"])


async def _update_has_reply(db: AsyncSession, account_id: int) -> int:
    """Mark INBOX emails as has_reply=True if a SENT email references them.

    For each SENT email, read raw_headers["In-Reply-To"], find the INBOX
    email with matching message_id, and set has_reply=True.
    Returns the number of emails updated.
    """
    # 获取所有已发送邮件（有 In-Reply-To 头的）
    sent_emails = (await db.execute(
        select(UnifiedEmail).where(
            UnifiedEmail.account_id == account_id,
            UnifiedEmail.direction == MailDirection.SENT,
        )
    )).scalars().all()

    matched = 0
    for sent in sent_emails:
        in_reply_to = ""
        if isinstance(sent.raw_headers, dict):
            in_reply_to = sent.raw_headers.get("In-Reply-To", "") or ""
        in_reply_to = in_reply_to.strip().strip("<>").strip()
        if not in_reply_to:
            continue
        # 找到对应的收件箱邮件
        inbox_email = (await db.execute(
            select(UnifiedEmail).where(
                UnifiedEmail.account_id == account_id,
                UnifiedEmail.direction == MailDirection.INBOX,
                UnifiedEmail.message_id == in_reply_to,
            )
        )).scalars().first()
        if inbox_email and not inbox_email.has_reply:
            inbox_email.has_reply = True
            matched += 1
    if matched:
        await db.commit()
    return matched


@router.post("/{account_id}/sync-sent")
async def sync_sent(
    account_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    """同步已发送邮件并更新 has_reply。

    1. 获取 account，resolve_credential，open_connection
    2. client.select_sent_folder()，如果返回 None，返回未找到已发送文件夹
    3. search_since(最近30天) 获取 UIDs
    4. fetch_normalised(uids, direction=MailDirection.SENT) 获取已发送邮件
    5. upsert_mails(db, account, sent_mails) 存入数据库
    6. 执行 thread 匹配，更新 has_reply
    7. 返回 {"imported": len(sent_mails), "matched": matched_count}
    """
    account = await db.get(EmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    since = date.today() - timedelta(days=30)

    # Microsoft OAuth accounts sync their Sent folder through the Graph API;
    # everything else uses IMAP.
    if account.auth_type == "oauth_microsoft":
        return await _sync_sent_graph(db, account, since)

    client: ImapClient | None = None
    try:
        credential = await resolve_credential(account)
        client = await open_connection(account, credential)

        sent_folder = await asyncio.to_thread(client.select_sent_folder)
        if sent_folder is None:
            return {"imported": 0, "matched": 0, "message": "未找到已发送文件夹"}

        uids = await asyncio.to_thread(client.search_since, since)
        if not uids:
            await upsert_mails(db, account, [])
            matched = await _update_has_reply(db, account_id)
            return {"imported": 0, "matched": matched}

        # Skip already-imported sent messages.
        uid_to_mid = await asyncio.to_thread(client.fetch_message_ids, uids)
        existing_result = await db.execute(
            select(UnifiedEmail.message_id).where(
                UnifiedEmail.account_id == account_id
            )
        )
        existing_mids = {r[0] for r in existing_result.all() if r[0]}
        new_uids = [
            u
            for u in uids
            if uid_to_mid.get(u, f"synthetic:{u}") not in existing_mids
        ]

        sent_mails = await asyncio.to_thread(
            client.fetch_normalised, new_uids, MailDirection.SENT
        ) if new_uids else []
        await upsert_mails(db, account, sent_mails)
        matched = await _update_has_reply(db, account_id)
        return {"imported": len(sent_mails), "matched": matched}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if client is not None:
            await asyncio.to_thread(client.logout)


async def _sync_sent_graph(
    db: AsyncSession, account: EmailAccount, since: date
) -> dict:
    """Sync the Sent folder of a Microsoft OAuth account via Graph."""
    from app.services import ms_graph

    credential = await resolve_credential(account)
    messages = await ms_graph.list_messages(credential, since=since, sent=True)
    sent_mails = [
        ms_graph.normalize_message(m, MailDirection.SENT) for m in messages
    ]
    await upsert_mails(db, account, sent_mails)
    matched = await _update_has_reply(db, account.id)
    return {"imported": len(sent_mails), "matched": matched}


@router.get("/pending", response_model=list[EmailOut])
async def list_pending_replies(
    account_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[UnifiedEmail]:
    """返回待回复邮件列表。

    Auto-handles stale mail first, then queries
    direction=INBOX AND suggested_action="reply" AND has_reply=false
    AND handled_at IS NULL，按 priority_score DESC, received_at DESC 排序。
    """
    # Keep the reply queue consistent with the dashboard: dismiss stale
    # (read, old) and already-handled mail so it doesn't linger forever.
    from app.services.schedule_analyzer import auto_handle_emails
    await auto_handle_emails(db)

    stmt = select(UnifiedEmail).where(
        UnifiedEmail.direction == MailDirection.INBOX,
        UnifiedEmail.suggested_action == "reply",
        UnifiedEmail.has_reply == False,  # noqa: E712
        UnifiedEmail.handled_at.is_(None),
    )
    if account_id is not None:
        stmt = stmt.where(UnifiedEmail.account_id == account_id)
    stmt = stmt.order_by(
        UnifiedEmail.priority_score.desc().nulls_last(),
        UnifiedEmail.received_at.desc(),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/threads/{email_id}", response_model=list[EmailOut])
async def get_email_thread(
    email_id: int, db: AsyncSession = Depends(get_db)
) -> list[UnifiedEmail]:
    """返回该邮件的会话线程（相关邮件列表）。

    取指定 email 的 thread_id，查询同一 thread_id 的所有邮件，按
    received_at ASC 排序。email 不存在返回 404。
    """
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")

    stmt = (
        select(UnifiedEmail)
        .where(UnifiedEmail.thread_id == email.thread_id)
        .order_by(UnifiedEmail.received_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
