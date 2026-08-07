"""ImportJob model — tracks a background historical-mail import.

One job per (account, range). Created when the user picks "import recent N days"
on first connection; the ImportJobManager runs it as an asyncio background task,
updating ``total``/``processed`` per batch so the frontend progress bar can poll.

Status lifecycle: pending -> running -> completed | failed | cancelled.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ImportStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )

    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_status"), default=ImportStatus.PENDING, index=True
    )

    # Range descriptor: range_days is the user-facing choice (7/30/90...);
    # since_date is the concrete IMAP SINCE boundary derived from it.
    range_days: Mapped[int] = mapped_column(Integer, default=7)
    since_date: Mapped[date] = mapped_column(Date, nullable=False)

    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ImportJob id={self.id} account={self.account_id} {self.status} {self.processed}/{self.total}>"
