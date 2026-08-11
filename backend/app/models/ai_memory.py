"""AiMemory model — distilled user preferences for AI mail analysis.

The settings page lets the user type free-form preferences ("I want to watch
out for ads from X", "mail from my boss is work"). The LLM distills those
inputs into concise, actionable memory entries stored here; every analysis
prompt then includes them so the AI classifies/prioritises mail according to
the user's stated preferences.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AiMemory(Base):
    __tablename__ = "ai_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # One actionable preference sentence, e.g. "mark mail from X as work".
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AiMemory id={self.id} content={self.content[:40]!r}>"
