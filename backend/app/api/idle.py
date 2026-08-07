"""IDLE monitoring API — start/stop/status for real-time mail push."""
from __future__ import annotations

from fastapi import APIRouter

from app.services.idle_manager import manager as idle_manager

router = APIRouter(prefix="/api/idle", tags=["idle"])


@router.post("/{account_id}/start")
async def start_idle(account_id: int) -> dict:
    """Start IDLE monitoring for an account.

    Returns ``{"started": true}`` if monitoring was started, or
    ``{"started": false}`` if it was already running.
    """
    started = await idle_manager.start(account_id)
    return {"started": started, "account_id": account_id}


@router.post("/{account_id}/stop")
async def stop_idle(account_id: int) -> dict:
    """Stop IDLE monitoring for an account."""
    stopped = idle_manager.stop(account_id)
    return {"stopped": stopped, "account_id": account_id}


@router.get("/{account_id}/status")
async def idle_status(account_id: int) -> dict:
    """Get IDLE monitoring status for a single account."""
    return idle_manager.get_status(account_id)


@router.get("/status")
async def all_idle_status() -> dict:
    """Get IDLE monitoring status for all accounts."""
    return idle_manager.get_all_status()
