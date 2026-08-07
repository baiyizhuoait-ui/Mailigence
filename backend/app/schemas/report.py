"""Pydantic schemas for the inbox-report statistics endpoint."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TopSender(BaseModel):
    sender_email: str
    sender_name: str
    count: int


class DailyTrendPoint(BaseModel):
    date: str  # YYYY-MM-DD
    count: int


class ReportSummary(BaseModel):
    range: str  # "day" | "week" | "month"
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    account_id: Optional[int] = None
    total: int
    unread: int
    ads: int
    category_dist: dict[str, int]
    top_senders: list[TopSender]
    daily_trend: list[DailyTrendPoint]
    priority_dist: dict[str, int]  # {"high": n, "medium": n, "low": n}
    action_dist: dict[str, int]  # {"reply": n, "review": n, "note": n, "ignore": n}
