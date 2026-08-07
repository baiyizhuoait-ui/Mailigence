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

    # OAuth2 providers
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    public_base_url: str = "http://localhost:8000"

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
