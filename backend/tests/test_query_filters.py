"""Tests for chat-QA structured filters and the category-aggregate bypass.

Two layers:

* Pure-rule parsing (``query_filters``) — no DB, no LLM.
* DB integration (real PostgreSQL, same dev DB as test_search) — temp rows are
  inserted, verified, then cleaned up. Verifies that enumerative category
  questions ("今天有什么垃圾邮件，都是谁发的") take the direct-SQL bypass and
  NEVER hit ``search_emails``.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone

# psycopg async needs the Windows selector loop, same as run.py.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.email import MailDirection, UnifiedEmail  # noqa: E402
from app.services.ai_config import AiConfig  # noqa: E402
from app.services import chat_qa_service  # noqa: E402
from app.services.chat_qa_service import (  # noqa: E402
    _category_stats_material,
    chat_qa,
)
from app.services.query_filters import (  # noqa: E402
    has_enumeration_intent,
    parse_structured_filters,
)


def _today():
    return datetime.now(timezone.utc).date()


def test_parse_today_marketing():
    f = parse_structured_filters("今天有什么垃圾邮件，都是谁发的")
    assert f["category"] == "marketing"
    assert f["date_from"] == _today().isoformat()
    assert f["date_to"] == _today().isoformat()
    assert has_enumeration_intent("今天有什么垃圾邮件，都是谁发的")


def test_parse_this_week_meeting():
    f = parse_structured_filters("这周有哪些会议邮件")
    assert f["category"] == "meeting"
    monday = _today() - timedelta(days=_today().weekday())
    assert f["date_from"] == monday.isoformat()
    assert f["date_to"] == _today().isoformat()
    assert has_enumeration_intent("这周有哪些会议邮件")


def test_parse_last_month_finance():
    f = parse_structured_filters("上个月财务类邮件有几封分别是谁发的")
    assert f["category"] == "finance"
    first_this_month = _today().replace(day=1)
    last_last_month = first_this_month - timedelta(days=1)
    assert f["date_from"] == last_last_month.replace(day=1).isoformat()
    assert f["date_to"] == last_last_month.isoformat()
    assert has_enumeration_intent("上个月财务类邮件有几封分别是谁发的")


def test_parse_specific_query_has_no_filters():
    f = parse_structured_filters("张三的报价单在哪个邮箱")
    assert f == {"category": None, "date_from": None, "date_to": None}


def test_english_synonym_and_date():
    f = parse_structured_filters("show me junk emails from last week")
    assert f["category"] == "marketing"
    monday = _today() - timedelta(days=_today().weekday())
    assert f["date_from"] == (monday - timedelta(days=7)).isoformat()
    assert f["date_to"] == (monday - timedelta(days=1)).isoformat()


def test_search_intent_not_enumeration():
    # A content-search inside a category must NOT take the aggregate bypass —
    # it goes through filtered retrieval instead.
    assert not has_enumeration_intent("垃圾邮件里有没有Google的验证码")
    assert not has_enumeration_intent("张三发的发票")


# ---- DB integration ---------------------------------------------------------

def _insert_temp_mail(category: str, sender: str, sender_email: str, subject: str,
                      received_at: datetime | None = None):
    async def _run() -> int:
        async with SessionLocal() as db:
            row = UnifiedEmail(
                account_id=1,
                platform="test",
                message_id=f"qf-{uuid.uuid4().hex}",
                thread_id="",
                direction=MailDirection.INBOX,
                sender=sender,
                sender_email=sender_email,
                subject=subject,
                body_snippet="query-filter test body",
                received_at=received_at or datetime.now(timezone.utc),
                category=category,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id
    return asyncio.run(_run())


def _delete_mail(email_id: int) -> None:
    async def _run() -> None:
        async with SessionLocal() as db:
            row = await db.get(UnifiedEmail, email_id)
            if row:
                await db.delete(row)
                await db.commit()
    asyncio.run(_run())


def test_category_stats_material_aggregates_by_sender():
    # Insert into a far-past date window so real mailbox data can never
    # crowd the temp senders out of the top-15 aggregate.
    old = datetime(2001, 1, 1, 12, 0, tzinfo=timezone.utc)
    id_a1 = _insert_temp_mail("marketing", "Ads Sender A", "ads.a@example.com", "Buy now 1", received_at=old)
    id_a2 = _insert_temp_mail("marketing", "Ads Sender A", "ads.a@example.com", "Buy now 2", received_at=old)
    id_b = _insert_temp_mail("marketing", "Ads Sender B", "ads.b@example.com", "Sale ends", received_at=old)
    try:
        async def _run():
            async with SessionLocal() as db:
                return await _category_stats_material(
                    db, "marketing", "2001-01-01", "2001-01-01"
                )
        text = asyncio.run(_run())
        assert "ads.a@example.com" in text
        assert "ads.b@example.com" in text
        assert "共 3 封" in text  # exactly the 3 temp mails in this window
    finally:
        _delete_mail(id_a1)
        _delete_mail(id_a2)
        _delete_mail(id_b)


def test_bypass_skips_retrieval_and_injects_material(monkeypatch):
    """The enumerative question must go straight to SQL, never to search_emails."""
    search_calls: list[dict] = []
    captured: dict = {}

    async def _fake_search(db, q, limit=15, offset=0, **kwargs):
        search_calls.append({"q": q, **kwargs})
        return [], 0

    async def _fake_llm(cfg, system_prompt, user_content, local):
        captured["user_content"] = user_content
        return "按统计结果回答 ---CITED_IDS--- []"

    monkeypatch.setattr(chat_qa_service, "search_emails", _fake_search)
    monkeypatch.setattr(chat_qa_service, "_call_llm", _fake_llm)

    id_a1 = _insert_temp_mail("marketing", "Ads Sender A", "ads.a@example.com", "Buy now 1")
    try:
        cfg = AiConfig()  # use_ai=False → no planner/LLM network calls

        async def _run():
            async with SessionLocal() as db:
                return await chat_qa(db, cfg, "今天有什么垃圾邮件，都是谁发的")

        result = asyncio.run(_run())
        # 1. Retrieval was NOT used — the direct-SQL bypass handled it.
        assert search_calls == []
        # 2. The aggregate material reached the prompt as section (四).
        assert "（四）分类统计结果" in captured["user_content"]
        assert "ads.a@example.com" in captured["user_content"]
        assert result.answer.startswith("按统计结果回答")
    finally:
        _delete_mail(id_a1)


def test_filtered_retrieval_passes_filters(monkeypatch):
    """Category question WITHOUT enumeration intent → retrieval WITH filters."""
    search_calls: list[dict] = []

    async def _fake_search(db, q, limit=15, offset=0, **kwargs):
        search_calls.append({"q": q, **kwargs})
        return [], 0

    async def _fake_llm(cfg, system_prompt, user_content, local):
        return "无命中 ---CITED_IDS--- []"

    monkeypatch.setattr(chat_qa_service, "search_emails", _fake_search)
    monkeypatch.setattr(chat_qa_service, "_call_llm", _fake_llm)

    cfg = AiConfig()

    async def _run():
        async with SessionLocal() as db:
            return await chat_qa(db, cfg, "垃圾邮件里有没有Google的验证码")

    asyncio.run(_run())
    assert len(search_calls) >= 1
    assert search_calls[0]["category"] == "marketing"  # filter parsed by rules
    assert search_calls[0]["date_from"] is None  # no time phrase in the query


def test_prompt_injects_current_time(monkeypatch):
    """The QA prompt must carry today's UTC date so relative days (前天) map
    onto the UTC dates shown in materials — local and cloud models alike."""
    captured: dict = {}

    async def _fake_search(db, q, limit=15, offset=0, **kwargs):
        return [], 0

    async def _fake_llm(cfg, system_prompt, user_content, local):
        captured["user_content"] = user_content
        return "好的 ---CITED_IDS--- []"

    monkeypatch.setattr(chat_qa_service, "search_emails", _fake_search)
    monkeypatch.setattr(chat_qa_service, "_call_llm", _fake_llm)

    cfg = AiConfig()

    async def _run():
        async with SessionLocal() as db:
            return await chat_qa(db, cfg, "前天有什么重要邮件")

    asyncio.run(_run())
    prompt = captured["user_content"]
    assert "（〇）当前时间" in prompt
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert today in prompt
    yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    assert yesterday in prompt
