"""Universal IMAP client.

A single class that connects to *any* IMAP server (Gmail, Outlook, QQ, 163, …)
and returns mail normalised into our ``UnifiedEmail`` shape. The caller never
deals with protocol specifics — auth (app password OR OAuth2 XOAUTH2), search,
fetch, and RFC822 parsing are all encapsulated here.

Blocking ``imaplib`` calls are wrapped with ``asyncio.to_thread`` by callers
(``mail_sync.py``) so FastAPI's event loop stays responsive.

Privacy
-------
Only a ``body_snippet`` (truncated text) is extracted; the full raw body is not
returned to the caller and is discarded after parsing.
"""
from __future__ import annotations

import asyncio
import base64
import email
import email.utils
from email import policy as email_policy
import html as html_module
import imaplib
import logging
import re
import socket
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from typing import Iterable

from app.models.email import MailDirection
from app.models.email_account import AuthType

_log = logging.getLogger(__name__)

SNIPPET_MAX = 500  # chars persisted to body_snippet
FETCH_BATCH = 50   # messages per FETCH round-trip

# IMAP date formats require English month abbreviations (e.g. "07-Jul-2025").
# We must NOT use ``strftime("%b")`` / ``strptime("%b")`` because those depend
# on the system locale — on a Chinese-locale Windows/WSL host they emit
# "七月" instead of "Jul", which the IMAP server rejects, causing SEARCH
# SINCE to silently match the wrong (or no) messages and INTERNALDATE
# parsing to fail (received_at becomes NULL).
_MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_MONTH_LOOKUP = {name.lower(): i + 1 for i, name in enumerate(_MONTH_ABBR)}


def _decode_modified_utf7(s: str) -> str:
    """Decode an IMAP modified UTF-7 encoded string (RFC 3501 §5.1.3).

    NetEase (163/126/188) mailboxes expose folder names in modified UTF-7,
    e.g. ``&XfJT0ZAB-`` decodes to ``已发送``. Printable ASCII (0x20-0x7E)
    except ``&`` is represented as-is; ``&-`` is a literal ``&``; any other
    character is Base64-encoded UTF-16-BE wrapped in ``&...-``.
    """
    if not s or "&" not in s:
        return s
    result: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] != "&":
            result.append(s[i])
            i += 1
            continue
        # Find the terminating '-'.
        j = s.find("-", i + 1)
        if j == -1:
            # Malformed — no closing dash; emit the rest verbatim.
            result.append(s[i:])
            break
        if j == i + 1:
            # "&-" is an escaped literal '&'.
            result.append("&")
        else:
            b64 = s[i + 1:j]
            # Modified UTF-7 Base64 has no '=' padding; add it for b64decode.
            pad = (-len(b64)) % 4
            try:
                raw = base64.b64decode(b64 + "=" * pad)
                result.append(raw.decode("utf-16-be"))
            except Exception:
                # If decoding fails, keep the original segment so the name
                # is still usable (just not localised).
                result.append(s[i:j + 1])
        i = j + 1
    return "".join(result)


def _format_imap_date(d: date) -> str:
    """Format a date as ``DD-Mon-YYYY`` using fixed English month names."""
    return f"{d.day:02d}-{_MONTH_ABBR[d.month - 1]}-{d.year}"


def _decode(value: str | None) -> str:
    """Decode an RFC2047-encoded header value to a plain unicode string.

    Handles three scenarios for Chinese mail that lacks proper RFC 2047
    encoding in headers:
    1. Surrogate-escaped bytes (compat32 parser on raw UTF-8/GBK headers).
    2. Mojibake: UTF-8 bytes that were wrongly decoded as GBK by the
       ``default`` email policy when the mail declares ``charset=gbk``
       but the actual header bytes are UTF-8.
    3. Plain ASCII — returned as-is.
    """
    if not value:
        return ""
    # Step 1: Try RFC 2047 decoding (=?charset?B/Q?encoded?=).
    try:
        decoded = str(make_header(decode_header(value)))
        if decoded != value:
            return decoded
    except Exception:
        pass
    # Step 2: Recover surrogate-escaped raw bytes (compat32 behaviour).
    try:
        raw = value.encode("ascii", "surrogateescape")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("gbk", errors="replace")
    except Exception:
        pass
    # Step 3: Recover mojibake — UTF-8 bytes wrongly decoded as GBK.
    # This happens when email_policy.default uses the declared charset
    # (e.g. gbk) to decode headers that are actually UTF-8 encoded.
    try:
        return value.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    # Step 3b: Same but skip chars that can't be encoded in GBK (e.g. ▼).
    try:
        recovered = value.encode("gbk", errors="ignore").decode(
            "utf-8", errors="replace"
        )
        if recovered:
            return recovered
    except Exception:
        pass
    return value


def _extract_emails(value: str | None) -> list[str]:
    """Return the list of bare email addresses from a To/Cc/From header."""
    if not value:
        return []
    return [addr.lower() for _, addr in email.utils.getaddresses([value]) if addr]


def _html_to_text(html_str: str) -> str:
    """Convert HTML to clean plain text.

    Drops style/script blocks, strips all tags, decodes HTML entities, and
    normalises whitespace. Replaces the old ``" ".join(html.split())`` which
    left raw ``<!DOCTYPE>`` / ``<STYLE>`` tags visible in the snippet.
    """
    out = re.sub(r"<style[^>]*>.*?</style>", "", html_str, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"<script[^>]*>.*?</script>", "", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"<!--.*?-->", "", out, flags=re.DOTALL)
    out = re.sub(r"</?(?:p|div|br|tr|li|h[1-6])[^>]*>", "\n", out, flags=re.IGNORECASE)
    out = re.sub(r"<[^>]+>", "", out)
    out = html_module.unescape(out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _decode_bytes(payload: bytes, charset: str | None) -> str:
    """Decode bytes to text, trying UTF-8 first.

    Many Chinese mail servers (163/126/188) declare ``charset=gbk`` in
    Content-Type but actually send UTF-8 encoded content.  Always trying
    UTF-8 first avoids the mojibake that results from decoding UTF-8 bytes
    with GBK.
    """
    # Always try UTF-8 first — it's the most common encoding and correctly
    # handles the "declared gbk but actually utf-8" case.
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Fall back to the declared charset.
    if charset:
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, TypeError):
            pass
    # Last resort: GBK (common for Chinese mail).
    return payload.decode("gbk", errors="replace")


def _first_text_body(msg: Message, limit: int = SNIPPET_MAX * 4) -> str:
    """Best-effort extraction of a plain-text preview from a parsed message."""
    candidate = ""
    # Prefer text/plain
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                candidate = _decode_bytes(payload, part.get_content_charset())
                break
    # Fallback: strip tags from text/html
    if not candidate:
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html = _decode_bytes(payload, part.get_content_charset())
                    candidate = _html_to_text(html)
                    break
    text = " ".join(candidate.split())
    return text[:limit]


@dataclass
class NormalisedMail:
    """In-memory representation ready to be persisted as a UnifiedEmail row."""

    message_id: str
    thread_id: str
    sender: str
    sender_email: str
    recipients: list[str]
    subject: str
    body_snippet: str
    received_at: datetime | None
    is_read: bool
    direction: MailDirection = MailDirection.INBOX
    raw_headers: dict = field(default_factory=dict)


class ImapClient:
    """Stateful IMAP connection. Use as a context manager for one logical op."""

    def __init__(self, server: str, port: int = 993, timeout: float = 30.0):
        self._server = server
        self._port = port
        self._timeout = timeout
        self._conn: imaplib.IMAP4_SSL | None = None
        self._idle_tag: bytes | None = None

    # -- connection lifecycle -------------------------------------------------

    def __enter__(self) -> "ImapClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.logout()

    def connect(self) -> None:
        self._conn = imaplib.IMAP4_SSL(self._server, self._port, timeout=self._timeout)
        self._identify()

    def _identify(self) -> None:
        """Send IMAP ID (RFC 2971) before login.

        NetEase mailboxes (163/126/188/yeah) reject LOGIN with
        ``EXAMINE Unsafe Login. Please contact kefu@...`` unless the client
        identifies itself via the ID command first.

        We bypass ``imaplib._command`` because it auto-quotes any argument
        containing spaces or parens — that would wrap the ID parenthesized list
        in escaped double-quotes, corrupting it so the server ignores the ID
        and login still fails.
        """
        if self._conn is None:
            return
        try:
            tag = self._conn._new_tag()
            if isinstance(tag, str):
                tag = tag.encode("ascii")
            payload = b'("name" "Mailigence" "version" "1.0.0" "vendor" "Mailigence")'
            self._conn.send(tag + b" ID " + payload + b"\r\n")
            # Drain responses until our tagged reply arrives (servers may emit
            # an untagged `* ID (...)` line first).
            while True:
                resp = self._conn.readline()
                if not resp or resp[: len(tag)] == tag:
                    break
        except Exception:
            # Server doesn't support ID or rejected it — not fatal.
            pass

    def logout(self) -> None:
        if self._conn is not None:
            try:
                if self._conn.state == "SELECTED":
                    self._conn.close()
            except Exception:
                pass
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    # -- authentication -------------------------------------------------------

    def login_app_password(self, username: str, password: str) -> None:
        assert self._conn is not None
        typ, data = self._conn.login(username, password)
        if typ != "OK":
            raise RuntimeError(f"IMAP login failed: {data!r}")

    def login_oauth2(self, username: str, access_token: str) -> None:
        """Authenticate with XOAUTH2 (Gmail / Outlook OAuth2 over IMAP)."""
        assert self._conn is not None
        auth_string = f"user={username}\x01auth=Bearer {access_token}\x01\x01"

        def _challenge(_challenge: bytes) -> bytes:
            return auth_string.encode("utf-8")

        typ, data = self._conn.authenticate("XOAUTH2", _challenge)
        if typ != "OK":
            raise RuntimeError(f"XOAUTH2 authentication failed: {data!r}")

    # -- folder / search ------------------------------------------------------

    def select_folder(self, folder: str = "INBOX") -> int:
        assert self._conn is not None
        typ, data = self._conn.select(folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Cannot select folder {folder!r}: {data!r}")
        return int(data[0] or 0)

    def search_since(self, since: date) -> list[str]:
        """Return UIDs of messages received on/after ``since`` (IMAP SINCE)."""
        assert self._conn is not None
        date_str = _format_imap_date(since)
        _log.info("IMAP SEARCH SINCE %s", date_str)
        typ, data = self._conn.uid("SEARCH", "SINCE", date_str)
        if typ != "OK":
            raise RuntimeError(f"SEARCH failed: {data!r}")
        raw = data[0]
        if not raw:
            _log.warning("IMAP SEARCH SINCE %s returned 0 UIDs", date_str)
            return []
        uids = [u.decode() for u in raw.split()]
        _log.info("IMAP SEARCH SINCE %s returned %d UIDs", date_str, len(uids))
        return uids

    def search_all(self) -> list[str]:
        assert self._conn is not None
        typ, data = self._conn.uid("SEARCH", "ALL")
        if typ != "OK":
            raise RuntimeError(f"SEARCH failed: {data!r}")
        raw = data[0]
        if not raw:
            return []
        return [u.decode() for u in raw.split()]

    def list_folders(self) -> list[str]:
        """Return the list of selectable folder names exposed by the server.

        imaplib ``LIST`` returns entries like
        ``b'(\\HasNoChildren) "/" "Sent"'`` or ``b'(\\HasNoChildren) "INBOX"'``;
        the folder name is the last double-quoted segment (the delimiter, when
        present, precedes it). Flag markers such as ``\\Inbox`` are part of the
        parenthesized list and are discarded by only keeping the quoted name.

        Folder names are decoded from modified UTF-7 (RFC 3501 §5.1.3) so
        NetEase mailboxes return ``已发送`` instead of ``&XfJT0ZAB-``.
        """
        assert self._conn is not None
        typ, data = self._conn.list()
        if typ != "OK":
            return []
        folders: list[str] = []
        for item in data:
            if not isinstance(item, (bytes, bytearray)):
                continue
            text = bytes(item).decode("utf-8", errors="replace")
            quoted = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
            if not quoted:
                continue
            # Unescape backslash-escaped quotes/backslashes within the name.
            name = quoted[-1].replace('\\"', '"').replace("\\\\", "\\")
            name = _decode_modified_utf7(name)
            folders.append(name)
        return folders

    def select_sent_folder(self) -> str | None:
        """Locate and select the Sent folder; return its name or ``None``.

        Matching priority (first hit wins):
        1. exact ``"Sent"`` / ``"已发送"``
        2. name contains ``"Sent"`` (e.g. ``"Sent Messages"``, ``"Sent Items"``)
        3. name contains ``"已发送"``
        4. name contains ``"发件箱"``

        The match is done on the *decoded* (human-readable) name, but the
        folder is selected using the *original encoded* name from the server,
        because IMAP SELECT expects the modified UTF-7 form (e.g. 163 requires
        ``SELECT "&XfJT0ZAB-"`` rather than ``SELECT "已发送"``).
        """
        assert self._conn is not None
        typ, data = self._conn.list()
        if typ != "OK":
            return None

        # Build (decoded_name, raw_name) pairs so we can match on the decoded
        # form but SELECT using the raw form the server expects.
        pairs: list[tuple[str, str]] = []
        for item in data:
            if not isinstance(item, (bytes, bytearray)):
                continue
            text = bytes(item).decode("utf-8", errors="replace")
            quoted = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
            if not quoted:
                continue
            raw = quoted[-1].replace('\\"', '"').replace("\\\\", "\\")
            decoded = _decode_modified_utf7(raw)
            pairs.append((decoded, raw))

        def find(predicate) -> tuple[str, str] | None:
            for decoded, raw in pairs:
                if predicate(decoded):
                    return (decoded, raw)
            return None

        target = (
            find(lambda n: n in ("Sent", "已发送"))
            or find(lambda n: "Sent" in n)
            or find(lambda n: "已发送" in n)
            or find(lambda n: "发件箱" in n)
        )
        if target is None:
            return None
        # SELECT with the raw (modified UTF-7) name; return the decoded name.
        self.select_folder(target[1])
        return target[0]

    # -- fetch + parse --------------------------------------------------------

    def fetch_normalised(
        self, uids: Iterable[str], direction: MailDirection = MailDirection.INBOX
    ) -> list[NormalisedMail]:
        """Fetch a batch of messages by UID and parse into NormalisedMail.

        ``direction`` labels the resulting mails (INBOX by default; pass
        ``MailDirection.SENT`` when fetching from a Sent folder).
        """
        uid_list = [u for u in uids if u]
        if not uid_list or self._conn is None:
            return []
        results: list[NormalisedMail] = []
        for i in range(0, len(uid_list), FETCH_BATCH):
            batch = uid_list[i : i + FETCH_BATCH]
            uid_set = ",".join(batch)
            typ, data = self._conn.uid("FETCH", uid_set, "(RFC822 INTERNALDATE FLAGS)")
            if typ != "OK":
                raise RuntimeError(f"FETCH failed: {data!r}")
            results.extend(self._parse_fetch_response(data, direction))
        return results

    def fetch_message_ids(self, uids: Iterable[str]) -> dict[str, str]:
        """Fetch only the Message-ID header for a set of UIDs.

        Returns a ``{uid: message_id}`` dict. Uses ``BODY.PEEK[HEADER.FIELDS
        (MESSAGE-ID)]`` so messages are NOT marked as read and only ~100 bytes
        per message are transferred (vs. full RFC822 which can be megabytes).

        Used by the importer to skip already-imported messages: query the DB
        for existing message_ids, then only full-fetch UIDs whose Message-ID
        is absent.
        """
        uid_list = [u for u in uids if u]
        if not uid_list or self._conn is None:
            return {}
        result: dict[str, str] = {}
        for i in range(0, len(uid_list), FETCH_BATCH):
            batch = uid_list[i : i + FETCH_BATCH]
            uid_set = ",".join(batch)
            typ, data = self._conn.uid(
                "FETCH", uid_set, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"
            )
            if typ != "OK":
                _log.warning("fetch_message_ids FETCH failed: %r", data)
                continue
            for item in data:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                meta, body = item
                meta_str = (
                    meta.decode("utf-8", errors="replace")
                    if isinstance(meta, bytes)
                    else str(meta)
                )
                uid_match = re.search(r"UID\s+(\d+)", meta_str)
                if not uid_match:
                    continue
                uid = uid_match.group(1)
                # body looks like b'Message-ID: <xxx@yyy>\r\n\r\n'
                text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
                mid_match = re.search(r"Message-ID:\s*<([^>]+)>", text, re.IGNORECASE)
                if mid_match:
                    result[uid] = mid_match.group(1).strip()
                else:
                    # No Message-ID header — mark with synthetic prefix so the
                    # caller can decide to always fetch these.
                    result[uid] = f"synthetic:{uid}"
        return result

    # -- IDLE (RFC 2177) ------------------------------------------------------

    def has_idle_capability(self) -> bool:
        """Check whether the server advertises the IDLE extension."""
        assert self._conn is not None
        typ, data = self._conn.capability()
        if typ != "OK" or not data:
            return False
        caps = data[0].decode("utf-8", errors="replace") if isinstance(data[0], (bytes, bytearray)) else str(data[0])
        return "IDLE" in caps.upper()

    def start_idle(self) -> None:
        """Send the IDLE command and wait for the server's continuation ``+``.

        After this returns, the connection is in IDLE mode and the server will
        push untagged responses (``* N EXISTS``, ``* N RECENT``, …) as they
        occur. Call ``wait_idle_event`` to read them and ``stop_idle`` to exit.
        """
        assert self._conn is not None
        tag = self._conn._new_tag()
        if isinstance(tag, str):
            tag = tag.encode("ascii")
        self._conn.send(tag + b" IDLE\r\n")
        # Server responds with a continuation response "+ ..."
        resp = self._conn.readline()
        if not resp or not resp.startswith(b"+"):
            raise RuntimeError(f"IDLE not accepted by server: {resp!r}")
        self._idle_tag = tag

    def wait_idle_event(self, timeout: float = 60.0) -> str | None:
        """Block until an untagged response arrives or *timeout* seconds elapse.

        Returns the response line as a string, or ``None`` on timeout. A
        socket timeout is set on the underlying connection so we don't block
        forever — this allows the caller to periodically check a stop flag and
        refresh the IDLE (RFC 2177 recommends refreshing every ≤29 min).
        """
        assert self._conn is not None and self._conn.sock is not None
        old_timeout = self._conn.sock.gettimeout()
        self._conn.sock.settimeout(timeout)
        try:
            resp = self._conn.readline()
            if resp:
                return resp.decode("utf-8", errors="replace").strip()
            return None
        except (socket.timeout, TimeoutError, OSError):
            return None
        finally:
            try:
                self._conn.sock.settimeout(old_timeout)
            except Exception:
                pass

    def stop_idle(self) -> None:
        """Send ``DONE`` to terminate IDLE and drain the tagged OK response."""
        assert self._conn is not None
        if self._idle_tag is None:
            return
        try:
            self._conn.send(b"DONE\r\n")
            # Read lines until our tagged response arrives.
            while True:
                resp = self._conn.readline()
                if not resp:
                    break
                if resp.startswith(self._idle_tag):
                    break
        except Exception:
            pass
        finally:
            self._idle_tag = None

    # -- parsing --------------------------------------------------------------

    @staticmethod
    def _parse_fetch_response(
        data, direction: MailDirection = MailDirection.INBOX
    ) -> list[NormalisedMail]:
        """imaplib returns a list of alternating (meta, bytes) tuples + b')'."""
        mails: list[NormalisedMail] = []
        # data looks like: [ (b'1 (UID.. INTERNALDATE .. FLAGS (..))', b'<raw>'), b')', ... ]
        for item in data:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            meta, raw = item
            meta_str = meta.decode("utf-8", errors="replace") if isinstance(meta, bytes) else str(meta)
            if not isinstance(raw, (bytes, bytearray)):
                continue
            try:
                mails.append(ImapClient._parse_one(meta_str, bytes(raw), direction))
            except Exception:
                # Skip a single malformed message rather than failing the whole sync.
                continue
        return mails

    @staticmethod
    def _parse_one(
        meta_str: str, raw: bytes, direction: MailDirection = MailDirection.INBOX
    ) -> NormalisedMail:
        # Use compat32 policy so that header bytes are preserved as
        # surrogate-escaped strings rather than being decoded with the
        # (possibly wrong) charset declared in Content-Type.  Our _decode()
        # function then recovers the correct text by trying UTF-8, GBK,
        # and mojibake repair in sequence.
        msg = email.message_from_bytes(raw, policy=email_policy.compat32)

        def header(name: str) -> str:
            return _decode(msg.get(name, ""))

        message_id_raw = header("Message-ID").strip()
        message_id = message_id_raw.strip("<>").strip()
        if not message_id:
            # Some mail lacks Message-ID; synthesise a stable per-UID id so the
            # (account_id, message_id) uniqueness key still dedupes on re-sync.
            uid_match = re.search(r"UID\s+(\d+)", meta_str)
            message_id = f"synthetic:{uid_match.group(1)}" if uid_match else ""

        references_raw = header("References")
        in_reply_to = header("In-Reply-To").strip("<>").strip()
        ref_ids = [r.strip("<>").strip() for r in references_raw.split() if r.strip()] if references_raw else []
        thread_id = (ref_ids[0] if ref_ids else in_reply_to) or message_id

        from_header = header("From")
        recipients = _extract_emails(header("To")) + _extract_emails(header("Cc"))
        from_addrs = _extract_emails(from_header)
        sender_email = from_addrs[0] if from_addrs else ""

        # INTERNALDATE in meta looks like: "1 (UID 123 INTERNALDATE 06-Aug-2025 12:34:56 +0000 FLAGS (\Seen))"
        received_at = _parse_internaldate(meta_str)
        is_read = "\\Seen" in meta_str

        raw_headers = {
            "Message-ID": message_id_raw,
            "References": references_raw,
            "In-Reply-To": in_reply_to,
            "List-Unsubscribe": header("List-Unsubscribe"),
            "Precedence": header("Precedence"),
        }

        return NormalisedMail(
            message_id=message_id,
            thread_id=thread_id,
            sender=from_header,
            sender_email=sender_email,
            recipients=recipients,
            subject=header("Subject"),
            body_snippet=_first_text_body(msg)[:SNIPPET_MAX],
            received_at=received_at,
            is_read=is_read,
            direction=direction,
            raw_headers=raw_headers,
        )


def _parse_internaldate(meta_str: str) -> datetime | None:
    """Extract the INTERNALDATE from a FETCH metadata string.

    Some IMAP servers (notably NetEase 163/126) return INTERNALDATE *without*
    surrounding double-quotes, so we make the quotes optional here.

    The month abbreviation is resolved through a fixed English lookup instead
    of ``strptime("%b")`` because the latter is locale-dependent: on a Chinese
    locale host, parsing "06-Aug-2025" raises ValueError and received_at
    silently becomes NULL.
    """
    # Quotes are optional — some servers omit them. NetEase (163/126/188)
    # also pads single-digit days with a LEADING SPACE inside the quoted
    # value, e.g. ``INTERNALDATE " 1-Aug-2026 13:01:04 +0800"``. The
    # ``"?\s*`` below tolerates both the optional quote and that padding;
    # without it the regex fails to match and received_at silently becomes
    # NULL, hiding the newest mail from date-ordered views.
    m = re.search(
        r'INTERNALDATE\s+"?\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+'
        r'(\d{2}):(\d{2}):(\d{2})\s+([+-]\d{4})"?'
        r'(?:\s+FLAGS|\s*\)|\s*$)',
        meta_str,
    )
    if not m:
        _log.warning("Could not parse INTERNALDATE from meta: %s", meta_str[:200])
        return None
    day_s, mon_abbr, year_s, hh, mm, ss, tz = m.groups()
    month = _MONTH_LOOKUP.get(mon_abbr.lower())
    if month is None:
        _log.warning("Unknown month abbreviation in INTERNALDATE: %s", mon_abbr)
        return None
    sign = 1 if tz[0] == "+" else -1
    offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    try:
        return datetime(
            int(year_s), month, int(day_s),
            int(hh), int(mm), int(ss),
            tzinfo=timezone(offset),
        )
    except ValueError:
        _log.warning("INTERNALDATE value not in expected format: %s", m.group(0))
        return None


# --- async convenience wrapper ----------------------------------------------

async def open_connection(account, credential: str) -> ImapClient:
    """Create and authenticate an ImapClient for ``account``.

    ``credential`` is the *plaintext* secret (already decrypted by the caller):
    an app password for ``APP_PASSWORD`` accounts, or an OAuth2 access token for
    OAuth accounts. Blocking IMAP connect/login is offloaded to a thread.
    """
    client = ImapClient(account.imap_server, account.imap_port)
    await asyncio.to_thread(client.connect)
    try:
        if account.auth_type == AuthType.APP_PASSWORD:
            await asyncio.to_thread(client.login_app_password, account.email, credential)
        else:
            await asyncio.to_thread(client.login_oauth2, account.email, credential)
    except Exception:
        await asyncio.to_thread(client.logout)
        raise
    return client
