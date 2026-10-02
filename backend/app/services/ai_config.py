"""AI configuration resolution — active provider profile + global toggles.

Providers live in the ``ai_provider_profiles`` table as independently managed
profiles ("DeepSeek 云端", "本地 LM Studio", ...); exactly one is ``is_active``
and drives every AI call. Global behavior toggles (analysis mode, semantic
embedding model) stay on the single-row ``app_settings`` table.

* ``load_ai_config(db)`` -> an ``AiConfig`` dataclass with effective values.
* With no profile saved, values fall back to environment variables, so a fresh
  checkout works purely from .env (and the legacy single-config DB row is
  migrated to a profile on startup).
* Profile CRUD helpers keep the API layer thin and encryption centralized.

``analysis_mode`` decides how analysis behaves:
  auto       — use AI when configured; degrade to rules on failure/misconfig
  ai_only    — always use AI; errors propagate (no silent rule fallback)
  rules_only — never call the LLM; pure programmatic analysis
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai_provider_profile import AIProviderProfile
from app.models.app_setting import AppSetting
from app.services import crypto

VALID_MODES = ("auto", "ai_only", "rules_only")
# provider_type values on AIProviderProfile.
PROFILE_TYPES = ("openai_compatible", "anthropic", "rules_only")


@dataclass
class AiConfig:
    analysis_mode: str = "auto"
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    api_key_from_db: bool = False  # whether the key came from a profile (DB) or env
    embedding_model: str = ""      # semantic-search model; empty -> auto/off
    profile_id: int | None = None  # active profile driving this config (None=env)
    # Resolved Ollama context window for this config: profile manual value or
    # probe cache. None → caller falls back to env AI_NUM_CTX / server default.
    resolved_num_ctx: int | None = None

    @property
    def use_ai(self) -> bool:
        """True when the current mode actually calls the LLM."""
        if self.analysis_mode == "rules_only":
            return False
        return bool(self.api_key and self.base_url and self.model)

    @property
    def effective_mode(self) -> str:
        return self.analysis_mode if self.analysis_mode in VALID_MODES else "auto"

    @property
    def effective_embedding_model(self) -> str:
        """Model used for embeddings, or "" when embedding is unavailable.

        An explicitly configured embedding model always wins. When empty, the
        auto-default ``text-embedding-3-small`` only applies to the official
        OpenAI endpoint — local servers (Ollama / LM Studio / vLLM / llama.cpp)
        and other compatible providers (DeepSeek, Kimi, Qwen ...) do not host
        that cloud model and would reject it with "invalid model name", so for
        them embedding stays OFF unless an embedding model is set explicitly.
        """
        model = self.embedding_model.strip()
        if model:
            return model
        if not self.base_url or "openai.com" in self.base_url:
            return "text-embedding-3-small"
        return ""

    @property
    def embedding_enabled(self) -> bool:
        """True when the vector (semantic) retrieval channel can be used.

        Anthropic has no embeddings API, and rules-only / missing-key setups
        must never trigger any AI call — both disable the channel silently so
        the rest of the app keeps working (keyword search only).
        """
        if self.analysis_mode == "rules_only" or self.provider == "anthropic":
            return False
        return bool(self.api_key and self.base_url and self.effective_embedding_model)


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
    # openai-compatible (OpenAI / DeepSeek / Kimi / Qwen / GLM / Ollama /
    # LM Studio / vLLM / llama.cpp server / oneAPI ...)
    return (
        "openai",
        (settings.ai_base_url or "").rstrip("/"),
        settings.ai_api_key,
        settings.ai_model,
    )


# ---- profile access --------------------------------------------------------


async def active_profile(db: AsyncSession) -> AIProviderProfile | None:
    """The profile currently driving AI calls, if any."""
    return (
        await db.execute(
            select(AIProviderProfile)
            .where(AIProviderProfile.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_profiles(db: AsyncSession) -> list[AIProviderProfile]:
    return list(
        (
            await db.execute(
                select(AIProviderProfile).order_by(AIProviderProfile.id.asc())
            )
        ).scalars().all()
    )


async def create_profile(
    db: AsyncSession,
    *,
    label: str,
    provider_type: str,
    base_url: str,
    api_key: str,
    model: str,
    num_ctx: int = 0,
) -> AIProviderProfile:
    """Create a profile; the first one (or when none is active) auto-activates."""
    existing = await list_profiles(db)
    profile = AIProviderProfile(
        label=label,
        provider_type=provider_type,
        base_url=(base_url or "").strip().rstrip("/"),
        model=(model or "").strip(),
        num_ctx=max(int(num_ctx or 0), 0),
        api_key_encrypted=crypto.encrypt(api_key.strip()) if api_key.strip() else "",
        is_active=not any(p.is_active for p in existing),
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def update_profile(
    db: AsyncSession,
    profile: AIProviderProfile,
    *,
    label: str | None = None,
    provider_type: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    num_ctx: int | None = None,
) -> AIProviderProfile:
    """Edit a profile. Empty ``api_key`` keeps the stored key."""
    if label is not None:
        profile.label = label
    if provider_type is not None:
        profile.provider_type = provider_type
    if base_url is not None:
        profile.base_url = (base_url or "").strip().rstrip("/")
    if model is not None:
        profile.model = (model or "").strip()
    if num_ctx is not None:
        profile.num_ctx = max(int(num_ctx), 0)
    if api_key is not None and api_key.strip():
        profile.api_key_encrypted = crypto.encrypt(api_key.strip())
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(profile)
    return profile


async def delete_profile(db: AsyncSession, profile: AIProviderProfile) -> None:
    """Delete a profile; if it was active, the first remaining one takes over."""
    was_active = profile.is_active
    await db.delete(profile)
    await db.commit()
    if was_active:
        remaining = await list_profiles(db)
        if remaining:
            await activate_profile(db, remaining[0].id)


async def activate_profile(db: AsyncSession, profile_id: int) -> AIProviderProfile | None:
    """Switch the active pointer to ``profile_id`` (only one is active)."""
    profiles = await list_profiles(db)
    target = next((p for p in profiles if p.id == profile_id), None)
    if target is None:
        return None
    for p in profiles:
        p.is_active = p.id == profile_id
    await db.commit()
    return target


# ---- effective config ------------------------------------------------------


async def load_ai_config(db: AsyncSession) -> AiConfig:
    """Load effective AI config: active profile merged with global toggles.

    Global analysis mode / embedding model come from ``app_settings`` (falling
    back to .env). Provider connection fields come from the active profile
    (falling back to .env when no profile is saved).

    num_ctx resolution: profile manual value (>0) > probe cache > None. None
    means "send no num_ctx" — the server default applies (a Modelfile with
    num_ctx 16384 keeps working; we never force a small window at runtime).
    """
    row = await db.get(AppSetting, 1)
    mode = (row.ai_analysis_mode or "") if row else ""
    if mode not in VALID_MODES:
        mode = settings.ai_analysis_mode or "auto"
    embedding_model = ((row.ai_embedding_model if row else "") or "").strip() or (
        settings.ai_embedding_model or ""
    )

    profile = await active_profile(db)
    if profile is None:
        prov, base_url, api_key, model = _env_fallback("")
        return AiConfig(
            analysis_mode=mode,
            provider=prov,
            base_url=base_url,
            api_key=api_key,
            model=model,
            embedding_model=embedding_model,
        )

    provider = "anthropic" if profile.provider_type == "anthropic" else "openai"
    api_key = ""
    api_key_from_db = False
    if profile.api_key_encrypted:
        try:
            api_key = crypto.decrypt(profile.api_key_encrypted)
            api_key_from_db = True
        except crypto.EncryptionError:
            api_key = ""  # undecryptable -> treat as unset
    if not api_key:
        # Profile without a stored key -> fall back to the env key for its type.
        api_key = (
            settings.anthropic_api_key if provider == "anthropic" else settings.ai_api_key
        )

    # Context window: manual profile value wins, then the probe cache. When
    # neither applies we leave None — the Ollama request omits num_ctx and the
    # server default (e.g. the Modelfile's) takes effect.
    resolved_num_ctx: int | None = None
    if profile.num_ctx and profile.num_ctx > 0:
        resolved_num_ctx = profile.num_ctx
    else:
        from app.services.context_probe import get_cached_num_ctx

        resolved_num_ctx = await get_cached_num_ctx(
            db, profile.base_url or "", profile.model or ""
        )

    return AiConfig(
        analysis_mode=mode,
        provider=provider,
        base_url=profile.base_url or "",
        api_key=api_key,
        model=profile.model or "",
        api_key_from_db=api_key_from_db,
        embedding_model=embedding_model,
        profile_id=profile.id,
        resolved_num_ctx=resolved_num_ctx,
    )


async def save_global_ai_settings(
    db: AsyncSession,
    *,
    analysis_mode: str = "",
    embedding_model: str = "",
) -> AiConfig:
    """Persist the global toggles only (analysis mode, embedding model)."""
    row = await db.get(AppSetting, 1)
    if row is None:
        row = AppSetting(id=1)
        db.add(row)
    if analysis_mode in VALID_MODES:
        row.ai_analysis_mode = analysis_mode
    if embedding_model is not None:
        row.ai_embedding_model = embedding_model.strip()
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return await load_ai_config(db)
