"""FastAPI application entry point.

Run (dev):
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api import (
    accounts,
    ads,
    categories,
    dashboard,
    emails,
    idle,
    import_jobs,
    memories,
    oauth,
    replies,
    reports,
)
from app.api.settings import router as settings_router
from app.config import settings
from app.database import SessionLocal, init_db
from app.models.email_account import EmailAccount
from app.services import mail_sync, reconcile_orphans
from app.services.idle_manager import manager as idle_manager

_log = logging.getLogger(__name__)

# Background sync interval for servers that don't support IMAP IDLE.
BACKGROUND_SYNC_SECONDS = 120
# How often to sweep for emails still waiting on AI analysis (new imports that
# exceeded one batch, or mails left uncategorized by a category delete).
ANALYSIS_SWEEP_SECONDS = 60


async def _analysis_sweep_loop() -> None:
    """Periodically find emails that need AI analysis and run it.

    ``run_analysis`` processes up to ANALYSIS_BATCH mails per run, so a large
    import can leave a backlog; this loop keeps working the queue every minute
    until it is empty. It also covers mails whose category was deleted (their
    ``category`` is NULL, which the analysis query treats as pending).
    """
    await asyncio.sleep(15)  # let startup settle
    while True:
        try:
            async with SessionLocal() as db:
                from app.services.analysis_service import pending_count
                pending = await pending_count(db)
            if pending > 0:
                from app.services.analysis_service import manager as analysis_mgr
                analysis_mgr.start(None)
        except Exception as exc:
            _log.error("Analysis sweep error: %s", exc)
        await asyncio.sleep(ANALYSIS_SWEEP_SECONDS)


async def _background_sync_loop() -> None:
    """Periodically sync all accounts as a fallback for non-IDLE servers."""
    await asyncio.sleep(10)  # let startup settle
    while True:
        try:
            async with SessionLocal() as db:
                result = await db.execute(select(EmailAccount))
                acct_list = list(result.scalars().all())
                since = date.today() - timedelta(days=1)
                for acct in acct_list:
                    # Skip accounts where IDLE is actively running.
                    status = idle_manager.get_status(acct.id)
                    if status.get("running") and not status.get("error"):
                        continue
                    try:
                        count = await mail_sync.sync_account(db, acct, since=since)
                        if count > 0:
                            from app.services.analysis_service import manager as analysis_mgr
                            analysis_mgr.start(acct.id)
                    except Exception as exc:
                        # Don't crash the loop, but surface the failure in the
                        # logs instead of silently swallowing it.
                        _log.warning(
                            "Background sync failed for account %s: %s",
                            acct.email, exc,
                        )
        except Exception as exc:
            _log.error("Background sync loop error: %s", exc)
        await asyncio.sleep(BACKGROUND_SYNC_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on first start (Stage 1 convenience; use Alembic later).
    try:
        await init_db()
    except Exception as exc:
        _log.error(
            "Database connection failed: %s\n"
            "Please make sure PostgreSQL is running and that DATABASE_URL in "
            "backend/.env is correct (see backend/.env.example).",
            exc,
        )
        # Fail fast with a clear message instead of an opaque traceback.
        raise SystemExit(1) from exc
    # Mark any import jobs orphaned by a crash/restart as failed so the UI
    # never shows a permanently "running" job.
    async with SessionLocal() as db:
        await reconcile_orphans(db)
        # Auto-start IDLE monitoring for all existing accounts so new mail
        # is pushed in real-time without manual intervention.
        result = await db.execute(select(EmailAccount))
        for acct in result.scalars().all():
            await idle_manager.start(acct.id)
    # Start background sync as a fallback for servers without IDLE support.
    sync_task = asyncio.create_task(_background_sync_loop())
    # Start the analysis sweep so unanalyzed/uncategorized mail is always
    # picked up automatically.
    analysis_task = asyncio.create_task(_analysis_sweep_loop())
    yield
    # Stop all IDLE listeners on shutdown.
    idle_manager.stop_all()
    sync_task.cancel()
    analysis_task.cancel()


app = FastAPI(
    title="Mailigence API",
    version="0.2.0",
    description="Multi-platform email aggregation & analysis backend (Stage 2).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "encryption_configured": settings.encryption_key_configured,
        "oauth_google_configured": bool(settings.google_oauth_client_id),
        "oauth_microsoft_configured": bool(settings.microsoft_oauth_client_id),
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return a readable error body instead of a bare 500, and log the traceback.

    Without this handler, FastAPI returns ``Internal Server Error`` with no
    detail, so the frontend can't surface why a request failed.
    """
    tb = traceback.format_exc()
    _log.error(
        "Unhandled error on %s %s: %s\n%s",
        request.method,
        request.url.path,
        exc,
        tb,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {exc}"},
    )


app.include_router(accounts.router)
app.include_router(oauth.router)
app.include_router(emails.router)
app.include_router(categories.router)
app.include_router(import_jobs.router)
app.include_router(dashboard.router)
app.include_router(ads.router)
app.include_router(ads.blocked_senders_router)
app.include_router(reports.router)
app.include_router(replies.router)
app.include_router(idle.router)
app.include_router(settings_router)
app.include_router(memories.router)
