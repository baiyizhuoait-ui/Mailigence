"""Unified mailbox view — lists mails across all accounts, protocol-agnostic."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete as db_delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.classification_feedback import ClassificationFeedback
from app.models.email import MailDirection, UnifiedEmail
from app.models.email_account import AuthType, EmailAccount
from app.schemas.email import (
    DraftReply,
    EmailListResponse,
    EmailOut,
    FullBodyOut,
    _clean_html_snippet,
)
from app.services.ai_config import load_ai_config
from app.services.memories import get_memory_texts
from app.services import reply_draft_service
from app.services.mail_sync import _extra_folders_for

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
    is_advertisement: bool | None = None,
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
    if is_advertisement is not None:
        # ``= true`` must also include legacy NULL rows only when False is asked
        # (NULL means "not analyzed yet", i.e. not an ad).
        cond = (
            UnifiedEmail.is_advertisement.is_(True)
            if is_advertisement
            else UnifiedEmail.is_advertisement.is_(False)
            | UnifiedEmail.is_advertisement.is_(None)
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
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


async def _fetch_full_body(db: AsyncSession, email: UnifiedEmail) -> dict:
    """Fetch the full ``{html, text}`` body, caching it locally on first read.

    A previously-fetched body (``body_html``/``body_text`` set) is served
    straight from the database; only the first view of a mail goes back to the
    source mailbox. Raises ``RuntimeError`` with a readable message when the
    body can't be fetched (unreachable server, deleted message, ...) so callers
    can degrade.
    """
    # Cache hit: a previous fetch stored the rendered body locally.
    if email.body_html is not None or email.body_text is not None:
        return {"html": email.body_html or "", "text": email.body_text or ""}

    account = await db.get(EmailAccount, email.account_id)
    if account is None:
        raise RuntimeError("Account not found")

    from app.services import mail_sync, ms_graph
    from app.services.imap_client import open_connection

    sent = email.direction == MailDirection.SENT
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
                client.fetch_full_body,
                email.message_id,
                sent=sent,
                # Auto-sorted/archived mail (netease 广告邮件, Gmail All Mail)
                # only lives in those folders — INBOX SEARCH alone misses it.
                extra_folders=[] if sent else _extra_folders_for(account.platform),
                # Some servers (QQ) silently fail HEADER searches — these
                # enable the date-window + local header-match fallback.
                from_addr=email.sender_email or "",
                received_at=email.received_at,
                subject=email.subject or "",
            )
        finally:
            await asyncio.to_thread(client.logout)

    if not body.get("html") and not body.get("text"):
        # Message no longer on the server (auto-cleaned junk, user deletion…)
        # — degrade to the stored snippet instead of an error so the detail
        # view still shows readable content.
        snippet = (email.body_snippet or "").strip()
        if snippet:
            result = {"html": "", "text": snippet}
        else:
            raise RuntimeError("Message body was not found on the server")
    else:
        result = {"html": body.get("html", ""), "text": body.get("text", "")}

    # Persist so re-opening the same mail is a local read next time.
    email.body_html = result["html"]
    email.body_text = result["text"]
    await db.commit()
    return result


@router.get("/{email_id}/full", response_model=FullBodyOut)
async def get_email_full_body(
    email_id: int, db: AsyncSession = Depends(get_db)
) -> FullBodyOut:
    """Fetch the full message body on demand from the source mailbox.

    The first view goes back to the provider (IMAP or Microsoft Graph) and the
    result is cached locally (``body_html``/``body_text``); later opens are a
    plain database read.
    """
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    try:
        body = await _fetch_full_body(db, email)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch the full message: {exc}",
        ) from exc
    return FullBodyOut(html=body["html"], text=body["text"])


@router.post("/{email_id}/draft-replies", response_model=list[DraftReply])
async def generate_draft_replies(
    email_id: int, db: AsyncSession = Depends(get_db)
) -> list[DraftReply]:
    """Generate 1-2 AI reply drafts for an email.

    Requires a configured LLM (rules mode → 400). Results are cached per
    (sender, subject, body, user-preferences) content hash, so re-requests
    with unchanged inputs return the stored drafts without another AI call.
    """
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")

    cfg = await load_ai_config(db)
    if not cfg.use_ai:
        raise HTTPException(
            status_code=400,
            detail="当前为规则模式（未配置 AI Key）。请在「设置 → AI 分析」中配置模型后使用回复草稿功能。",
        )

    # Prefer the full body; fall back to the stored snippet if the source
    # mailbox is unreachable so generation never hard-fails on the fetch.
    try:
        fetched = await _fetch_full_body(db, email)
        body_text = fetched["text"] or _clean_html_snippet(fetched["html"])
    except Exception:
        body_text = email.body_snippet
    body_text = (body_text or "")[: reply_draft_service.BODY_MAX_CHARS]

    memories = await get_memory_texts(db)
    cache_hash = hashlib.sha256(
        f"{email.sender}|{email.subject}|{body_text}|{json.dumps(memories, ensure_ascii=False)}".encode(
            "utf-8"
        )
    ).hexdigest()
    if email.cached_draft_replies and email.draft_cache_hash == cache_hash:
        return [DraftReply(**item) for item in email.cached_draft_replies]

    try:
        drafts = await reply_draft_service.generate_draft_replies(
            cfg, email.sender, email.subject, body_text, memories
        )
    except Exception as exc:
        # Surface a friendly 502 instead of a raw 500 with the provider's
        # error blob (e.g. local servers rejecting the model name).
        raise HTTPException(
            status_code=502,
            detail=reply_draft_service.friendly_llm_error(exc),
        ) from exc
    email.cached_draft_replies = drafts
    email.cached_at = datetime.now(timezone.utc)
    email.draft_cache_hash = cache_hash
    await db.commit()
    return [DraftReply(**item) for item in drafts]


class SendReplyRequest(BaseModel):
    content: str


@router.post("/{email_id}/send-reply")
async def send_reply(
    email_id: int, payload: SendReplyRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Send a reply to an email from inside the app (closes the AI-draft loop).

    App-password accounts go out via SMTP (our own Message-ID + In-Reply-To
    headers, plus a local SENT row so reply tracking matches instantly);
    Microsoft OAuth goes via Graph sendMail; Gmail OAuth is refused with a
    hint (plain login with an OAuth-derived credential is rejected by Google's
    SMTP endpoint). Either way the original mail is marked ``has_reply`` so it
    drops out of the pending-replies queue immediately.
    """
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="回复内容不能为空")

    to_addr = (email.sender_email or "").strip()
    if not to_addr:
        raise HTTPException(status_code=400, detail="原邮件没有可回复的发件人地址")

    account = await db.get(EmailAccount, email.account_id)
    if account is None:
        raise HTTPException(status_code=400, detail="找不到该邮件所属的邮箱账户")
    if account.auth_type not in (AuthType.APP_PASSWORD, AuthType.OAUTH_MICROSOFT):
        raise HTTPException(
            status_code=400,
            detail="Gmail OAuth 账户暂不支持应用内发送：请改用授权码方式添加账户，或到原邮箱发送。",
        )

    from app.services import smtp_sender
    from app.services.mail_sync import resolve_credential

    orig_subject = email.subject or "(无主题)"
    subject = (
        orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    )
    subject = " ".join(subject.split())  # IMAP keeps folded subjects with \r\n

    orig_mid = (email.message_id or "").strip().strip("<>")
    raw = email.raw_headers if isinstance(email.raw_headers, dict) else {}
    refs = (raw.get("References") or "").strip()
    if orig_mid and f"<{orig_mid}>" not in refs:
        refs = f"{refs} <{orig_mid}>" if refs else f"<{orig_mid}>"

    try:
        credential = await resolve_credential(account)
        if account.auth_type == AuthType.OAUTH_MICROSOFT:
            # Graph generates the Message-ID server-side, so we don't insert a
            # local SENT row (it would duplicate the synced copy); the reply
            # shows up on the next "sync sent" pass in the reply-tracking view.
            await smtp_sender.send_via_graph(credential, to_addr, subject, content)
            sent_id = None
        else:
            msg, mid = smtp_sender.build_reply_message(
                account.email, to_addr, subject, content, orig_mid, refs
            )
            await smtp_sender.send_via_smtp(account, credential, msg)
            sent_id = await _insert_sent_row(
                db, account, email, subject, content, mid, refs, orig_mid
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=smtp_sender.friendly_send_error(exc)
        ) from exc

    if not email.has_reply:
        email.has_reply = True
    # Replied mail is done — set handled_at at the source so the dashboard and
    # reply queue drop it without needing a write on every /pending read.
    if email.handled_at is None:
        email.handled_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "sent_id": sent_id}


async def _insert_sent_row(
    db: AsyncSession,
    account: EmailAccount,
    email: UnifiedEmail,
    subject: str,
    content: str,
    mid: str,
    refs: str,
    orig_mid: str,
) -> int:
    """Persist the outgoing reply as a SENT UnifiedEmail row.

    Carries the same Message-ID we put on the wire so the IMAP sent-folder
    sync later dedupes it against the server's copy; ``In-Reply-To`` lets
    reply tracking thread it under the original mail.
    """
    sent = UnifiedEmail(
        account_id=account.id,
        platform=account.platform,
        message_id=mid,
        thread_id=email.thread_id or "",
        direction=MailDirection.SENT,
        sender=account.display_name or account.email,
        sender_email=account.email,
        recipients=[email.sender_email],
        subject=subject,
        body_snippet=content[:500],
        received_at=datetime.now(timezone.utc),
        is_read=True,
        raw_headers={"Message-ID": mid, "In-Reply-To": orig_mid, "References": refs},
    )
    db.add(sent)
    await db.commit()
    await db.refresh(sent)
    return sent.id


@router.patch("/{email_id}/read", response_model=EmailOut)
async def mark_email_read(
    email_id: int,
    read: bool = True,
    db: AsyncSession = Depends(get_db),
) -> UnifiedEmail:
    """Set the read state (``?read=false`` marks the mail unread)."""
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    if email.is_read != read:
        email.is_read = read
        await db.commit()
        await db.refresh(email)
    return email


class ClassificationUpdate(BaseModel):
    """User correction of the AI classification (P1 feedback loop)."""

    category: str | None = Field(default=None, min_length=1, max_length=64)
    is_advertisement: bool | None = None


def _sender_email_key(sender: str | None) -> str:
    """Extract the bare address from ``张工 <zhang@x.com>`` / ``zhang@x.com``."""
    value = (sender or "").strip()
    if "<" in value and ">" in value:
        value = value.split("<", 1)[1].rsplit(">", 1)[0]
    return value.strip().lower()


@router.patch("/{email_id}/classification", response_model=EmailOut)
async def update_email_classification(
    email_id: int, payload: ClassificationUpdate, db: AsyncSession = Depends(get_db)
) -> UnifiedEmail:
    """Correct an email's AI classification and feed the learning loop.

    Writes the mail + a ``classification_feedback`` row (one per email, last
    correction wins), then re-queues this sender's other unprocessed mails for
    re-analysis so the correction propagates (the next analysis injects the
    recent corrections as few-shot context).
    """
    email = await db.get(UnifiedEmail, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    if payload.category is None and payload.is_advertisement is None:
        raise HTTPException(status_code=422, detail="category 或 is_advertisement 至少提供一个")

    # Capture the AI's original verdict BEFORE mutating, for the feedback row.
    was_category = email.category
    was_ad = email.is_advertisement
    changed = False
    if payload.category is not None and payload.category != email.category:
        email.category = payload.category.strip()
        changed = True
    if payload.is_advertisement is not None and payload.is_advertisement != email.is_advertisement:
        email.is_advertisement = payload.is_advertisement
        changed = True

    if not changed:
        return email

    # Upsert feedback: one row per email — the latest correction replaces any
    # earlier one for the same mail.
    await db.execute(
        db_delete(ClassificationFeedback).where(
            ClassificationFeedback.email_id == email.id
        )
    )
    db.add(
        ClassificationFeedback(
            email_id=email.id,
            sender=email.sender or "",
            sender_email=_sender_email_key(email.sender_email or email.sender),
            subject=email.subject or "",
            was_category=was_category,
            corrected_category=email.category,
            was_ad=was_ad,
            corrected_ad=email.is_advertisement,
        )
    )
    await db.commit()
    await db.refresh(email)

    # Targeted re-analysis: reset this sender's other unprocessed mails so
    # they re-enter the analysis queue and pick up the injected feedback.
    sender_key = _sender_email_key(email.sender_email or email.sender)
    if sender_key:
        requeue = (
            await db.execute(
                select(UnifiedEmail.id)
                .where(
                    UnifiedEmail.sender_email == sender_key,
                    UnifiedEmail.id != email.id,
                    UnifiedEmail.direction == MailDirection.INBOX,
                    UnifiedEmail.analyzed_at.is_not(None),
                    UnifiedEmail.handled_at.is_(None),
                    UnifiedEmail.is_archived.is_(False),
                )
                .limit(50)
            )
        ).scalars().all()
        if requeue:
            await db.execute(
                update(UnifiedEmail)
                .where(UnifiedEmail.id.in_(requeue))
                .values(analyzed_at=None)
            )
            await db.commit()
        from app.services.analysis_service import manager as analysis_manager

        analysis_manager.start(email.account_id)
    return email
