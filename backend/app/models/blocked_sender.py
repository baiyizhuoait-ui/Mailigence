"""BlockedSender model.

Stores senders the user has blocked. A row with ``account_id = None`` is a
*global* block (applies to every account); otherwise the block is scoped to
that single account. Populated by the ad-detection pipeline (reason=
"advertisement"), the "block this sender" UI action (reason="advertisement"),
or manual user entry (reason="manual").
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BlockedSender(Base):
    __tablename__ = "blocked_senders"
    __table_args__ = (
        UniqueConstraint("account_id", "sender_email", name="uq_blocked_sender_account_email"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # null = global block (applies to all accounts)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_accounts.id"), nullable=True, index=True
    )
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sender_name: Mapped[str] = mapped_column(String(255), default="")
    # manual | advertisement | scam
    reason: Mapped[str] = mapped_column(String(100), default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BlockedSender id={self.id} {self.sender_email} account={self.account_id}>"
