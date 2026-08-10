"""Dynamic email category management.

Categories are no longer a fixed preset: the AI classifier auto-registers new
categories as it encounters them, and the user can add, rename or delete
categories here. Deleting a category nulls ``UnifiedEmail.category`` for the
affected mails (they become "uncategorized") but never deletes the mails.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email import UnifiedEmail
from app.models.email_category import EmailCategory
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate
from app.services.categories import DEFAULT_CATEGORY_COLOR

router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)) -> list[CategoryOut]:
    """List all categories with their per-category mail counts."""
    count_stmt = (
        select(UnifiedEmail.category, func.count(UnifiedEmail.id))
        .where(UnifiedEmail.category.is_not(None))
        .group_by(UnifiedEmail.category)
    )
    counts = {name: count for name, count in (await db.execute(count_stmt)).all()}

    result = await db.execute(
        select(EmailCategory).order_by(EmailCategory.is_system.desc(), EmailCategory.id.asc())
    )
    out: list[CategoryOut] = []
    for row in result.scalars().all():
        out.append(
            CategoryOut(
                id=row.id,
                name=row.name,
                label=row.label or row.name,
                color=row.color,
                is_system=row.is_system,
                email_count=counts.get(row.name, 0),
                created_at=row.created_at,
            )
        )
    return out


@router.post("", response_model=CategoryOut, status_code=201)
async def create_category(
    payload: CategoryCreate, db: AsyncSession = Depends(get_db)
) -> CategoryOut:
    """Create a user-defined category."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name cannot be empty")
    existing = await db.execute(
        select(EmailCategory).where(EmailCategory.name == name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Category already exists: {name}")

    row = EmailCategory(
        name=name,
        label=(payload.label or "").strip() or name,
        color=payload.color or DEFAULT_CATEGORY_COLOR,
        is_system=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return CategoryOut(
        id=row.id,
        name=row.name,
        label=row.label or row.name,
        color=row.color,
        is_system=row.is_system,
        email_count=0,
        created_at=row.created_at,
    )


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
) -> CategoryOut:
    """Rename the display label and/or change the color."""
    row = await db.get(EmailCategory, category_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if payload.label is not None:
        row.label = payload.label
    if payload.color is not None:
        row.color = payload.color or DEFAULT_CATEGORY_COLOR
    await db.commit()
    await db.refresh(row)
    count = (
        await db.execute(
            select(func.count(UnifiedEmail.id)).where(
                UnifiedEmail.category == row.name
            )
        )
    ).scalar_one()
    return CategoryOut(
        id=row.id,
        name=row.name,
        label=row.label or row.name,
        color=row.color,
        is_system=row.is_system,
        email_count=count,
        created_at=row.created_at,
    )


@router.delete("/{category_id}", response_model=dict)
async def delete_category(
    category_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    """Delete a category; affected mails become uncategorized (NULL).

    The affected mails re-enter the analysis queue (their ``category`` is
    NULL, which ``run_analysis`` now treats as "needs classification"), and a
    background analysis run is kicked off so the AI re-classifies them.
    """
    row = await db.get(EmailCategory, category_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Category not found")

    await db.execute(
        update(UnifiedEmail)
        .where(UnifiedEmail.category == row.name)
        .values(category=None)
    )
    await db.delete(row)
    await db.commit()

    # Kick off re-classification of the now-uncategorized mails in the
    # background (best-effort; the periodic sweep covers it otherwise).
    try:
        from app.services.analysis_service import manager as analysis_mgr
        analysis_mgr.start(None)
    except Exception:
        pass

    return {"deleted": category_id}
