"""Windows-friendly launcher for the Mailigence backend.

Python 3.13 on Windows defaults to the ``ProactorEventLoop``, which psycopg v3
(and asyncpg) cannot use in async mode. uvicorn also forces
``ProactorEventLoop`` on Windows during startup, so simply calling
``uvicorn app.main:app`` fails with:

    Psycopg cannot use the 'ProactorEventLoop' to run in async mode

This launcher pins the ``SelectorEventLoop`` policy BEFORE uvicorn starts and
neutralizes uvicorn's loop setup (``uvicorn.loops.asyncio.asyncio_setup``), so
the selector loop survives.

Run on any platform (works identically on macOS/Linux):

    python run.py
"""
from __future__ import annotations

import asyncio
import sys


def _patch_windows_event_loop() -> None:
    if sys.platform != "win32":
        return
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        import uvicorn.loops.asyncio

        uvicorn.loops.asyncio.asyncio_setup = lambda **kwargs: None
    except ImportError:
        # uvicorn internals moved between versions — the policy set above is
        # still applied, which covers most cases.
        pass


def main() -> None:
    _patch_windows_event_loop()

    from app.config import settings

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
