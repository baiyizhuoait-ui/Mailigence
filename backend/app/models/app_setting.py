"""AppSetting model — single-row table for user-tunable app settings.

Currently stores the AI analysis configuration that the settings UI writes to
(the analysis mode, provider, endpoint and model name). The API key is stored
Fernet-encrypted (see ``app.services.crypto``), mirroring how mailbox
credentials are protected.

The row is always id=1. When no row exists, defaults are taken from the
environment (.env), so a fresh checkout works out of the box.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Analysis mode: auto (AI first, fall back to rules) | ai_only | rules_only
    ai_analysis_mode: Mapped[str] = mapped_column(String(16), default="auto")

    # Provider: "" (env default) | openai (OpenAI-compatible) | anthropic
    ai_provider: Mapped[str] = mapped_column(String(32), default="")

    # Endpoint & model (empty -> fall back to .env values)
    ai_base_url: Mapped[str] = mapped_column(String(255), default="")
    ai_model: Mapped[str] = mapped_column(String(120), default="")

    # Fernet-encrypted API key. Empty -> use AI_API_KEY / ANTHROPIC_API_KEY from env.
    ai_api_key_encrypted: Mapped[str] = mapped_column(Text, default="")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AppSetting id={self.id} mode={self.ai_analysis_mode!r}>"
