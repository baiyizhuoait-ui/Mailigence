"""Mail sync orchestration.

Responsibilities:
* Resolve a usable credential for an account (decrypt app password, or refresh
  an OAuth2 access token — cached in memory to limit refresh calls).
* Open an IMAP connection, search, fetch, and *upsert* normalised mails into the
  ``unified_emails`` table so re-syncing is idempotent.
* Update ``EmailAccount.sync_status`` / ``last_synced_at`` / ``last_error``.

The AI analysis step (Stage 3) will hook in after upsert, reading the freshly
stored rows — kept out of Stage 1 to keep concerns isolated.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import UnifiedEmail
from app.models.email_account import AuthType, EmailAccount, SyncStatus
from app.services import crypto, oauth
from app.services.imap_client import ImapClient, NormalisedMail, open_connection


# --- credential resolution --------------------------------------------------

# account_id -> (access_token, expires_at_epoch). In-memory only.
_access_cache: dict[int, tuple[str, float]] = {}


async def resolve_credential(account: EmailAccount) -> str:
    """Return a plaintext credential usable for one IMAP connection.

    * APP_PASSWORD: decrypt the stored secret.
    * OAUTH_*: decrypt the stored refresh token, then (refresh &) cache the
      resulting access token for ~its lifetime.
    """
    if account.auth_type == AuthType.APP_PASSWORD:
        return crypto.decrypt(account.credential_secret)

    provider = oauth.oauth_provider_for_platform(account.platform) or ""
    if not provider:
        raise RuntimeError(
            f"OAuth account on platform {account.platform!r} has no provider mapping"
        )

    cached = _access_cache.get(account.id)
    now = time.time()
    if cached and cached[1] - now > 60:  # 60s safety margin
        return cached[0]

    refresh_token = crypto.decrypt(account.credential_secret)
    token_resp = await oauth.refresh_oauth_access_token(provider, refresh_token)
    access_token = token_resp["access_token"]
    expires_in = int(token_resp.get("expires_in", 3600))
    _access_cache[account.id] = (access_token, now + expires_in)
    return access_token


# --- upsert -----------------------------------------------------------------

async def upsert_mails(db: AsyncSession, account: EmailAccount, mails: list[NormalisedMail]) -> int:
    if not mails:
        return 0
    rows = []
    for m in mails:
        rows.append({
            "account_id": account.id,
            "platform": account.platform,
            "message_id": m.message_id,
            "thread_id": m.thread_id,
            "direction": m.direction,
            "sender": m.sender,
            "sender_email": m.sender_email,
            "recipients": m.recipients,
            "subject": m.subject,
            "body_snippet": m.body_snippet,
            "received_at": m.received_at,
            "is_read": m.is_read,
            "has_reply": False,
            "raw_headers": m.raw_headers,
        })

    stmt = insert(UnifiedEmail).values(rows)
    # On conflict, refresh volatile fields (read state, snippet) but never
    # clobber AI analysis columns once populated.
    update_cols = {
        "is_read": stmt.excluded.is_read,
        "body_snippet": stmt.excluded.body_snippet,
        "sender": stmt.excluded.sender,
        "recipients": stmt.excluded.recipients,
        # Backfill received_at if a previous import left it NULL (e.g. an older
        # build failed to parse INTERNALDATE). COALESCE keeps any existing
        # non-null value so we never clobber a correct timestamp.
        "received_at": func.coalesce(UnifiedEmail.received_at, stmt.excluded.received_at),
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_account_message",
        set_=update_cols,
    )

    await db.execute(stmt)
    await db.commit()
    # rowcount is unreliable across dialects for bulk upsert; every row was
    # processed (inserted or updated), so report the input count.
    return len(rows)


async def apply_read_flags(
    db: AsyncSession,
    account_id: int,
    uid_to_mid: dict[str, str],
    flags: dict[str, bool],
) -> int:
    """Reconcile ``is_read`` for messages that were already in the database.

    ``uid_to_mid`` maps IMAP UIDs to stored Message-IDs (as returned by
    ``fetch_message_ids``); ``flags`` maps UIDs to their live ``\\Seen`` state.
    Only rows matching an existing Message-ID are touched, so this never
    creates or deletes anything.
    """
    if not flags:
        return 0
    updated = 0
    for uid, is_read in flags.items():
        mid = uid_to_mid.get(uid)
        if not mid:
            continue
        result = await db.execute(
            update(UnifiedEmail)
            .where(
                UnifiedEmail.account_id == account_id,
                UnifiedEmail.message_id == mid,
            )
            .values(is_read=is_read)
        )
        updated += result.rowcount or 0
    if updated:
        await db.commit()
    return updated


# --- public API -------------------------------------------------------------

async def test_connection(
    *, auth_type: AuthType, platform: str, email_addr: str, imap_server: str,
    imap_port: int, credential: str,
) -> bool:
    """Validate that a credential can authenticate against the IMAP server.

    Used by the "add account" flow before persisting anything. Raises on failure.
    """
    client = ImapClient(imap_server, imap_port)
    await asyncio.to_thread(client.connect)
    try:
        if auth_type == AuthType.APP_PASSWORD:
            await asyncio.to_thread(client.login_app_password, email_addr, credential)
        else:
            await asyncio.to_thread(client.login_oauth2, email_addr, credential)
        await asyncio.to_thread(client.select_folder, "INBOX")
        return True
    finally:
        await asyncio.to_thread(client.logout)


async def sync_account(
    db: AsyncSession,
    account: EmailAccount,
    *,
    since: Optional[date] = None,
    limit: Optional[int] = None,
) -> int:
    """Pull new mail for one account since ``since`` (default: last 7 days).

    Returns the number of upserted rows. Updates account sync metadata.
    """
    if since is None:
        since = date.today() - timedelta(days=7)

    await _set_status(db, account, SyncStatus.SYNCING, error="")
    try:
        credential = await resolve_credential(account)
        client = await open_connection(account, credential)
        try:
            await asyncio.to_thread(client.select_folder, "INBOX")
            uids = await asyncio.to_thread(client.search_since, since)
            if limit:
                uids = uids[-limit:]  # most recent N

            # Skip already-imported messages: fetch only Message-ID headers
            # (fast), then filter out UIDs whose Message-ID is already in DB.
            if uids:
                uid_to_mid = await asyncio.to_thread(
                    client.fetch_message_ids, uids
                )
                existing_result = await db.execute(
                    select(UnifiedEmail.message_id).where(
                        UnifiedEmail.account_id == account.id
                    )
                )
                existing_mids = {r[0] for r in existing_result.all() if r[0]}
                existing_uids = [
                    u
                    for u in uids
                    if uid_to_mid.get(u, f"synthetic:{u}") in existing_mids
                ]
                uids = [
                    u
                    for u in uids
                    if uid_to_mid.get(u, f"synthetic:{u}") not in existing_mids
                ]

            mails: list[NormalisedMail] = []
            if uids:
                mails = await asyncio.to_thread(client.fetch_normalised, uids)
            # Messages already in the DB were deduped out of the full fetch,
            # so their is_read may be stale — reconcile it from live FLAGS.
            if existing_uids:
                flags = await asyncio.to_thread(client.fetch_flags, existing_uids)
                await apply_read_flags(db, account.id, uid_to_mid, flags)
        finally:
            await asyncio.to_thread(client.logout)

        count = await upsert_mails(db, account, mails)
        account.last_synced_at = datetime.now(timezone.utc)
        account.sync_status = SyncStatus.IDLE
        account.last_error = ""
        await db.commit()
        return count
    except Exception as exc:
        account.sync_status = SyncStatus.ERROR
        account.last_error = str(exc)[:500]
        await db.commit()
        raise


async def _set_status(db: AsyncSession, account: EmailAccount, status: SyncStatus, *, error: str = "") -> None:
    account.sync_status = status
    if error:
        account.last_error = error
    await db.commit()


async def list_accounts(db: AsyncSession) -> list[EmailAccount]:
    result = await db.execute(select(EmailAccount).order_by(EmailAccount.created_at.desc()))
    return list(result.scalars().all())
