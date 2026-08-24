"""Basic tests for the full-text search endpoint (GET /api/search).

Runs against the configured local database via an in-process ASGI client, so
PostgreSQL must be reachable (the same DB the dev server uses). The tests
insert temporary emails, verify the search behaviour, then clean them up.

Run:  cd backend && .venv\\Scripts\\python -m pytest tests/test_search.py -v
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone

# psycopg async needs the Windows selector loop, same as run.py.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.email import MailDirection, UnifiedEmail  # noqa: E402
from app.api.search import highlight_snippet  # noqa: E402

BASE = "http://test"


async def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url=BASE, timeout=30)


async def _insert_temp_email(
    account_id: int, subject: str, body: str = "", sender: str = "Test Sender"
) -> int:
    async with SessionLocal() as db:
        row = UnifiedEmail(
            account_id=account_id,
            platform="test",
            message_id=f"test-{uuid.uuid4().hex}",
            thread_id="",
            direction=MailDirection.INBOX,
            sender=sender,
            sender_email="search.test@example.com",
            subject=subject,
            body_snippet=body,
            received_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def _delete_email(email_id: int) -> None:
    async with SessionLocal() as db:
        row = await db.get(UnifiedEmail, email_id)
        if row is not None:
            await db.delete(row)
            await db.commit()


async def _first_account_id() -> int:
    async with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.email_account import EmailAccount

        row = (await db.execute(select(EmailAccount.id).limit(1))).scalar_one_or_none()
    if row is None:
        pytest.skip("no email accounts configured — cannot run DB-backed tests")
    return row


# ---- endpoint behaviour ----------------------------------------------------


def test_empty_query_returns_400():
    async def _run():
        async with await _client() as client:
            r1 = await client.get("/api/search", params={"q": ""})
            r2 = await client.get("/api/search", params={"q": "   "})
        assert r1.status_code == 400
        assert r2.status_code == 400

    asyncio.run(_run())


def test_special_chars_do_not_error():
    async def _run():
        async with await _client() as client:
            resp = await client.get("/api/search", params={"q": "100%_ok'quote"})
        # Must be a clean 200 with the expected shape — never a 500.
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data and "results" in data

    asyncio.run(_run())


def test_invalid_date_returns_400():
    async def _run():
        async with await _client() as client:
            resp = await client.get("/api/search", params={"q": "x", "date_from": "not-a-date"})
        assert resp.status_code == 400

    asyncio.run(_run())


def test_account_filter_isolates_results():
    async def _run():
        account_a = await _first_account_id()
        subject = f"zzsearchaccount{uuid.uuid4().hex[:8]}"
        email_id = await _insert_temp_email(account_a, subject)

        try:
            async with await _client() as client:
                # Unfiltered — must contain the temp email.
                all_resp = await client.get("/api/search", params={"q": subject})
                assert all_resp.status_code == 200
                ids = [r["id"] for r in all_resp.json()["results"]]
                assert email_id in ids

                # Filtered to a non-existent account — the temp email must not leak.
                filtered = await client.get(
                    "/api/search", params={"q": subject, "account_id": 999_999_999}
                )
                assert filtered.status_code == 200
                assert filtered.json()["total"] == 0

                # Filtered to the owning account — must still be found.
                owned = await client.get(
                    "/api/search", params={"q": subject, "account_id": account_a}
                )
                owned_ids = [r["id"] for r in owned.json()["results"]]
                assert email_id in owned_ids
        finally:
            await _delete_email(email_id)

    asyncio.run(_run())


def test_chinese_keyword_matches_chinese_email():
    async def _run():
        account = await _first_account_id()
        subject = "会议通知测试邮件唯一标识XYZ"
        body = "本周三下午三点召开项目评审会议，请准时参加。"
        email_id = await _insert_temp_email(account, subject, body)

        try:
            async with await _client() as client:
                resp = await client.get("/api/search", params={"q": "会议通知"})
                assert resp.status_code == 200
                hits = [r["id"] for r in resp.json()["results"]]
                assert email_id in hits
        finally:
            await _delete_email(email_id)

    asyncio.run(_run())


# ---- highlight unit tests --------------------------------------------------


def test_highlight_wraps_keyword_and_escapes_html():
    snippet = highlight_snippet("", 'hello <script>alert(1)</script> hello world', "hello")
    # Keyword hits are wrapped in <mark>.
    assert "<mark>" in snippet and "hello" in snippet
    # Raw HTML must be escaped, not passed through.
    assert "<script>" not in snippet
    assert "&lt;script&gt;" in snippet


def test_highlight_empty_source_returns_empty():
    assert highlight_snippet("", "", "anything") == ""


def test_highlight_uses_subject_when_body_empty():
    snippet = highlight_snippet("Meeting today", "", "today")
    assert "<mark>today</mark>" in snippet
