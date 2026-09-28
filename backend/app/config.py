"""Application settings loaded from environment variables.

Security notes
--------------
* ``CREDENTIAL_ENCRYPTION_KEY`` is the master key protecting every mailbox
  credential (app passwords and OAuth2 refresh tokens) at rest. It MUST be set
  before any account is created. If it is rotated, existing credentials must be
  re-encrypted with the old key first.
* ``AI_API_KEY`` / OAuth secrets are only read into memory and never logged.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Application
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Database
    # psycopg (v3) driver: ships binary wheels for Python 3.13 on Windows and
    # avoids the asyncpg SSLRequest crash seen with portable PostgreSQL builds.
    database_url: str = (
        "postgresql+psycopg://mailigence:mailigence_dev_pw@127.0.0.1:5432/mailigence"
    )

    # Credential encryption (Fernet). Required once accounts are created.
    credential_encryption_key: str = ""

    # AI (Stage 3+)
    # auto | ai_only | rules_only — the default analysis mode (UI can override)
    ai_analysis_mode: str = "auto"
    ai_provider: str = "openai"
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    anthropic_api_key: str = ""
    # Semantic-search embedding model (openai-compatible embeddings endpoint).
    # Empty -> auto default (text-embedding-3-small for OpenAI-compatible /
    # Ollama; embedding disabled for anthropic, which has no embeddings API).
    ai_embedding_model: str = ""
    # pgvector column dimension — must match the embedding model in use
    # (text-embedding-3-small = 1536; nomic-embed-text via Ollama = 768).
    embedding_dim: int = 1536
    # Accuracy tuning. AI_MAX_BODY_CHARS caps the body text fed to the LLM
    # (0 = auto: 2500 cloud / 1200 local). AI_NUM_CTX sets Ollama's context
    # window explicitly (0 = leave the server default; raise it if local
    # models return truncated/garbage JSON on long prompts).
    ai_max_body_chars: int = 0
    ai_num_ctx: int = 0
    # Deterministic fast-path (P2): unambiguous bulk mail / OTP / calendar
    # invites skip the LLM entirely (cost + stability). Set 0/false to disable.
    ai_skip_deterministic: bool = True

    # OAuth2 providers
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    public_base_url: str = "http://localhost:8000"

    # Full-text search ranking weights (sum = 1.0). Tune via env:
    # SEARCH_RELEVANCE_WEIGHT / SEARCH_PRIORITY_WEIGHT / SEARCH_RECENCY_WEIGHT
    search_relevance_weight: float = 0.6
    search_priority_weight: float = 0.2
    search_recency_weight: float = 0.2

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def encryption_key_configured(self) -> bool:
        return bool(self.credential_encryption_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
