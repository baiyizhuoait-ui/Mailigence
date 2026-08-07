"""EmailAccount model.

Stores per-mailbox connection metadata. Credentials (app password or OAuth2
refresh token) are stored *encrypted* in ``credential_secret`` via Fernet
(``app.services.crypto``). The plaintext credential never persists and is only
held in memory for the duration of an IMAP connection.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthType(str, enum.Enum):
    """How the account authenticates against IMAP."""

    APP_PASSWORD = "app_password"  # generic fallback: IMAP LOGIN w/ app-specific password
    OAUTH_GOOGLE = "oauth_google"  # Gmail via OAuth2 xoauth2
    OAUTH_MICROSOFT = "oauth_microsoft"  # Outlook via OAuth2 xoauth2


class SyncStatus(str, enum.Enum):
    IDLE = "idle"
    SYNCING = "syncing"
    ERROR = "error"


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Logical platform label surfaced in the UI. "imap" = generic provider.
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    auth_type: Mapped[AuthType] = mapped_column(
        Enum(AuthType, name="auth_type"), nullable=False
    )

    # Fernet-encrypted blob. For app_password -> the app password.
    # For OAuth* -> the refresh token (access tokens are cached in-memory only).
    credential_secret: Mapped[str] = mapped_column(Text, nullable=False)

    # IMAP connection params. OAuth accounts fill these from provider presets.
    imap_server: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)

    # SMTP (used later for reply tracking / sending). Optional in Stage 1.
    smtp_server: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=465)

    display_name: Mapped[str] = mapped_column(String(120), default="")

    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="sync_status"), default=SyncStatus.IDLE
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EmailAccount id={self.id} {self.platform}:{self.email}>"
