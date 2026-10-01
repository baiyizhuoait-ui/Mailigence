"""AI-powered cross-mailbox chat QA.

Given a natural-language question, builds an answer from three layered
context sources (each strictly grounded, nothing invented):

1. **Step-back digest** — mailbox statistics + a one-line list of the most
   important recent emails. Always present for broad/summary questions
   ("最近有什么重要的？"), so vague queries get a useful overview instead of
   a refusal.
2. **Multi-query retrieval** — the original query plus LLM-extracted search
   keywords are each run through the three-way recall (keyword/semantic)
   and fused with RRF, so vague questions autonomously surface likely
   relevant mail ("query expansion", see arXiv:2305.14283 and multi-query
   retrieval patterns).
3. **Conversation history** — last few turns for follow-up questions.

A tiny planner call first classifies the question (broad vs specific) and
extracts search keywords. Local servers (Ollama / LM Studio, detected by a
localhost base_url) get a compact context budget to fit 4k windows.

The LLM must answer only from the provided material and always append the
IDs it actually cited (``---CITED_IDS---`` marker).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.email import MailDirection, UnifiedEmail
from app.models.email_account import EmailAccount
from app.services.ai_analyzer import _anthropic_chat, _openai_chat
from app.services.ai_config import AiConfig
from app.services.email_search import search_emails

_log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是用户的邮件助理。你只能根据下面提供的材料回答问题：邮箱概况、重要邮件列表、相关邮件片段，严禁编造材料之外的信息。

回答策略：
- 概况/总结/泛泛类问题（如"最近有什么重要的事"）：先用邮箱概况给一句总览，再按"需要行动 / 时间敏感 / 其余值得注意"分组列点；每个要点后标注来源邮件 [ID:xx]
- 具体问题：直接给结论，再补充细节，引用的邮件标注 [ID:xx]
- 材料确实完全无法回答时才说明不足，并建议用户如何缩小问题范围；不要轻易放弃——先利用概况和重要邮件列表作答
- 回答简洁直接，不要复述材料原文

最后必须输出一个 JSON 字段列出你在回答中实际引用到的邮件 ID，格式：
---CITED_IDS---
["id1", "id2"]"""

USER_PROMPT_TEMPLATE = """用户问题：{query}

（一）邮箱概况：
{stats_context}

（二）近期重要邮件列表（未处理收件，按优先级）：
{important_list}

（三）相关邮件片段：
{fragments}

对话历史（如有，最近3轮）：
{history}

请回答用户问题。"""

# Tiny pre-retrieval planner: classify + extract search keywords.
_PLANNER_SYSTEM = """你是检索规划器。分析用户关于邮箱的提问，只返回JSON对象（无markdown、无额外文字）：
{{"broad": true或false, "keywords": ["关键词1", "关键词2", "关键词3"]}}
- broad=true：概况/总结/泛泛类（"最近有什么重要的"、"总结一下"、"有什么待办"、"谁在等我回复"）
- broad=false：具体查找（含明确发件人/主题/编号，如"Google的验证码"、"张三的报价单"）
- keywords：从问题提取3个以内具体检索词（名词、发件人、主题词，可含中英文变体）；宽泛问题提取其隐含主题（如"风险"→["安全","提醒","警告"]）；提取不出则空数组"""

CITED_MARKER = "---CITED_IDS---"
# Cloud budget: 12 fragments × 600 chars keeps total prompt ≈3k tokens.
RETRIEVAL_LIMIT = 12
SNIPPET_MAX_CHARS = 600
# Local 8k-window budget (Ollama/LM Studio): ~10×420-char fragments ≈5.3k
# prompt tokens + 2048 output leaves headroom under 8k.
LOCAL_RETRIEVAL_LIMIT = 10
LOCAL_SNIPPET_MAX_CHARS = 420
LOCAL_MAX_TOKENS = 2048
HISTORY_ROUNDS = 3
# History budget: cap each turn and the total so long conversations can't
# blow the context window (answers can be 500+ chars each). Local 8k windows
# share the cloud caps now that fragments dominate the budget.
HISTORY_TURN_MAX_CLOUD = 600
HISTORY_TURN_MAX_LOCAL = 600
HISTORY_TOTAL_MAX_CLOUD = 2200
HISTORY_TOTAL_MAX_LOCAL = 2200
# Thinking models spend many tokens on reasoning before answering; give them
# room plus time so the answer and the ---CITED_IDS--- trailer survive.
MAX_TOKENS = 2048
TIMEOUT_SECONDS = 120
PLANNER_TIMEOUT = 25
# Important-mail digest lines (step-back context for broad questions).
DIGEST_LIMIT = 12
LOCAL_DIGEST_LIMIT = 12
# Extra keyword queries the planner may contribute.
MAX_EXPANSION_QUERIES = 3
RRF_K = 60

# Appended to the system prompt when semantic (embedding) recall is off, so the
# model sets honest expectations instead of pretending it saw everything.
_NO_EMBEDDING_NOTE = (
    "\n\n（当前使用关键词检索，可能存在语义相关但未命中的邮件。）"
)

_STATS_QUERY_RE = re.compile(r"(多少|几条|几封|统计|数量|几个|多少封|多少条)")
# Heuristic "broad question" hints — used before/instead of the planner LLM.
_VAGUE_RE = re.compile(
    r"(总结|概况|概览|概述|重要|要紧|待办|需要我|等我回复|谁在|有哪些|有什么|忙|优先|"
    r"风险|安排|日程|状态|近况|帮我看看|怎么样|如何|attention|summary|overview|recent|"
    r"important|todo|status|pending|urgent|anything|what.*(need|should|have))"
)
# Heuristic "specific" hints — concrete identifiers make retrieval reliable.
_SPECIFIC_RE = re.compile(r"(\d{4,}|[\w.+-]+@[\w-]+\.[\w.]+|验证码|订单|编号|invoice|ticket|#)")


@dataclass
class ChatResult:
    answer: str
    cited_ids: list[str]


@dataclass
class QueryPlan:
    broad: bool = False
    keywords: list[str] = field(default_factory=list)


def _is_local(cfg: AiConfig) -> bool:
    """Local servers (Ollama/LM Studio/vLLM) → compact 8k prompt budget."""
    return any(h in (cfg.base_url or "") for h in ("localhost", "127.0.0.1"))


def _detect_stats_query(query: str) -> bool:
    """True when the question asks about aggregate mailbox statistics."""
    return bool(_STATS_QUERY_RE.search(query or ""))


def _looks_broad(query: str) -> bool:
    """Heuristic broad-question detection (no LLM needed)."""
    q = (query or "").strip()
    if not q:
        return False
    if _SPECIFIC_RE.search(q):
        return False
    if _VAGUE_RE.search(q):
        return True
    # Very short, content-free questions are treated as broad too.
    return len(q) <= 6


async def _plan_query(cfg: AiConfig, query: str, history: list | None) -> QueryPlan:
    """Classify the question and extract expansion keywords.

    Heuristics run first; the LLM planner only runs when AI is available and
    either the heuristics are inconclusive or keyword extraction would help
    (broad questions need reformulations, per multi-query retrieval).
    """
    broad = _looks_broad(query)
    if not cfg.use_ai:
        return QueryPlan(broad=broad)
    if not broad and len((query or "").strip()) > 10 and not history:
        # Clearly specific, standalone question → retrieval works as-is.
        return QueryPlan(broad=False)
    try:
        raw = await asyncio.wait_for(
            _openai_chat(
                cfg,
                query,
                _PLANNER_SYSTEM,
                max_tokens=120,
                json_mode=True,
                timeout=PLANNER_TIMEOUT,
            )
            if cfg.provider != "anthropic"
            else _anthropic_chat(
                cfg, query, _PLANNER_SYSTEM, max_tokens=120, timeout=PLANNER_TIMEOUT
            ),
            timeout=PLANNER_TIMEOUT,
        )
        obj = json.loads((raw or "").strip().strip("`").removeprefix("json").strip())
        if isinstance(obj, dict):
            llm_broad = bool(obj.get("broad"))
            kws = [
                str(k).strip()
                for k in (obj.get("keywords") or [])
                if str(k).strip()
            ][:MAX_EXPANSION_QUERIES]
            # Broad wins when either signal fires — keeps the digest path.
            return QueryPlan(broad=broad or llm_broad, keywords=kws)
    except Exception as exc:
        _log.warning("Chat query planner failed, heuristic only: %s", exc)
    return QueryPlan(broad=broad)


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


async def _digest_important_emails(db, limit: int) -> str:
    """One line per important recent email — step-back context for broad Qs.

    Same window/eligibility as the dashboard advisor (unhandled inbox, ads
    excluded, priority then recency). One compact line keeps the whole list
    well under 500 chars even for the 12-entry cloud budget.
    """
    stmt = (
        select(UnifiedEmail)
        .where(
            UnifiedEmail.direction == MailDirection.INBOX,
            UnifiedEmail.handled_at.is_(None),
            UnifiedEmail.is_advertisement.is_(False)
            | (UnifiedEmail.is_advertisement.is_(None)),
        )
        .order_by(
            UnifiedEmail.priority_score.desc().nullslast(),
            UnifiedEmail.received_at.desc().nullslast(),
        )
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return "（暂无未处理的重要邮件）"
    lines = []
    for e in rows:
        date = e.received_at.strftime("%m-%d") if e.received_at else "—"
        cat = e.category or "未分类"
        action = {"reply": "需回复", "review": "需查看", "notice": "通知"}.get(
            e.suggested_action or "", ""
        )
        subject = (e.subject or "（无主题）")[:36]
        lines.append(
            f"[ID:{e.id}] {date} [{cat}]{('[' + action + ']') if action else ''} "
            f"{e.sender or '未知发件人'}：{subject}"
        )
    return "\n".join(lines)


def _rrf_merge(result_lists: list[list[dict]], limit: int) -> list[dict]:
    """Fuse multiple retrieval result lists with Reciprocal Rank Fusion."""
    fused: dict[int, tuple[float, dict]] = {}
    for results in result_lists:
        for rank, item in enumerate(results, start=1):
            score = 1 / (RRF_K + rank)
            if item["id"] in fused:
                fused[item["id"]][0] += score
            else:
                fused[item["id"]] = [score, dict(item)]
    ordered = sorted(fused.values(), key=lambda x: x[0], reverse=True)
    return [item for _, item in ordered[:limit]]


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
    limit: int | None = None,
) -> ChatResult:
    """Answer ``query`` from digest + multi-query-retrieved fragments."""
    query = (query or "").strip()
    local = _is_local(cfg)
    limit = limit or (LOCAL_RETRIEVAL_LIMIT if local else RETRIEVAL_LIMIT)
    snippet_max = LOCAL_SNIPPET_MAX_CHARS if local else SNIPPET_MAX_CHARS

    plan = await _plan_query(cfg, query, history)

    stats_needed = _detect_stats_query(query) or plan.broad
    stats_context = (await _compute_stats(db)) if stats_needed else "（无）"
    if stats_needed:
        important_list = await _digest_important_emails(
            db, LOCAL_DIGEST_LIMIT if local else DIGEST_LIMIT
        )
    else:
        important_list = "（无）"

    # Multi-query retrieval: original query first (the anchor), then the
    # planner's expansion keywords. RRF merges so items multiple queries
    # agree on rise to the top.
    result_lists: list[list[dict]] = []
    seen_ids: set[int] = set()
    for q in [query, *plan.keywords]:
        if not q:
            continue
        results, _total = await search_emails(db, q, limit=10, cfg=cfg)
        if results:
            result_lists.append(results)
            seen_ids.update(r["id"] for r in results)
        if len(seen_ids) >= limit:
            break
    results = _rrf_merge(result_lists, limit) if result_lists else []

    if results:
        parts = []
        for i, item in enumerate(results, start=1):
            snippet = (item.get("snippet") or "")[:snippet_max] or "（无正文）"
            match = "/".join(item.get("match") or []) or "—"
            parts.append(
                f"[{i}] ID:{item['id']} 主题:{item.get('subject') or ''} "
                f"发件人:{item.get('sender') or ''} 日期:{item.get('date')} "
                f"匹配方式:{match}\n"
                f"    摘要:{snippet}"
            )
        fragments = "\n".join(parts)
    else:
        fragments = "（无命中——请优先依据概况和重要邮件列表回答）"

    if history:
        turn_max = HISTORY_TURN_MAX_LOCAL if local else HISTORY_TURN_MAX_CLOUD
        total_max = HISTORY_TOTAL_MAX_LOCAL if local else HISTORY_TOTAL_MAX_CLOUD
        lines = []
        for item in history[-HISTORY_ROUNDS:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = str(item.get("content") or "")
            if role == "user":
                lines.append(f"user: {content[:turn_max]}")
            else:
                lines.append(f"assistant: {content[:turn_max]}")
        # Prefer recent turns: drop oldest until under the total budget.
        while len(lines) > 1 and sum(len(line) for line in lines) > total_max:
            lines.pop(0)
        history_text = "\n".join(lines) or "（无）"
    else:
        history_text = "（无）"

    user_content = USER_PROMPT_TEMPLATE.format(
        query=query,
        stats_context=stats_context,
        important_list=important_list,
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
                max_tokens=LOCAL_MAX_TOKENS if local else MAX_TOKENS,
                json_mode=False,
                timeout=TIMEOUT_SECONDS,
            ),
            timeout=TIMEOUT_SECONDS,
        )

    answer, cited_ids = _split_cited_ids(raw)
    return ChatResult(answer=answer, cited_ids=cited_ids)
