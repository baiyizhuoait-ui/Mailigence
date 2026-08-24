"""AIProviderProfile — one saved provider configuration.

Replaces the single global AI config (AppSetting row) with a list of
independently managed provider profiles, e.g. "DeepSeek 云端", "本地 LM Studio",
"本地 Ollama", "Anthropic Claude" — any number can coexist; exactly one is
``is_active`` at a time and drives the AI / embedding calls.

Global behavior toggles (analysis mode, semantic-search embedding model) stay
on ``AppSetting``; this table only holds per-provider connection data.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIProviderProfile(Base):
    __tablename__ = "ai_provider_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # User-chosen name, e.g. "我的 DeepSeek" / "本地 LM Studio".
    label: Mapped[str] = mapped_column(String(120), default="")

    # openai_compatible | anthropic | rules_only
    provider_type: Mapped[str] = mapped_column(String(32), default="openai_compatible")

    # Endpoint root, e.g. https://api.deepseek.com/v1 or http://localhost:1234/v1
    base_url: Mapped[str] = mapped_column(String(255), default="")

    # Fernet-encrypted API key. Empty for local tools that need no auth.
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")

    model: Mapped[str] = mapped_column(String(120), default="")

    # Exactly one profile has is_active=True (drives all AI calls).
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AIProviderProfile {self.id} {self.label!r} {self.provider_type} active={self.is_active}>"
