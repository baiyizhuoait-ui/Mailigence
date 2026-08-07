"""AI configuration resolution — merges DB settings with .env defaults.

The settings UI writes AI preferences (analysis mode, provider, endpoint,
model, encrypted API key) into the ``app_settings`` table. This module is the
single source of truth used by every AI call site:

* ``load_ai_config(db)`` -> an ``AiConfig`` dataclass with effective values.
* DB fields that are empty fall back to environment variables, so a fresh
  checkout (no DB row) works purely from .env.
* ``save_ai_config(db, data)`` persists UI form values, encrypting the key.

``analysis_mode`` decides how analysis behaves:
  auto       — use AI when configured; degrade to rules on failure/misconfig
  ai_only    — always use AI; errors propagate (no silent rule fallback)
  rules_only — never call the LLM; pure programmatic analysis
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.app_setting import AppSetting
from app.services import crypto

VALID_MODES = ("auto", "ai_only", "rules_only")
VALID_PROVIDERS = ("openai", "anthropic", "")


@dataclass
class AiConfig:
    analysis_mode: str = "auto"
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    api_key_from_db: bool = False  # whether the key came from the DB (UI) or env

    @property
    def use_ai(self) -> bool:
        """True when the current mode actually calls the LLM."""
        if self.analysis_mode == "rules_only":
            return False
        return bool(self.api_key and self.base_url and self.model)

    @property
    def effective_mode(self) -> str:
        return self.analysis_mode if self.analysis_mode in VALID_MODES else "auto"


def _env_fallback(provider: str | None) -> tuple[str, str, str, str]:
    """Resolve (provider, base_url, api_key, model) from env, if present."""
    prov = (provider or "").strip() or (settings.ai_provider or "openai")
    if prov == "anthropic":
        return (
            "anthropic",
            (settings.ai_base_url or "https://api.anthropic.com/v1").rstrip("/"),
            settings.anthropic_api_key,
            settings.ai_model,
        )
    # openai-compatible (OpenAI / DeepSeek / Kimi / Qwen / GLM / Ollama ...)
    return (
        "openai",
        (settings.ai_base_url or "").rstrip("/"),
        settings.ai_api_key,
        settings.ai_model,
    )


async def load_ai_config(db: AsyncSession) -> AiConfig:
    """Load effective AI config, merging DB row (if any) over .env defaults."""
    row = await db.get(AppSetting, 1)
    if row is None:
        prov, base_url, api_key, model = _env_fallback("")
        return AiConfig(
            analysis_mode=(settings.ai_analysis_mode or "auto"),
            provider=prov,
            base_url=base_url,
            api_key=api_key,
            model=model,
            api_key_from_db=False,
        )

    mode = row.ai_analysis_mode or "auto"
    if mode not in VALID_MODES:
        mode = "auto"

    # Provider/base_url/model: DB value wins; empty falls back to env.
    env_prov, env_url, env_key, env_model = _env_fallback(row.ai_provider)
    provider = (row.ai_provider or "").strip() or env_prov
    base_url = (row.ai_base_url or "").strip().rstrip("/") or env_url
    model = (row.ai_model or "").strip() or env_model

    # API key: DB-encrypted wins; else env.
    api_key = ""
    api_key_from_db = False
    if row.ai_api_key_encrypted:
        try:
            api_key = crypto.decrypt(row.ai_api_key_encrypted)
            api_key_from_db = True
        except crypto.EncryptionError:
            api_key = ""  # undecryptable -> treat as unset
    if not api_key:
        api_key = env_key

    return AiConfig(
        analysis_mode=mode,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        api_key_from_db=api_key_from_db,
    )


async def save_ai_config(
    db: AsyncSession,
    *,
    analysis_mode: str = "",
    provider: str = "",
    base_url: str = "",
    model: str = "",
    api_key: str = "",
) -> AiConfig:
    """Persist AI settings from the UI form.

    Empty ``api_key`` keeps whatever is already stored (DB or env). Pass
    ``api_key=None`` to explicitly clear the stored key.
    """
    row = await db.get(AppSetting, 1)
    if row is None:
        row = AppSetting(id=1)
        db.add(row)

    if analysis_mode in VALID_MODES:
        row.ai_analysis_mode = analysis_mode
    if provider in VALID_PROVIDERS:
        row.ai_provider = provider
    if base_url is not None:
        row.ai_base_url = base_url.strip().rstrip("/")
    if model is not None:
        row.ai_model = model.strip()
    if api_key is not None:
        if api_key.strip():
            row.ai_api_key_encrypted = crypto.encrypt(api_key.strip())
        else:
            row.ai_api_key_encrypted = ""

    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return await load_ai_config(db)
