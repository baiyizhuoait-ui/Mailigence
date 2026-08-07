"""Pydantic schemas for ad-mail detection & blocked-sender endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class BlockedSenderOut(BaseModel):
    id: int
    account_id: Optional[int] = None
    sender_email: str
    sender_name: str
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BlockSenderRequest(BaseModel):
    sender_email: str
    sender_name: str = ""
    reason: str = "manual"
    account_id: Optional[int] = None


class AdStatsOut(BaseModel):
    total_ads: int
    blocked_senders: int
    ads_by_category: dict[str, int]


class BatchAdActionRequest(BaseModel):
    action: Literal["delete", "mark_read"]
    email_ids: list[int] = []


class BatchAdActionResult(BaseModel):
    affected: int
    action: str


class UnsubscribeInfo(BaseModel):
    email_id: int
    has_unsubscribe: bool
    url: Optional[str] = None
    mailto: Optional[str] = None
