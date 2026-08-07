"""OAuth2 flow endpoints (Gmail / Outlook).

Two-step authorization code flow:
1. ``GET /api/oauth/start?provider=google`` -> returns an authorization URL the
   frontend opens. ``state`` encodes the chosen platform so the callback can
   persist the right account type.
2. ``GET /api/oauth/callback/{provider}?code=...&state=...`` -> exchanges the
   code for tokens, encrypts the refresh token, creates the EmailAccount, then
   redirects to the frontend accounts page.

Stage 1 keeps the universal app-password path as the primary flow; OAuth is an
opt-in enhancement that activates only when client id/secret are configured.
"""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.email_account import AuthType, EmailAccount
from app.services import crypto, oauth
from app.services.oauth import PROVIDER_PRESETS

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# In-memory state store (single-process dev). For multi-worker deploys, move to
# a signed/short-lived store (e.g. DB row or JWT). state -> {"platform": ...}
_state_store: dict[str, dict] = {}


@router.get("/start")
async def oauth_start(provider: str, platform: str | None = None) -> dict:
    if provider not in oauth.OAUTH_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    plat = platform or ("gmail" if provider == "google" else "outlook")
    state = secrets.token_urlsafe(24)
    _state_store[state] = {"provider": provider, "platform": plat}
    url = oauth.get_oauth_authorization_url(provider, state)
    return {"authorization_url": url, "state": state}


@router.get("/callback/{provider}")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    stored = _state_store.pop(state, None)
    if not stored or stored.get("provider") != provider:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    try:
        tokens = await oauth.exchange_code_for_tokens(provider, code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {exc}")

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token or not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh_token returned. Re-consent is required (prompt=consent).",
        )

    # Resolve the mailbox address from the id token (best-effort).
    user_email = _email_from_id_token(tokens.get("id_token")) or ""

    platform = stored["platform"]
    preset = PROVIDER_PRESETS.get(platform)
    if not preset:
        raise HTTPException(status_code=400, detail=f"Unknown platform {platform}")

    auth_type = AuthType.OAUTH_GOOGLE if provider == "google" else AuthType.OAUTH_MICROSOFT

    account = EmailAccount(
        platform=platform,
        email=user_email,
        auth_type=auth_type,
        credential_secret=crypto.encrypt(refresh_token),
        imap_server=preset.imap_server,
        imap_port=preset.imap_port,
        smtp_server=preset.smtp_server,
        smtp_port=preset.smtp_port,
        display_name="",
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    # Back to the frontend accounts page (carry the new account id for UX).
    target = settings.cors_origins[0].rstrip("/") + f"/accounts?added={account.id}"
    return RedirectResponse(url=target)


def _email_from_id_token(id_token: str | None) -> str:
    """Extract the email claim from a JWT id_token without verifying signature.

    Used only to label the account; auth is handled separately by IMAP XOAUTH2,
    so signature verification is not required at this step.
    """
    import base64
    import json

    if not id_token or id_token.count(".") != 2:
        return ""
    try:
        payload_b64 = id_token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return str(payload.get("email") or "").lower()
    except Exception:
        return ""
