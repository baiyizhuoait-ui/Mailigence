"""AI-powered cross-mailbox chat QA.

Given a natural-language question, retrieves the most relevant email
fragments (pg_trgm similarity search) plus optional mailbox statistics, and
asks the configured LLM to answer strictly from that material — never
inventing facts outside the provided fragments.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.email import UnifiedEmail
from app.models.email_account import EmailAccount
from app.services.ai_analyzer import _anthropic_chat, _openai_chat
from app.services.ai_config import AiConfig
from app.services.email_search import search_emails

SYSTEM_PROMPT = """你是用户的邮件助理。你只能根据下面提供的邮件片段和统计数据回答问题，严禁使用片段之外的信息编造答案。
如果提供的邮件片段不足以回答问题，必须明确告诉用户'没有找到足够的相关邮件来回答这个问题'，不要猜测或编造。
回答要简洁、直接给结论，必要时再展开细节。
最后必须输出一个 JSON 字段列出你在回答中实际引用到的邮件 ID，格式：
---CITED_IDS---
["id1", "id2"]"""

USER_PROMPT_TEMPLATE = """用户问题：{query}\n\n（如有统计数据）相关统计：{stats_context}\n\n相关邮件片段：\n{fragments}\n\n对话历史（如有，最近3轮）：\n{history}\n\n请回答用户问题。"""

CITED_MARKER = "---CITED_IDS---"
RETRIEVAL_LIMIT = 15
SNIPPET_MAX_CHARS = 200
HISTORY_ROUNDS = 3
# Thinking models spend many tokens on reasoning before answering; give them
# room plus time so the answer and the ---CITED_IDS--- trailer survive.
MAX_TOKENS = 2048
TIMEOUT_SECONDS = 120

# Appended to the system prompt when semantic (embedding) recall is off, so the
# model sets honest expectations instead of pretending it saw everything.
_NO_EMBEDDING_NOTE = (
    "\n\n（当前使用关键词检索，可能存在语义相关但未命中的邮件。）"
)

_STATS_QUERY_RE = re.compile(r"(多少|几条|几封|统计|数量|几个|多少封|多少条)")


@dataclass
class ChatResult:
    answer: str
    cited_ids: list[str]


def _detect_stats_query(query: str) -> bool:
    """True when the question asks about aggregate mailbox statistics."""
    return bool(_STATS_QUERY_RE.search(query or ""))


async def _compute_stats(db) -> str:
    """Aggregate mailbox statistics into a multi-line summary string."""
    total = await db.scalar(select(func.count()).select_from(UnifiedEmail))
    unread = await db.scalar(
        select(func.count()).select_from(UnifiedEmail).where(UnifiedEmail.is_read.is_(False))
    )
    since = datetime.now(timezone.utc) - timedelta(days=7)
    recent = await db.scalar(
        select(func.count())
        .select_from(UnifiedEmail)
        .where(UnifiedEmail.received_at >= since)
    )
    by_category = (
        await db.execute(
            select(UnifiedEmail.category, func.count())
            .where(UnifiedEmail.category.isnot(None))
            .group_by(UnifiedEmail.category)
            .order_by(func.count().desc())
            .limit(5)
        )
    ).all()
    by_account = (
        await db.execute(
            select(EmailAccount.email, func.count())
            .join(UnifiedEmail, UnifiedEmail.account_id == EmailAccount.id)
            .group_by(EmailAccount.email)
            .order_by(func.count().desc())
            .limit(5)
        )
    ).all()

    category_text = (
        ", ".join(f"{cat} {num}" for cat, num in by_category) if by_category else "（无）"
    )
    account_text = (
        ", ".join(f"{email} {num}" for email, num in by_account) if by_account else "（无）"
    )
    return "\n".join(
        [
            f"邮件总数：{total or 0}",
            f"未读：{unread or 0}",
            f"近7天：{recent or 0}",
            f"按分类：{category_text}",
            f"按账户：{account_text}",
        ]
    )


def _split_cited_ids(raw: str) -> tuple[str, list[str]]:
    """Return ``(answer, cited_ids)`` parsed from the LLM's raw output.

    Two accepted shapes, in order of preference:
    1. A free-form answer followed by ``---CITED_IDS---`` and a JSON array
       (the format the system prompt instructs).
    2. A JSON object like ``{"answer": "...", "cited_ids": [...]}`` — some
       providers force ``json_object`` output regardless of instructions, so
       this wrapper is unwrapped defensively.
    """
    text = (raw or "").strip()
    if not text:
        return "", []

    # Shape 2: whole output is a JSON object with answer/cited_ids.
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict):
        answer = obj.get("answer")
        ids = obj.get("cited_ids") or obj.get("ids") or obj.get("result")
        if isinstance(answer, str) and (ids is None or isinstance(ids, list)):
            clean = (
                [str(i).strip().strip('"\'') for i in ids if i is not None]
                if isinstance(ids, list)
                else []
            )
            return answer.strip(), clean

    # Shape 1: split on the marker; anything before it is the answer.
    if CITED_MARKER in text:
        answer, _, payload = text.partition(CITED_MARKER)
        return answer.strip(), _extract_cited_ids(payload)
    return text, []


def _extract_cited_ids(payload: str) -> list[str]:
    """Parse the marker payload into a list of ID strings; [] on failure."""
    if not payload.strip():
        return []

    def _loads(candidate: str):
        return json.loads(
            candidate.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )

    try:
        parsed = _loads(payload)
    except json.JSONDecodeError:
        # Slice from the first '[' to the last ']' to drop surrounding prose.
        start, end = payload.find("["), payload.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            parsed = _loads(payload[start : end + 1])
        except json.JSONDecodeError:
            return []

    if isinstance(parsed, dict):
        for key in ("ids", "cited_ids", "result", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip().strip('"\'') for item in parsed if item is not None]


async def chat_qa(
    db,
    cfg: AiConfig,
    query: str,
    history: list | None = None,
    limit: int = RETRIEVAL_LIMIT,
) -> ChatResult:
    """Answer ``query`` from retrieved email fragments (plus stats if asked)."""
    query = (query or "").strip()
    stats_context = (await _compute_stats(db)) if _detect_stats_query(query) else "（无）"

    results, _total = await search_emails(db, query, limit=limit, cfg=cfg)
    if results:
        parts = []
        for i, item in enumerate(results, start=1):
            snippet = (item.get("snippet") or "")[:SNIPPET_MAX_CHARS] or "（无正文）"
            match = "/".join(item.get("match") or []) or "—"
            parts.append(
                f"[{i}] ID:{item['id']} 主题:{item.get('subject') or ''} "
                f"发件人:{item.get('sender') or ''} 日期:{item.get('date')} "
                f"匹配方式:{match}\n"
                f"    摘要:{snippet}"
            )
        fragments = "\n".join(parts)
    else:
        fragments = "（无）"

    if history:
        lines = []
        for item in history[-HISTORY_ROUNDS:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = str(item.get("content") or "")
            if role == "user":
                lines.append(f"user: {content}")
            else:
                lines.append(f"assistant: {content}")
        history_text = "\n".join(lines) or "（无）"
    else:
        history_text = "（无）"

    user_content = USER_PROMPT_TEMPLATE.format(
        query=query,
        stats_context=stats_context,
        fragments=fragments,
        history=history_text,
    )

    # Be honest about retrieval limits when the semantic channel is off (either
    # not configured, or the DB lacks the pgvector extension).
    from app.services.embedding_service import vector_available

    embedding_effective = cfg.embedding_enabled and await vector_available(db)
    system_prompt = SYSTEM_PROMPT
    if not embedding_effective:
        system_prompt += _NO_EMBEDDING_NOTE

    if cfg.provider == "anthropic":
        raw = await asyncio.wait_for(
            _anthropic_chat(
                cfg, user_content, system_prompt, max_tokens=MAX_TOKENS, timeout=TIMEOUT_SECONDS
            ),
            timeout=TIMEOUT_SECONDS,
        )
    else:
        raw = await asyncio.wait_for(
            _openai_chat(
                cfg,
                user_content,
                system_prompt,
                max_tokens=MAX_TOKENS,
                json_mode=False,
                timeout=TIMEOUT_SECONDS,
            ),
            timeout=TIMEOUT_SECONDS,
        )

    answer, cited_ids = _split_cited_ids(raw)
    return ChatResult(answer=answer, cited_ids=cited_ids)
