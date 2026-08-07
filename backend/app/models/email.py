"""UnifiedEmail model — the single schema that hides protocol/platform differences.

Every mail fetched from any provider (Gmail, Outlook, QQ, 163, ...) is normalised
into this table, so the UI and AI pipeline never need to know the source protocol.

Design notes
------------
* ``message_id`` is the RFC822 Message-ID; together with ``account_id`` it forms a
  natural unique key so re-syncing is idempotent.
* ``thread_id`` is derived from the ``References`` / ``In-Reply-To`` headers (root
  Message-ID of the conversation) — a protocol-agnostic threading strategy that
  also powers reply tracking in Stage 5.
* ``raw_headers`` stores only the headers useful downstream (List-Unsubscribe,
  Precedence, Message-ID, References, In-Reply-To) — NOT the full raw message —
  to support ad detection (Stage 6) without leaking excessive content.
* Analysis columns (category, is_advertisement, ...) are nullable and populated
  in Stage 3. Defining them now avoids a schema migration later.
* Per the privacy policy, only a ``body_snippet`` (truncated) is persisted; the
  full body is fetched live when needed.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MailDirection(str, enum.Enum):
    INBOX = "inbox"
    SENT = "sent"


class UnifiedEmail(Base):
    __tablename__ = "unified_emails"
    __table_args__ = (UniqueConstraint("account_id", "message_id", name="uq_account_message"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # RFC822 Message-ID (stripped of <>). Empty string fallback for malformed mail.
    message_id: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    thread_id: Mapped[str] = mapped_column(String(512), default="", index=True)

    direction: Mapped[MailDirection] = mapped_column(
        Enum(MailDirection, name="mail_direction"), default=MailDirection.INBOX
    )

    sender: Mapped[str] = mapped_column(Text, default="")
    sender_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    recipients: Mapped[Any] = mapped_column(JSONB, default=list)
    subject: Mapped[str] = mapped_column(Text, default="")

    # Truncated plain-text preview (<= ~500 chars) — full body fetched on demand.
    body_snippet: Mapped[str] = mapped_column(Text, default="")

    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    has_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    # Mailbox actions: archive moves the mail out of the default inbox view
    # (kept searchable), star is the manual "flagged" marker.
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    raw_headers: Mapped[Any] = mapped_column(JSONB, default=dict)

    # ---- AI analysis (populated from Stage 3; nullable until then) ----
    category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    is_advertisement: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    priority_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Dashboard handling: when the user dismissed/handled this email from the
    # priority queue. NULL = still pending on the dashboard.
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UnifiedEmail id={self.id} account={self.account_id} subj={self.subject!r}>"
