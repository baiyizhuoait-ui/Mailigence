"""Background historical-mail import.

Runs as an in-process asyncio background task (no Celery broker needed at this
stage). The flow:

1. API creates an ``ImportJob`` row (status=pending) and calls ``manager.start``.
2. The manager spawns a task that: resolves the credential, opens IMAP,
   ``SEARCH SINCE <since>`` to discover ``total``, then fetches+upserts in
   batches of ``PROGRESS_BATCH``, committing ``processed`` after each batch so
   the frontend progress bar can poll ``GET /api/import-jobs/{id}``.
3. Cancellation is cooperative: a flag is checked between batches, so a running
   IMAP FETCH round-trip finishes cleanly before stopping.

On server restart, ``reconcile_orphans`` marks any interrupted running/pending
jobs as failed so the UI never shows a stuck "running" job.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.email import UnifiedEmail
from app.models.email_account import EmailAccount, SyncStatus
from app.models.import_job import ImportJob, ImportStatus
from app.services.imap_client import open_connection
from app.services.mail_sync import apply_read_flags, resolve_credential, upsert_mails

# UIDs fetched + upserted per progress tick. Smaller = finer progress granularity.
PROGRESS_BATCH = 50


class ImportJobManager:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._cancel_flags: set[int] = set()

    # -- lifecycle -----------------------------------------------------------

    def start(self, job_id: int, account_id: int, since: date) -> None:
        task = asyncio.create_task(self._run(job_id, account_id, since), name=f"import-{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, jid=job_id: self._tasks.pop(jid, None))

    def request_cancel(self, job_id: int) -> None:
        self._cancel_flags.add(job_id)

    def is_running(self, job_id: int) -> bool:
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    # -- worker --------------------------------------------------------------

    async def _run(self, job_id: int, account_id: int, since: date) -> None:
        async with SessionLocal() as db:
            job = await db.get(ImportJob, job_id)
            account = await db.get(EmailAccount, account_id)
            if job is None or account is None:
                return
            job.status = ImportStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)
            account.sync_status = SyncStatus.SYNCING
            await db.commit()

            try:
                credential = await resolve_credential(account)
                client = await open_connection(account, credential)
                try:
                    await asyncio.to_thread(client.select_folder, "INBOX")
                    uids = await asyncio.to_thread(client.search_since, since)
                    job.total = len(uids)
                    await db.commit()

                    # --- Skip already-imported messages ---
                    # Fetch only Message-ID headers (very fast, ~100 bytes/msg)
                    # and filter out UIDs whose Message-ID already exists in DB.
                    # This avoids downloading full RFC822 for duplicates.
                    if uids:
                        uid_to_mid = await asyncio.to_thread(
                            client.fetch_message_ids, uids
                        )
                        existing_mids = await _existing_message_ids(
                            db, account_id
                        )
                        existing_uids = [
                            u
                            for u in uids
                            if uid_to_mid.get(u, f"synthetic:{u}")
                            in existing_mids
                        ]
                        new_uids = [
                            u
                            for u in uids
                            if uid_to_mid.get(u, f"synthetic:{u}")
                            not in existing_mids
                        ]
                        skipped = len(uids) - len(new_uids)
                        if skipped:
                            job.total = len(new_uids)
                            await db.commit()
                        # Existing rows were deduped out of the full fetch —
                        # reconcile their read state from live FLAGS so a
                        # re-import corrects stale is_read values.
                        if existing_uids:
                            flags = await asyncio.to_thread(
                                client.fetch_flags, existing_uids
                            )
                            await apply_read_flags(
                                db, account_id, uid_to_mid, flags
                            )
                        uids = new_uids

                    processed = 0
                    for i in range(0, len(uids), PROGRESS_BATCH):
                        if job_id in self._cancel_flags:
                            self._cancel_flags.discard(job_id)
                            job.status = ImportStatus.CANCELLED
                            job.finished_at = datetime.now(timezone.utc)
                            account.sync_status = SyncStatus.IDLE
                            await db.commit()
                            return

                        batch = uids[i : i + PROGRESS_BATCH]
                        mails = await asyncio.to_thread(client.fetch_normalised, batch)
                        await upsert_mails(db, account, mails)
                        processed += len(batch)
                        job.processed = processed
                        await db.commit()
                finally:
                    await asyncio.to_thread(client.logout)

                job.status = ImportStatus.COMPLETED
                job.finished_at = datetime.now(timezone.utc)
                account.last_synced_at = datetime.now(timezone.utc)
                account.sync_status = SyncStatus.IDLE
                account.last_error = ""
                await db.commit()

            except Exception as exc:
                job.status = ImportStatus.FAILED
                job.error = str(exc)[:500]
                job.finished_at = datetime.now(timezone.utc)
                account.sync_status = SyncStatus.ERROR
                account.last_error = str(exc)[:500]
                await db.commit()
                return

            # Kick off AI analysis for the freshly imported mail. Runs in the
            # background; failures here must not affect the completed import.
            try:
                from app.services.analysis_service import manager as analysis_manager

                analysis_manager.start(account_id)
            except Exception:
                pass


# Module-level singleton (single FastAPI worker). For multi-worker deployments,
# replace with a shared broker (Celery/RQ) + a real job table (already present).
manager = ImportJobManager()


async def _existing_message_ids(db: AsyncSession, account_id: int) -> set[str]:
    """Return the set of message_ids already stored for this account.

    Used to skip re-downloading messages that were previously imported.
    """
    result = await db.execute(
        select(UnifiedEmail.message_id).where(
            UnifiedEmail.account_id == account_id
        )
    )
    return {row[0] for row in result.all() if row[0]}


async def reconcile_orphans(db: AsyncSession) -> int:
    """Mark jobs left running/pending by a crash/restart as failed.

    Returns the number of reconciled jobs. Called on app startup.
    """
    result = await db.execute(
        update(ImportJob)
        .where(ImportJob.status.in_([ImportStatus.PENDING, ImportStatus.RUNNING]))
        .values(status=ImportStatus.FAILED, error="Interrupted by server restart", finished_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return result.rowcount or 0


# --- query helpers (used by API) --------------------------------------------

async def get_job(db: AsyncSession, job_id: int) -> ImportJob | None:
    return await db.get(ImportJob, job_id)


async def latest_job_for_account(db: AsyncSession, account_id: int) -> ImportJob | None:
    result = await db.execute(
        select(ImportJob)
        .where(ImportJob.account_id == account_id)
        .order_by(ImportJob.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def has_running_import(db: AsyncSession, account_id: int) -> bool:
    result = await db.execute(
        select(ImportJob)
        .where(
            ImportJob.account_id == account_id,
            ImportJob.status.in_([ImportStatus.PENDING, ImportStatus.RUNNING]),
        )
        .limit(1)
    )
    return result.scalars().first() is not None
