"""Pydantic schemas for the unified mailbox view."""
from __future__ import annotations

import html as html_module
import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, field_validator

from app.models.email import MailDirection

_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCK_RE = re.compile(r"</?(?:p|div|br|tr|li|h[1-6])[^>]*>", re.IGNORECASE)


def _try_fix_mojibake(text: str) -> str:
    """Try to recover UTF-8 text that was wrongly decoded as GBK.

    This happens with legacy data imported before the IMAP charset fix on
    Windows. The round-trip encode(GBK) → decode(UTF-8) only succeeds when
    the text is actually mojibake, so correct text is left untouched.
    """
    if not text:
        return text
    try:
        return text.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _clean_html_snippet(text: str) -> str:
    """Strip HTML tags/entities from a snippet. Handles legacy rows stored
    before the IMAP client learned to strip tags (so old data still renders
    as readable text instead of ``<!DOCTYPE html>...``).
    """
    if not text or "<" not in text:
        return text
    out = _STYLE_RE.sub("", text)
    out = _SCRIPT_RE.sub("", out)
    out = _COMMENT_RE.sub("", out)
    out = _BLOCK_RE.sub("\n", out)
    out = _TAG_RE.sub("", out)
    out = html_module.unescape(out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


class EmailOut(BaseModel):
    id: int
    account_id: int
    platform: str
    thread_id: str
    direction: MailDirection
    sender: str
    sender_email: str
    recipients: list[Any]
    subject: str
    body_snippet: str
    received_at: Optional[datetime] = None
    is_read: bool
    has_reply: bool
    # AI fields (null until Stage 3)
    category: Optional[str] = None
    is_advertisement: Optional[bool] = None
    priority_score: Optional[int] = None
    summary: Optional[str] = None
    suggested_action: Optional[str] = None
    analyzed_at: Optional[datetime] = None

    @field_validator("subject", "sender", mode="before")
    @classmethod
    def _fix_text_mojibake(cls, v: Any) -> Any:
        """Recover mojibake in text fields (legacy data)."""
        if isinstance(v, str) and v:
            return _try_fix_mojibake(v)
        return v

    @field_validator("body_snippet", "summary", mode="before")
    @classmethod
    def _clean_snippet(cls, v: Any) -> str:
        """Fix mojibake first, then strip HTML — order matters because
        mojibaked HTML like ``<!doctype html>...`` needs both steps."""
        if not v:
            return v
        text = _try_fix_mojibake(str(v))
        return _clean_html_snippet(text)

    model_config = {"from_attributes": True}


class EmailListResponse(BaseModel):
    total: int
    items: list[EmailOut]
