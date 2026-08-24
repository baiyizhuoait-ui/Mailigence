"""Settings API.

* ``GET/PUT /api/settings`` — the aggregate AI status (used by views for the
  "AI available" flag) plus the *global* toggles (analysis mode, semantic
  embedding model). Provider connection details live in profiles, below.
* ``/api/settings/provider-profiles`` — CRUD + activate for the multi-config
  provider list ("DeepSeek 云端", "本地 LM Studio", ...).
* ``GET .../provider-profiles/{id}/models`` — probes the standard
  OpenAI-compatible ``{base_url}/models`` endpoint (Ollama / LM Studio / vLLM /
  llama.cpp server / oneAPI all implement it). Failures never block manual
  model entry on the frontend.
* ``POST .../provider-profiles/{id}/test`` — connectivity test for any type.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.ai_provider_profile import AIProviderProfile
from app.services import ai_config, crypto
from app.services.ai_config import load_ai_config, save_global_ai_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

PROBE_TIMEOUT = 15


# ---- global settings (aggregate status + behavior toggles) -----------------


class AiSettingsIn(BaseModel):
    # Global behavior toggles only — provider connection is per-profile now.
    analysis_mode: str = Field(default="auto")
    embedding_model: str = Field(default="")


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
    # Semantic-search embedding config (UI shows it greyed out when disabled).
    embedding_model: str
    embedding_enabled: bool


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
        embedding_model=cfg.embedding_model,
        embedding_enabled=cfg.embedding_enabled,
    )


@router.put("", response_model=AiSettingsOut)
async def update_ai_settings(
    payload: AiSettingsIn, db: AsyncSession = Depends(get_db)
) -> AiSettingsOut:
    if payload.analysis_mode not in ai_config.VALID_MODES:
        raise HTTPException(status_code=422, detail="Invalid analysis_mode")
    await save_global_ai_settings(
        db,
        analysis_mode=payload.analysis_mode,
        embedding_model=payload.embedding_model,
    )
    _invalidate_schedule()
    return await get_ai_settings(db)


# ---- provider profiles -----------------------------------------------------


class ProviderProfileIn(BaseModel):
    label: str = Field(default="", min_length=1)
    provider_type: str = Field(default="openai_compatible")
    base_url: str = Field(default="")
    api_key: str = Field(default="")  # empty keeps the stored key on edit
    model: str = Field(default="")


class ProviderProfileOut(BaseModel):
    id: int
    label: str
    provider_type: str
    base_url: str
    model: str
    api_key_configured: bool
    is_active: bool


class ProviderModelsOut(BaseModel):
    models: list[str]


class ProbeIn(BaseModel):
    # Ad-hoc probe for a not-yet-saved profile (new-profile form).
    provider_type: str = Field(default="openai_compatible")
    base_url: str = Field(default="")
    api_key: str = Field(default="")


def _to_out(p: AIProviderProfile) -> ProviderProfileOut:
    return ProviderProfileOut(
        id=p.id,
        label=p.label,
        provider_type=p.provider_type,
        base_url=p.base_url,
        model=p.model,
        api_key_configured=bool(p.api_key_encrypted),
        is_active=p.is_active,
    )


async def _get_profile(db: AsyncSession, profile_id: int) -> AIProviderProfile:
    profile = await db.get(AIProviderProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="AI 配置不存在")
    return profile


def _profile_key(profile: AIProviderProfile) -> str:
    """Decrypted key (or env fallback for the type) used for probing/testing."""
    if profile.api_key_encrypted:
        try:
            return crypto.decrypt(profile.api_key_encrypted)
        except crypto.EncryptionError:
            return ""
    return settings.anthropic_api_key if profile.provider_type == "anthropic" else settings.ai_api_key


def _guard_key_encryption(api_key: str) -> None:
    if not api_key.strip():
        return
    if not settings.credential_encryption_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "CREDENTIAL_ENCRYPTION_KEY 未配置，无法加密保存 API Key。"
                "请在 backend/.env 中配置（见 .env.example），"
                "或留空密钥（适用于无需鉴权的本地推理服务）。"
            ),
        )
    try:
        crypto.encrypt(api_key.strip())  # validate early
    except crypto.EncryptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _invalidate_schedule() -> None:
    """Config changed -> drop cached schedule analysis so it re-runs."""
    from app.services.schedule_analyzer import invalidate_cache

    invalidate_cache()


async def _probe_models(profile_type: str, base_url: str, api_key: str) -> list[str]:
    """Call the standard OpenAI-compatible ``/models`` endpoint."""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="请先填写接口地址（Base URL）")
    headers: dict[str, str] = {}
    if profile_type == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"无法连接 {base_url}：{exc.__class__.__name__}"
        ) from exc
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"模型列表接口返回 HTTP {resp.status_code}，请检查地址与密钥后重试",
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="模型列表接口返回了无法解析的内容") from exc
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [str(m["id"]) for m in items if isinstance(m, dict) and m.get("id")]


@router.get("/provider-profiles", response_model=list[ProviderProfileOut])
async def list_provider_profiles(db: AsyncSession = Depends(get_db)) -> list[ProviderProfileOut]:
    return [_to_out(p) for p in await ai_config.list_profiles(db)]


@router.post("/provider-profiles", response_model=ProviderProfileOut, status_code=201)
async def create_provider_profile(
    payload: ProviderProfileIn, db: AsyncSession = Depends(get_db)
) -> ProviderProfileOut:
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="配置名称不能为空")
    if payload.provider_type not in ai_config.PROFILE_TYPES:
        raise HTTPException(status_code=422, detail="不支持的配置类型")
    _guard_key_encryption(payload.api_key)
    profile = await ai_config.create_profile(
        db,
        label=label,
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
    )
    _invalidate_schedule()
    return _to_out(profile)


@router.put("/provider-profiles/{profile_id}", response_model=ProviderProfileOut)
async def update_provider_profile(
    profile_id: int, payload: ProviderProfileIn, db: AsyncSession = Depends(get_db)
) -> ProviderProfileOut:
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="配置名称不能为空")
    if payload.provider_type not in ai_config.PROFILE_TYPES:
        raise HTTPException(status_code=422, detail="不支持的配置类型")
    _guard_key_encryption(payload.api_key)
    profile = await _get_profile(db, profile_id)
    await ai_config.update_profile(
        db,
        profile,
        label=label,
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
    )
    _invalidate_schedule()
    return _to_out(profile)


@router.delete("/provider-profiles/{profile_id}")
async def delete_provider_profile(
    profile_id: int, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    profile = await _get_profile(db, profile_id)
    await ai_config.delete_profile(db, profile)
    _invalidate_schedule()
    return {"ok": True}


@router.post("/provider-profiles/{profile_id}/activate", response_model=ProviderProfileOut)
async def activate_provider_profile(
    profile_id: int, db: AsyncSession = Depends(get_db)
) -> ProviderProfileOut:
    profile = await ai_config.activate_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="AI 配置不存在")
    _invalidate_schedule()
    return _to_out(profile)


@router.get("/provider-profiles/{profile_id}/models", response_model=ProviderModelsOut)
async def probe_profile_models(
    profile_id: int, db: AsyncSession = Depends(get_db)
) -> ProviderModelsOut:
    profile = await _get_profile(db, profile_id)
    if profile.provider_type == "rules_only":
        raise HTTPException(status_code=400, detail="规则模式没有可探测的模型")
    models = await _probe_models(profile.provider_type, profile.base_url, _profile_key(profile))
    return ProviderModelsOut(models=models)


@router.post("/provider-profiles/probe", response_model=ProviderModelsOut)
async def probe_ad_hoc_models(payload: ProbeIn) -> ProviderModelsOut:
    if payload.provider_type == "rules_only":
        raise HTTPException(status_code=400, detail="规则模式没有可探测的模型")
    models = await _probe_models(payload.provider_type, payload.base_url, payload.api_key)
    return ProviderModelsOut(models=models)


@router.post("/provider-profiles/{profile_id}/test")
async def test_provider_connection(
    profile_id: int, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    profile = await _get_profile(db, profile_id)
    if profile.provider_type == "rules_only":
        raise HTTPException(status_code=400, detail="规则模式无需测试连接")
    models = await _probe_models(profile.provider_type, profile.base_url, _profile_key(profile))
    return {"ok": True, "message": f"连接正常（{len(models)} 个模型可用）"}
