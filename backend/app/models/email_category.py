"""EmailCategory — user/AI-managed mailbox categories.

Categories are *not* a fixed preset list. The AI classifier may introduce a
new category at any time (it is auto-registered here with ``is_system=False``),
and the user can add, rename or delete categories freely. ``name`` is the
unique key stored on ``UnifiedEmail.category``; ``label`` is the display name
(defaults to ``name`` when AI/user creates it without one).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailCategory(Base):
    __tablename__ = "email_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # True for the built-in seed categories; False for AI/user-created ones.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EmailCategory id={self.id} name={self.name!r} label={self.label!r}>"
