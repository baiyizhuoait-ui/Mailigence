"""Shared helpers for dynamic email categories.

Category data lives in the ``email_categories`` table (see
``app.models.email_category``). This module holds the read/write helpers used
by both the categories API and the AI analysis pipeline, so the service layer
never has to import from the API layer.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_category import EmailCategory

DEFAULT_CATEGORY_COLOR = "#9ca3af"


async def ensure_category(db: AsyncSession, name: str) -> EmailCategory:
    """Get or auto-create a category row. Used when the AI returns a category
    name that isn't registered yet."""
    name = (name or "").strip()
    if not name:
        name = "other"
    row = (
        await db.execute(select(EmailCategory).where(EmailCategory.name == name))
    ).scalar_one_or_none()
    if row is None:
        row = EmailCategory(name=name, label=name, is_system=False)
        db.add(row)
        await db.flush()
    return row


async def list_category_names(db: AsyncSession) -> list[str]:
    """Return the registered category names (for AI prompt injection)."""
    result = await db.execute(select(EmailCategory.name))
    return [r[0] for r in result.all()]


async def category_label_map(db: AsyncSession) -> dict[str, str]:
    """Return ``{name: label}`` for registered categories."""
    result = await db.execute(select(EmailCategory.name, EmailCategory.label))
    return {name: label or name for name, label in result.all()}
