"""Settings API — read/update user-tunable app settings (AI analysis).

GET returns the effective AI configuration (masked) plus the UI state.
PUT persists the form values; the API key is Fernet-encrypted at rest.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services import ai_config, crypto
from app.services.ai_config import load_ai_config, save_ai_config

router = APIRouter(prefix="/api/settings", tags=["settings"])


class AiSettingsIn(BaseModel):
    analysis_mode: str = Field(default="auto")
    provider: str = Field(default="")
    base_url: str = Field(default="")
    model: str = Field(default="")
    # Optional: pass a new key to store it; empty string keeps existing.
    api_key: str = Field(default="")
    # If true, the stored DB key (if any) is cleared.
    clear_api_key: bool = Field(default=False)


class AiSettingsOut(BaseModel):
    analysis_mode: str
    provider: str
    base_url: str
    model: str
    # Never send the key back; only whether one is configured and its origin.
    api_key_configured: bool
    api_key_from_db: bool
    # The .env fallback values, so the UI can show what env provides.
    env_provider: str
    env_base_url: str
    env_model: str
    env_key_configured: bool


@router.get("", response_model=AiSettingsOut)
async def get_ai_settings(db: AsyncSession = Depends(get_db)) -> AiSettingsOut:
    cfg = await load_ai_config(db)
    env_provider, env_url, env_key, env_model = ai_config._env_fallback(cfg.provider)
    return AiSettingsOut(
        analysis_mode=cfg.effective_mode,
        provider=cfg.provider,
        base_url=cfg.base_url,
        model=cfg.model,
        api_key_configured=bool(cfg.api_key),
        api_key_from_db=cfg.api_key_from_db,
        env_provider=env_provider,
        env_base_url=env_url,
        env_model=env_model,
        env_key_configured=bool(env_key),
    )


@router.put("", response_model=AiSettingsOut)
async def update_ai_settings(
    payload: AiSettingsIn, db: AsyncSession = Depends(get_db)
) -> AiSettingsOut:
    if payload.analysis_mode not in ai_config.VALID_MODES:
        raise HTTPException(status_code=422, detail="Invalid analysis_mode")
    if payload.provider not in ai_config.VALID_PROVIDERS:
        raise HTTPException(status_code=422, detail="Invalid provider")

    # Key semantics: non-empty -> store new; empty + clear flag -> remove;
    # empty without clear flag -> keep whatever is already configured.
    if payload.clear_api_key:
        key_to_save: str | None = ""
    elif payload.api_key.strip():
        key_to_save = payload.api_key.strip()
    else:
        key_to_save = None

    # Encrypting a new key requires the master encryption key to be configured.
    if key_to_save and not settings.credential_encryption_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "CREDENTIAL_ENCRYPTION_KEY is not set, so the API key can't be "
                "stored securely. Set it in backend/.env (see .env.example) or "
                "leave the key field empty and configure AI_API_KEY there instead."
            ),
        )
    if key_to_save:
        try:
            crypto.encrypt(key_to_save)  # validate early
        except crypto.EncryptionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    await save_ai_config(
        db,
        analysis_mode=payload.analysis_mode,
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
        api_key=key_to_save,
    )
    # Settings changed -> drop cached schedule analysis so it re-runs with the
    # new mode/provider on the next dashboard request.
    from app.services.schedule_analyzer import invalidate_cache

    invalidate_cache()
    return await get_ai_settings(db)
