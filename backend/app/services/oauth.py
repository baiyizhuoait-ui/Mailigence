"""OAuth2 helpers for Gmail (Google) and Outlook (Microsoft).

Both providers support OAuth2 over IMAP via the ``XOAUTH2`` SASL mechanism:
the auth string is ``user=<email>\\x01auth=Bearer <access_token>\\x01\\x01``.

This module only deals with obtaining/refreshing *access tokens*. The actual
IMAP ``authenticate("XOAUTH2", ...)`` call lives in ``imap_client.py`` so the
auth mechanism stays protocol-agnostic and testable.

Provider registration
---------------------
To use OAuth2 you must register an app and set the client id/secret in .env:
* Google Cloud Console -> create OAuth client (type: Web), add redirect URI
  ``<PUBLIC_BASE_URL>/api/oauth/callback/google``. Enable Gmail IMAP scope.
* Azure Portal -> App registrations -> Web platform, add redirect URI
  ``<PUBLIC_BASE_URL>/api/oauth/callback/microsoft``.

If these are left blank, the UI falls back to the app-specific-password flow,
which works for every IMAP provider and is the universal baseline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.config import settings


# --- IMAP/SMTP host presets per provider -------------------------------------

@dataclass(frozen=True)
class ProviderPreset:
    platform: str            # gmail | outlook | qq | netease163 | netease126 | netease188 | neteaseyeah | imap
    label: str
    imap_server: str
    imap_port: int
    smtp_server: str
    smtp_port: int
    supports_oauth: bool = False
    # Default domain suffix(es) for the email input. None = custom/unknown.
    domains: tuple[str, ...] = ()


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "gmail": ProviderPreset("gmail", "Gmail", "imap.gmail.com", 993, "smtp.gmail.com", 465, True, ("gmail.com",)),
    "outlook": ProviderPreset(
        "outlook", "Outlook / Hotmail", "outlook.office365.com", 993,
        "smtp.office365.com", 587, True, ("outlook.com", "hotmail.com", "live.com"),
    ),
    "qq": ProviderPreset("qq", "QQ邮箱", "imap.qq.com", 993, "smtp.qq.com", 465, False, ("qq.com", "vip.qq.com", "foxmail.com")),
    "netease": ProviderPreset(
        "netease", "网易邮箱", "", 993, "", 465, False,
        ("163.com", "126.com", "188.com", "yeah.net"),
    ),
    "yahoo": ProviderPreset(
        "yahoo", "Yahoo Mail", "imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465,
        False, ("yahoo.com", "ymail.com", "rocketmail.com"),
    ),
    "icloud": ProviderPreset(
        "icloud", "iCloud Mail", "imap.mail.me.com", 993, "smtp.mail.me.com", 465,
        False, ("icloud.com", "me.com", "mac.com"),
    ),
    "aol": ProviderPreset(
        "aol", "AOL Mail", "imap.aol.com", 993, "smtp.aol.com", 465,
        False, ("aol.com",),
    ),
    "zoho": ProviderPreset(
        "zoho", "Zoho Mail", "imap.zoho.com", 993, "smtp.zoho.com", 465,
        False, ("zoho.com", "zohomail.com"),
    ),
    "yandex": ProviderPreset(
        "yandex", "Yandex Mail", "imap.yandex.com", 993, "smtp.yandex.com", 465,
        False, ("yandex.com", "yandex.ru", "yandex.ua"),
    ),
}


# Per-domain IMAP/SMTP hosts for NetEase family (163/126/188/yeah).
# Used to resolve the right server from the email's domain.
NETEASE_SERVER_MAP: dict[str, tuple[str, str]] = {
    "163.com": ("imap.163.com", "smtp.163.com"),
    "126.com": ("imap.126.com", "smtp.126.com"),
    "188.com": ("imap.188.com", "smtp.188.com"),
    "yeah.net": ("imap.yeah.net", "smtp.yeah.net"),
}


def netease_servers_for_email(email_addr: str) -> tuple[str, str]:
    """Return (imap_server, smtp_server) for a NetEase email by its domain."""
    domain = (email_addr.rsplit("@", 1)[-1] if "@" in email_addr else "").lower()
    return NETEASE_SERVER_MAP.get(domain, ("", ""))


# --- OAuth2 scopes & endpoints -----------------------------------------------

OAUTH_PRESETS = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        # IMAP access + offline refresh token
        "scope": "https://mail.google.com/ openid email",
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "redirect_path": "/api/oauth/callback/google",
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        # Read mail via the Microsoft Graph REST API (no IMAP XOAUTH2 needed).
        "scope": "User.Read Mail.Read offline_access openid profile email",
        "client_id": settings.microsoft_oauth_client_id,
        "client_secret": settings.microsoft_oauth_client_secret,
        "redirect_path": "/api/oauth/callback/microsoft",
    },
}


def oauth_provider_for_platform(platform: str) -> Optional[str]:
    """Map a logical platform to an OAuth provider key, or None."""
    if platform == "gmail":
        return "google"
    if platform == "outlook":
        return "microsoft"
    return None


def get_oauth_authorization_url(provider: str, state: str) -> str:
    """Build the provider authorization URL the user must visit to consent."""
    cfg = OAUTH_PRESETS.get(provider)
    if not cfg:
        raise ValueError(f"Unknown OAuth provider: {provider}")
    if not cfg["client_id"]:
        raise RuntimeError(
            f"OAuth for {provider} is not configured. Set the client id/secret in .env "
            f"or use the app-specific-password flow instead."
        )
    redirect_uri = settings.public_base_url.rstrip("/") + cfg["redirect_path"]
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": cfg["scope"],
        "access_type": "offline",  # request a refresh token (Google)
        "prompt": "consent",
        "state": state,
    }
    return f"{cfg['auth_url']}?{urlencode(params)}"


async def exchange_code_for_tokens(provider: str, code: str) -> dict:
    """Exchange the authorization code for access + refresh tokens."""
    cfg = OAUTH_PRESETS[provider]
    redirect_uri = settings.public_base_url.rstrip("/") + cfg["redirect_path"]
    data = {
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(cfg["token_url"], data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"{provider} token exchange failed: {resp.text}")
        return resp.json()


async def refresh_oauth_access_token(provider: str, refresh_token: str) -> dict:
    """Refresh an expired access token using a stored refresh token.

    Returns ``{"access_token": ..., "expires_in": ...}``. The refresh token
    itself is NOT returned by Google on every refresh; callers must keep the
    stored one unless a new one is present.
    """
    cfg = OAUTH_PRESETS[provider]
    data = {
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(cfg["token_url"], data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"{provider} token refresh failed: {resp.text}")
        return resp.json()
