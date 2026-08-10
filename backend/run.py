"""Windows-friendly launcher for the Mailigence backend.

Python 3.13 on Windows defaults to the ``ProactorEventLoop``, which psycopg v3
(and asyncpg) cannot use in async mode. uvicorn also tries to set up its own
event loop during ``uvicorn.run``, which can override the policy we pinned.

This launcher:
* pins the ``WindowsSelectorEventLoopPolicy`` on Windows,
* imports the ``app`` object directly (never re-imports it via a string),
* drives ``uvicorn.Server.serve()`` on a loop we created — bypassing uvicorn's
  loop setup entirely, so the selector loop is guaranteed to survive.

Run on any platform (works identically on macOS/Linux):

    python run.py
"""
from __future__ import annotations

import asyncio
import sys


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    from app.config import settings
    from app.main import app

    import uvicorn

    config = uvicorn.Config(
        app,  # app object — no string re-import, no uvicorn loop override
        host=settings.app_host,
        port=settings.app_port,
        loop="asyncio",
        reload=False,
    )
    server = uvicorn.Server(config)
    # asyncio.run() creates the loop via the policy set above -> SelectorEventLoop.
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
