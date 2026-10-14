"""Outgoing mail: SMTP for app-password accounts, Graph for Microsoft OAuth.

Completes the AI-draft loop — drafts used to be copy-to-clipboard only; the
user can now send the reply from inside the app. SMTP replies carry our own
Message-ID plus ``In-Reply-To``/``References`` headers so the sent copy stays
threaded with the original mail: the local SENT row inserted by the API is
deduplicated by the later IMAP sent-folder sync (same account_id +
message_id), and reply tracking matches the original via ``In-Reply-To``.

Gmail OAuth accounts are not supported here: Google's SMTP endpoint rejects
plain login with OAuth-derived credentials (it would need its own XOAUTH2
flow), so the API layer refuses them with a clear message instead.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from app.models.email_account import EmailAccount

_log = logging.getLogger(__name__)

SMTP_TIMEOUT = 30


def _clean_header(value: str) -> str:
    """Collapse folded-header whitespace (IMAP stores References etc. with
    embedded ``\\r\\n``) — EmailMessage refuses raw linefeeds in header values."""
    return " ".join(value.split())


def build_reply_message(
    from_addr: str,
    to_addr: str,
    subject: str,
    body_text: str,
    in_reply_to: str = "",
    references: str = "",
) -> tuple[EmailMessage, str]:
    """Build the RFC822 reply message.

    Returns ``(message, message_id)`` — the Message-ID is generated here so the
    caller can persist a SENT row the future sent-folder sync will dedupe on.
    """
    msg = EmailMessage()
    msg["From"] = _clean_header(from_addr)
    msg["To"] = _clean_header(to_addr)
    msg["Subject"] = _clean_header(subject)
    mid = make_msgid(domain=from_addr.rsplit("@", 1)[-1])[1:-1]
    msg["Message-ID"] = f"<{mid}>"
    if in_reply_to:
        msg["In-Reply-To"] = f"<{_clean_header(in_reply_to).strip('<>')}>"  # fmt: skip
    if references:
        msg["References"] = _clean_header(references)
    msg.set_content(body_text)
    return msg, mid


def _smtp_send_sync(
    host: str, port: int, user: str, password: str, msg: EmailMessage
) -> None:
    """Blocking SMTP session; runs in a worker thread via ``send_via_smtp``."""
    if port == 465:  # implicit TLS (Gmail / QQ / NetEase presets)
        with smtplib.SMTP_SSL(
            host, port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context()
        ) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:  # STARTTLS (Outlook preset uses 587)
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)


async def send_via_smtp(
    account: EmailAccount, credential: str, msg: EmailMessage
) -> None:
    """Send ``msg`` through the account's SMTP server (thread-offloaded)."""
    host = account.smtp_server
    if not host:
        raise RuntimeError("该账户未配置 SMTP 服务器，无法应用内发送")
    await asyncio.to_thread(
        _smtp_send_sync, host, account.smtp_port or 465, account.email, credential, msg
    )


async def send_via_graph(
    credential: str, to_addr: str, subject: str, body_text: str
) -> None:
    """Send via Microsoft Graph ``/me/sendMail`` (credential = access token)."""
    from app.services import ms_graph  # lazy import keeps module load light

    await ms_graph.send_mail(credential, to_addr, subject, body_text)


def friendly_send_error(exc: Exception) -> str:
    """Translate raw SMTP/Graph failures into actionable Chinese messages."""
    msg = str(exc)
    low = msg.lower()
    if any(k in low for k in ("535", "534", "530", "auth", "login", "username and password")):
        return "SMTP 认证失败：请确认使用的是授权码（应用专用密码），而非邮箱登录密码。"
    if any(k in low for k in ("getaddrinfo", "name or service not known", "timed out", "timeout")):
        return "无法连接 SMTP 服务器，请检查网络或该账户的 SMTP 配置。"
    return f"发送失败：{msg[:200]}"
