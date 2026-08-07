"""FastAPI application entry point.

Run (dev):
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import accounts, ads, dashboard, emails, idle, import_jobs, oauth, replies, reports
from app.api.settings import router as settings_router
from app.config import settings
from app.database import SessionLocal, init_db
from app.models.email_account import EmailAccount
from app.services import mail_sync, reconcile_orphans
from app.services.idle_manager import manager as idle_manager

# Background sync interval for servers that don't support IMAP IDLE.
BACKGROUND_SYNC_SECONDS = 120


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
                    except Exception:
                        pass  # best-effort, don't crash the loop
        except Exception:
            pass
        await asyncio.sleep(BACKGROUND_SYNC_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on first start (Stage 1 convenience; use Alembic later).
    await init_db()
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
    yield
    # Stop all IDLE listeners on shutdown.
    idle_manager.stop_all()
    sync_task.cancel()


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


app.include_router(accounts.router)
app.include_router(oauth.router)
app.include_router(emails.router)
app.include_router(import_jobs.router)
app.include_router(dashboard.router)
app.include_router(ads.router)
app.include_router(ads.blocked_senders_router)
app.include_router(reports.router)
app.include_router(replies.router)
app.include_router(idle.router)
app.include_router(settings_router)
