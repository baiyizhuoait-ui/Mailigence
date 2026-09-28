"""ClassificationFeedback model — the human-correction loop (P1).

Every time the user fixes an AI classification in the UI (category or
advertisement flag), one row lands here. Future analyses of mail from the
same sender inject the most recent corrections as few-shot context, so the
model literally learns from its own mistakes — the Inbox-Zero approach.

One row per (email, correction); a re-correction of the same email replaces
the previous row (the API layer upserts).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClassificationFeedback(Base):
    __tablename__ = "classification_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("unified_emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Display form ("张工 <zhang@company.example>") kept for prompt context.
    sender: Mapped[str] = mapped_column(Text, default="")
    # Normalized address — the lookup key for few-shot injection (indexed).
    sender_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    subject: Mapped[str] = mapped_column(Text, default="")

    was_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    corrected_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    was_ad: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    corrected_ad: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ClassificationFeedback id={self.id} sender_email={self.sender_email!r} "
            f"corrected={self.corrected_category!r}>"
        )
