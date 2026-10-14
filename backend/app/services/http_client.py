"""Shared httpx.AsyncClient — reuses keep-alive TCP connections app-wide."""
from __future__ import annotations

import httpx

# Sized for the concurrent background sync + analysis tasks that run alongside
# API requests.
_limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
client = httpx.AsyncClient(limits=_limits)


async def aclose() -> None:
    """Close the shared client (called from the app lifespan on shutdown)."""
    await client.aclose()
