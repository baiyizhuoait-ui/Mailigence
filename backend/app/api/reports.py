"""Inbox report statistics.

Provides an aggregated summary over a day / week / month window so the UI
can render total/unread/ad counts, category & priority distributions, top
senders and a per-day trend without N+1 round-trips.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, cast, Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email import UnifiedEmail
from app.schemas.report import ReportSummary, TopSender, DailyTrendPoint
from app.services.categories import category_label_map

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _range_bounds(range_key: str) -> tuple[date, date]:
    """Return (start_date, end_date) inclusive for the given range key.

    ``end_date`` is always today; ``start_date`` is today for ``day``,
    today - 6 days for ``week`` (7-day window) and today - 29 days for
    ``month`` (30-day window).
    """
    today = date.today()
    if range_key == "day":
        start = today
    elif range_key == "month":
        start = today - timedelta(days=29)
    else:  # "week" (default)
        start = today - timedelta(days=6)
    return start, today


@router.get("/summary", response_model=ReportSummary)
async def report_summary(
    range: str = Query("week", pattern="^(day|week|month)$"),
    account_id: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> ReportSummary:
    """Aggregate inbox statistics over a day/week/month window.

    All queries share the same base filter: ``received_at`` falls inside
    [start_date, end_date+1day) and, when provided, ``account_id`` matches.
    """
    start_date, end_date = _range_bounds(range)
    # received_at is a tz-aware datetime, so the upper bound is exclusive
    # at midnight of the day after end_date.
    start_dt = start_date
    end_exclusive = end_date + timedelta(days=1)

    conditions = [
        UnifiedEmail.received_at >= start_dt,
        UnifiedEmail.received_at < end_exclusive,
    ]
    if account_id is not None:
        conditions.append(UnifiedEmail.account_id == account_id)
    base_where = and_(*conditions)

    # --- total / unread / ads (single pass with conditional counts) ---
    counts_stmt = select(
        func.count(UnifiedEmail.id).label("total"),
        func.count(
            case((UnifiedEmail.is_read.is_(False), 1))
        ).label("unread"),
        func.count(
            case((UnifiedEmail.is_advertisement.is_(True), 1))
        ).label("ads"),
    ).where(base_where)
    counts_row = (await db.execute(counts_stmt)).one()
    total = counts_row.total or 0
    unread = counts_row.unread or 0
    ads = counts_row.ads or 0

    # --- category distribution (keys are display labels, not internal names) ---
    category_stmt = (
        select(UnifiedEmail.category, func.count(UnifiedEmail.id))
        .where(base_where)
        .group_by(UnifiedEmail.category)
    )
    name_to_label = await category_label_map(db)
    category_dist: dict[str, int] = {}
    for category, count in (await db.execute(category_stmt)).all():
        if category is None:
            key = name_to_label.get("uncategorized", "uncategorized")
        else:
            key = name_to_label.get(category, category)
        category_dist[key] = category_dist.get(key, 0) + count

    # --- top senders (limit 10) ---
    top_stmt = (
        select(
            UnifiedEmail.sender_email,
            UnifiedEmail.sender,
            func.count(UnifiedEmail.id).label("count"),
        )
        .where(base_where)
        .group_by(UnifiedEmail.sender_email, UnifiedEmail.sender)
        .order_by(func.count(UnifiedEmail.id).desc())
        .limit(10)
    )
    top_senders: list[TopSender] = []
    for sender_email, sender_name, count in (await db.execute(top_stmt)).all():
        top_senders.append(
            TopSender(
                sender_email=sender_email or "",
                sender_name=sender_name or "",
                count=count,
            )
        )

    # --- daily trend (only days that have mail; ascending) ---
    day_expr = func.to_char(
        cast(UnifiedEmail.received_at, Date), "YYYY-MM-DD"
    )
    trend_stmt = (
        select(day_expr.label("day"), func.count(UnifiedEmail.id).label("count"))
        .where(base_where)
        .group_by(day_expr)
        .order_by(day_expr.asc())
    )
    daily_trend: list[DailyTrendPoint] = []
    for day, count in (await db.execute(trend_stmt)).all():
        daily_trend.append(DailyTrendPoint(date=day, count=count))

    # --- priority distribution (high>=70, medium 40-69, low<40) ---
    priority_stmt = select(
        func.count(
            case((UnifiedEmail.priority_score >= 70, 1))
        ).label("high"),
        func.count(
            case(
                (
                    and_(
                        UnifiedEmail.priority_score >= 40,
                        UnifiedEmail.priority_score < 70,
                    ),
                    1,
                )
            )
        ).label("medium"),
        func.count(
            case((UnifiedEmail.priority_score < 40, 1))
        ).label("low"),
    ).where(base_where)
    priority_row = (await db.execute(priority_stmt)).one()
    priority_dist: dict[str, int] = {
        "high": priority_row.high or 0,
        "medium": priority_row.medium or 0,
        "low": priority_row.low or 0,
    }

    # --- suggested-action distribution ---
    action_stmt = (
        select(UnifiedEmail.suggested_action, func.count(UnifiedEmail.id))
        .where(base_where)
        .group_by(UnifiedEmail.suggested_action)
    )
    action_dist: dict[str, int] = {}
    for action, count in (await db.execute(action_stmt)).all():
        key = action if action is not None else "uncategorized"
        action_dist[key] = count

    return ReportSummary(
        range=range,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        account_id=account_id,
        total=total,
        unread=unread,
        ads=ads,
        category_dist=category_dist,
        top_senders=top_senders,
        daily_trend=daily_trend,
        priority_dist=priority_dist,
        action_dist=action_dist,
    )
