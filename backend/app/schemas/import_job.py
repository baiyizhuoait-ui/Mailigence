"""Pydantic schemas for import jobs."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, computed_field, model_validator

from app.models.import_job import ImportStatus


class ImportStartRequest(BaseModel):
    """Pick a historical range to import. Exactly one of ``days`` / ``since``."""

    days: Optional[int] = None
    since: Optional[date] = None

    @model_validator(mode="after")
    def _validate_range(self) -> "ImportStartRequest":
        if self.days is None and self.since is None:
            raise ValueError("Provide either 'days' or 'since'.")
        if self.days is not None and self.days <= 0:
            raise ValueError("'days' must be positive.")
        return self


class ImportJobOut(BaseModel):
    id: int
    account_id: int
    status: ImportStatus
    range_days: int
    since_date: date
    total: int
    processed: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: str = ""
    created_at: datetime

    @computed_field
    @property
    def progress_pct(self) -> float:
        return round(self.processed / self.total * 100, 1) if self.total else 0.0

    model_config = {"from_attributes": True}
