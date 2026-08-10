"""Microsoft Graph client — reads Outlook mail via the Graph REST API.

Outlook OAuth accounts no longer speak IMAP (the consent scope drops
``IMAP.AccessAsUser.All``); instead we fetch the same mail through
``GET https://graph.microsoft.com/v1.0/me/messages`` and normalise it into the
shared ``NormalisedMail`` shape so the rest of the pipeline (upsert, AI
analysis, reports) is driver-agnostic.

Only *read* operations are implemented here (list inbox/sent, user profile).
Both endpoints paginate via ``@odata.nextLink`` and return full message
objects in the list, so no separate body-fetch step is needed.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import AsyncIterator

import httpx

from app.models.email import MailDirection
from app.services.imap_client import NormalisedMail, SNIPPET_MAX, _html_to_text

GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"

# Fields needed by the pipeline. internetMessageHeaders carries the raw
# Message-ID / In-Reply-To / References / List-Unsubscribe used for threading,
# reply tracking and ad detection.
MESSAGE_SELECT = (
    "id,conversationId,subject,from,toRecipients,ccRecipients,"
    "bodyPreview,receivedDateTime,isRead,internetMessageId,"
    "internetMessageHeaders,hasAttachments"
)

# Headers we keep for reply tracking / ad detection (mirrors imap_client).
_HEADER_FIELDS = {
    "Message-ID",
    "References",
    "In-Reply-To",
    "List-Unsubscribe",
    "Precedence",
}


async def _graph_get(access_token: str, url: str) -> dict:
    """GET a Graph endpoint, translating auth/scope failures into clear errors."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 401:
        raise RuntimeError(
            "Microsoft access token is invalid or expired — re-authorize the account"
        )
    if resp.status_code == 403:
        raise RuntimeError(
            "Microsoft account lacks Mail.Read permission. Delete and re-add the "
            "account in the UI to re-consent with the new permissions."
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Microsoft Graph HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _first_page_url(*, since: date | datetime | None, sent: bool) -> str:
    path = "/me/mailFolders/sentitems/messages" if sent else "/me/messages"
    params = [
        "$top=50",
        "$count=true",
        f"$select={MESSAGE_SELECT}",
        "$orderby=receivedDateTime desc",
    ]
    if since is not None:
        if isinstance(since, datetime):
            since_iso = since.astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            since_iso = f"{since.isoformat()}T00:00:00Z"
        params.append(f"$filter=receivedDateTime ge {since_iso}")
    return f"{GRAPH_ENDPOINT}{path}?{'&'.join(params)}"


async def iter_messages(
    access_token: str,
    *,
    since: date | datetime | None = None,
    sent: bool = False,
    max_pages: int = 10,
) -> AsyncIterator[tuple[list[dict], int | None]]:
    """Yield ``(page, total_count)`` for the inbox or Sent folder (newest first).

    ``total_count`` comes from ``@odata.count`` and is only populated on the
    first page (used by the importer to size its progress bar); subsequent
    pages carry the same value. ``max_pages`` bounds one pass (50 msgs/page →
    500 default for a background sync; the historical importer passes a larger
    cap).
    """
    url: str | None = _first_page_url(since=since, sent=sent)
    total: int | None = None
    for _ in range(max_pages):
        if not url:
            break
        data = await _graph_get(access_token, url)
        if total is None:
            total = data.get("@odata.count")
        yield data.get("value") or [], total
        url = data.get("@odata.nextLink")


async def list_messages(
    access_token: str,
    *,
    since: date | datetime | None = None,
    sent: bool = False,
    max_pages: int = 10,
) -> list[dict]:
    """Collect all pages into one flat list (convenience wrapper)."""
    out: list[dict] = []
    async for page, _total in iter_messages(access_token, since=since, sent=sent, max_pages=max_pages):
        out.extend(page)
    return out


async def fetch_user_profile(access_token: str) -> dict:
    """Return the signed-in user's profile (used for connection tests)."""
    return await _graph_get(access_token, f"{GRAPH_ENDPOINT}/me")


async def fetch_message_body(
    access_token: str, message_id: str, *, sent: bool = False
) -> dict[str, str]:
    """Fetch one message's full body by its InternetMessageId.

    Returns ``{"html": ..., "text": ...}``. Graph filters on
    ``internetMessageId`` with an ``eq`` expression; we try both the
    ``<...>`` and bare forms because providers store it inconsistently.
    """
    from urllib.parse import quote

    mid = message_id.strip().strip("<>")
    folder = "/me/mailFolders/sentitems/messages" if sent else "/me/messages"
    for value in (f"<{mid}>", mid):
        url = (
            f"{GRAPH_ENDPOINT}{folder}?$top=1"
            f"&$filter=internetMessageId eq '{quote(value)}'"
            "&$select=id,body"
        )
        data = await _graph_get(access_token, url)
        items = data.get("value") or []
        if not items:
            continue
        body = items[0].get("body") or {}
        content = body.get("content") or ""
        if (body.get("contentType") or "").lower() == "html":
            return {"html": content, "text": _html_to_text(content)}
        return {"html": "", "text": content}
    raise RuntimeError("Message not found in mailbox")


# --- normalisation -----------------------------------------------------------

def _extract_headers(header_list: list[dict]) -> dict:
    out: dict = {}
    for h in header_list or []:
        name = (h.get("name") or "").strip()
        if name in _HEADER_FIELDS:
            out[name] = (h.get("value") or "").strip()
    return out


def normalize_message(msg: dict, direction: MailDirection = MailDirection.INBOX) -> NormalisedMail:
    """Map a Graph message object onto the shared ``NormalisedMail`` shape."""
    frm = (msg.get("from") or {}).get("emailAddress") or {}

    def _addresses(key: str) -> list[str]:
        return [
            r.get("emailAddress", {}).get("address", "")
            for r in (msg.get(key) or [])
            if r.get("emailAddress", {}).get("address")
        ]

    message_id = (msg.get("internetMessageId") or "").strip().strip("<>").strip()
    if not message_id:
        message_id = f"synthetic:{msg.get('id')}"

    received_at: datetime | None = None
    received_raw = msg.get("receivedDateTime")
    if received_raw:
        try:
            received_at = datetime.fromisoformat(received_raw.replace("Z", "+00:00"))
        except ValueError:
            received_at = None

    return NormalisedMail(
        message_id=message_id,
        thread_id=(msg.get("conversationId") or "").strip() or message_id,
        sender=(frm.get("name") or ""),
        sender_email=(frm.get("address") or ""),
        recipients=_addresses("toRecipients") + _addresses("ccRecipients"),
        subject=(msg.get("subject") or ""),
        body_snippet=(msg.get("bodyPreview") or "")[:SNIPPET_MAX],
        received_at=received_at,
        is_read=bool(msg.get("isRead")),
        direction=direction,
        raw_headers=_extract_headers(msg.get("internetMessageHeaders")),
    )
