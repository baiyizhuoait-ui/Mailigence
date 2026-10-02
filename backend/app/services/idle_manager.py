"""IMAP IDLE manager — real-time mail push (RFC 2177).

Runs one background asyncio task per account. Each task opens a dedicated
IMAP connection, issues IDLE, and blocks on server-pushed notifications.
When ``* N EXISTS`` / ``* N RECENT`` arrives, it triggers a normal sync
(re-using the existing ``sync_account`` flow on a *separate* connection so
the IDLE listener stays connected) and then kicks off AI analysis.

Each connection lives for one poll window (``IDLE_POLL_TIMEOUT``): once a
read times out, imaplib's buffered file object is unusable ("cannot read
from timed out object"), so the task ends the cycle and reconnects fresh —
no backoff, since a poll timeout is normal operation, not an error. Every
(re)connect also runs a catch-up sync, so mail that arrived while no IDLE
connection was up is picked up within one poll window at the latest; the
``last_sync_at`` status stamp lets the background poller skip accounts
whose IDLE loop is demonstrably alive.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.email_account import EmailAccount
from app.services.imap_client import ImapClient, open_connection
from app.services.mail_sync import resolve_credential, sync_account

_log = logging.getLogger(__name__)

# How long wait_idle_event blocks before returning (lets us check stop flag).
# After this timeout the connection's buffered file is unusable, so this is
# also the maximum lifetime of one IDLE connection.
IDLE_POLL_TIMEOUT = 120
# Back-off between reconnection attempts on error.
RECONNECT_DELAY = 30


class IdleManager:
    """Manages per-account IDLE listener tasks."""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._stop_flags: set[int] = set()
        self._status: dict[int, dict] = {}

    # -- public API -----------------------------------------------------------

    async def start(self, account_id: int) -> bool:
        """Start IDLE monitoring for an account. Returns False if already
        running, or if the account can't use IMAP IDLE (Microsoft OAuth
        accounts read via Graph and rely on the background polling loop)."""
        async with SessionLocal() as db:
            account = await db.get(EmailAccount, account_id)
        if account is None:
            return False
        # Microsoft Graph accounts have no IMAP scope — polling only.
        # The marker string is localized by the frontend (see idle.pollingOnly).
        if account.auth_type == "oauth_microsoft":
            self._status[account_id] = {
                "running": False,
                "last_event_at": None,
                "last_sync_at": None,
                "events": 0,
                "last_sync_count": 0,
                "error": "polling_only",
            }
            return False

        existing = self._tasks.get(account_id)
        if existing and not existing.done():
            return False
        self._stop_flags.discard(account_id)
        self._status[account_id] = {
            "running": True,
            "last_event_at": None,
            "last_sync_at": None,
            "events": 0,
            "last_sync_count": 0,
            "error": "",
        }
        task = asyncio.create_task(
            self._run(account_id), name=f"idle-{account_id}"
        )
        self._tasks[account_id] = task
        task.add_done_callback(lambda _, aid=account_id: self._on_done(aid))
        _log.info("IDLE started for account %d", account_id)
        return True

    def stop(self, account_id: int) -> bool:
        """Request IDLE monitoring to stop. Returns True if it was running."""
        if account_id not in self._tasks:
            return False
        self._stop_flags.add(account_id)
        _log.info("IDLE stop requested for account %d", account_id)
        return True

    def stop_all(self) -> None:
        """Stop all IDLE listeners (called on app shutdown)."""
        for aid in list(self._tasks.keys()):
            self._stop_flags.add(aid)

    def get_status(self, account_id: int) -> dict:
        return self._status.get(
            account_id,
            {"running": False, "last_event_at": None, "last_sync_at": None,
             "events": 0, "last_sync_count": 0, "error": ""},
        )

    def get_all_status(self) -> dict[int, dict]:
        return dict(self._status)

    # -- internals ------------------------------------------------------------

    def _on_done(self, account_id: int) -> None:
        self._tasks.pop(account_id, None)
        if account_id in self._status:
            self._status[account_id]["running"] = False
        _log.info("IDLE task finished for account %d", account_id)

    async def _run(self, account_id: int) -> None:
        """Main IDLE loop for one account — reconnects on failure."""
        while account_id not in self._stop_flags:
            try:
                await self._run_one_cycle(account_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _log.error("IDLE error for account %d: %s", account_id, exc)
                self._set_error(account_id, str(exc)[:200])
                # Wait before reconnecting (unless we're stopping).
                for _ in range(RECONNECT_DELAY):
                    if account_id in self._stop_flags:
                        break
                    await asyncio.sleep(1)

    async def _run_one_cycle(self, account_id: int) -> None:
        """One connect → catch-up sync → IDLE listen → disconnect cycle.

        The cycle ends after one poll window without an event: the read
        timeout leaves imaplib's buffered file unusable, so the connection
        is retired instead of waiting for the next call to explode. ``_run``
        immediately reconnects (no backoff — a timeout is normal operation).
        """
        async with SessionLocal() as db:
            account = await db.get(EmailAccount, account_id)
        if account is None:
            self._set_error(account_id, "Account not found")
            self._stop_flags.add(account_id)
            return

        credential = await resolve_credential(account)
        client = await open_connection(account, credential)
        try:
            await asyncio.to_thread(client.select_folder, "INBOX")

            if not await asyncio.to_thread(client.has_idle_capability):
                self._set_error(account_id, "Server does not support IDLE")
                self._stop_flags.add(account_id)
                return

            _log.info("IDLE connected for account %d (%s)", account_id, account.email)
            self._set_error(account_id, "")

            # Catch-up sync on every (re)connect: picks up anything that
            # arrived while no IDLE connection was listening (e.g. during the
            # reconnect gap), and stamps last_sync_at so the background
            # poller can trust this account's freshness.
            await self._sync_new_mail(account_id)

            while account_id not in self._stop_flags:
                event = await self._idle_round(
                    client, account_id, IDLE_POLL_TIMEOUT
                )
                if account_id in self._stop_flags:
                    break
                if event and ("EXISTS" in event or "RECENT" in event):
                    self._record_event(account_id)
                    await self._sync_new_mail(account_id)
                    continue
                # Poll window elapsed with no event — the socket's buffered
                # file object is unusable after a read timeout (imaplib
                # limitation), so retire the connection; _run reconnects.
                return
        finally:
            await asyncio.to_thread(client.logout)

    async def _idle_round(
        self, client: ImapClient, account_id: int, max_wait: float
    ) -> str | None:
        """One IDLE start → wait → stop cycle.

        Blocks up to ``max_wait`` seconds for a server-pushed event. Returns
        the event line, or ``None`` when the window elapsed with no event
        (the caller must then retire the connection — see ``_run_one_cycle``).
        """
        await asyncio.to_thread(client.start_idle)
        try:
            return await asyncio.to_thread(client.wait_idle_event, max_wait)
        finally:
            await asyncio.to_thread(client.stop_idle)

    async def _sync_new_mail(self, account_id: int) -> None:
        """Sync new mail using a *separate* connection, then trigger AI analysis."""
        from datetime import date, timedelta

        try:
            async with SessionLocal() as db:
                account = await db.get(EmailAccount, account_id)
                if account is None:
                    return
                # Sync last 1 day — new mail just arrived.
                since = date.today() - timedelta(days=1)
                count = await sync_account(db, account, since=since)
                st = self._status.setdefault(account_id, {})
                st["last_sync_count"] = count
                # Stamp liveness so the background poller can skip this
                # account (see main._background_sync_loop).
                st["last_sync_at"] = datetime.now(timezone.utc).isoformat()
                _log.info(
                    "IDLE sync: %d new mail(s) for account %d", count, account_id
                )

            if count > 0:
                # Trigger AI analysis for the freshly imported mail.
                try:
                    from app.services.analysis_service import manager as analysis_mgr
                    analysis_mgr.start(account_id)
                except Exception as exc:
                    _log.warning("Could not start AI analysis: %s", exc)
        except Exception as exc:
            _log.error("IDLE sync failed for account %d: %s", account_id, exc)

    # -- status helpers -------------------------------------------------------

    def _record_event(self, account_id: int) -> None:
        st = self._status.setdefault(account_id, {})
        st["last_event_at"] = datetime.now(timezone.utc).isoformat()
        st["events"] = st.get("events", 0) + 1

    def _set_error(self, account_id: int, error: str) -> None:
        self._status.setdefault(account_id, {})["error"] = error


# Module-level singleton.
manager = IdleManager()
