"""Pydantic schemas for email account endpoints.

NOTE: credential fields are write-only (never serialized back to the client) —
secrets are encrypted at rest and never exposed by the API.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.email_account import AuthType, SyncStatus
from app.services.oauth import PROVIDER_PRESETS, netease_servers_for_email


class TestConnectionRequest(BaseModel):
    """Validate a credential against an IMAP server before saving."""

    auth_type: AuthType
    platform: str = Field(..., description="gmail|outlook|qq|netease163|imap")
    email: EmailStr
    # For app password: the app-specific password / auth code.
    # For OAuth: not used (token obtained via OAuth flow).
    credential: Optional[str] = None
    # Generic IMAP overrides (required when platform == "imap")
    imap_server: Optional[str] = None
    imap_port: int = 993
    smtp_server: Optional[str] = None
    smtp_port: int = 465

    @model_validator(mode="after")
    def _fill_preset_and_validate(self) -> "TestConnectionRequest":
        preset = PROVIDER_PRESETS.get(self.platform)
        if preset:
            self._apply_preset_servers(preset)
        if self.auth_type == AuthType.APP_PASSWORD:
            if not self.credential:
                raise ValueError("credential is required for app_password auth")
            if not self.imap_server:
                raise ValueError("imap_server is required for platform 'imap'")
        return self

    def _apply_preset_servers(self, preset) -> None:
        """Fill imap/smtp hosts from preset, resolving NetEase by email domain."""
        if self.platform == "netease" and not self.imap_server:
            imap, smtp = netease_servers_for_email(str(self.email))
            self.imap_server = imap
            self.smtp_server = self.smtp_server or smtp
        self.imap_server = self.imap_server or preset.imap_server
        self.smtp_server = self.smtp_server or preset.smtp_server
        self.imap_port = self.imap_port or preset.imap_port
        self.smtp_port = self.smtp_port or preset.smtp_port


class AccountCreateRequest(BaseModel):
    """Persist a new mailbox account (app-password path)."""

    auth_type: AuthType = AuthType.APP_PASSWORD
    platform: str
    email: EmailStr
    credential: str = Field(..., description="App-specific password / authorization code")
    display_name: Optional[str] = None
    imap_server: Optional[str] = None
    imap_port: int = 993
    smtp_server: Optional[str] = None
    smtp_port: int = 465

    @model_validator(mode="after")
    def _apply_preset(self) -> "AccountCreateRequest":
        if self.auth_type != AuthType.APP_PASSWORD:
            raise ValueError(
                "This endpoint only supports app_password auth; "
                "OAuth accounts are created via /api/oauth/callback."
            )
        preset = PROVIDER_PRESETS.get(self.platform)
        if preset:
            self._apply_preset_servers(preset)
        if not self.imap_server:
            raise ValueError("imap_server is required for platform 'imap'")
        return self

    def _apply_preset_servers(self, preset) -> None:
        """Fill imap/smtp hosts from preset, resolving NetEase by email domain."""
        if self.platform == "netease" and not self.imap_server:
            imap, smtp = netease_servers_for_email(str(self.email))
            self.imap_server = imap
            self.smtp_server = self.smtp_server or smtp
        self.imap_server = self.imap_server or preset.imap_server
        self.smtp_server = self.smtp_server or preset.smtp_server
        self.imap_port = self.imap_port or preset.imap_port
        self.smtp_port = self.smtp_port or preset.smtp_port


class AccountOut(BaseModel):
    """Public account representation — never includes the credential."""

    id: int
    platform: str
    email: str
    display_name: str
    auth_type: AuthType
    imap_server: str
    imap_port: int
    smtp_server: str
    smtp_port: int
    sync_status: SyncStatus
    last_synced_at: Optional[datetime] = None
    last_error: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}


class SyncResult(BaseModel):
    account_id: int
    synced: int
    status: SyncStatus
    error: str = ""
