"""Pydantic schemas for dynamic email categories."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class CategoryOut(BaseModel):
    id: int
    name: str
    label: str
    color: Optional[str] = None
    is_system: bool
    email_count: int = 0
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    label: Optional[str] = Field(default=None, max_length=128)
    color: Optional[str] = Field(default=None, max_length=32)

    @field_validator("name", "label")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
        return v


class CategoryUpdate(BaseModel):
    label: Optional[str] = Field(default=None, max_length=128)
    color: Optional[str] = Field(default=None, max_length=32)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                v = None
        return v
