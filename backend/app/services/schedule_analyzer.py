"""AI schedule & priority analysis for the dashboard.

Takes the user's recent important emails (last 7 days, non-ad, unhandled)
and asks the LLM to act as an executive assistant:
1. Write an action-first brief — what to do NOW, in imperative voice.
2. Produce a priority queue with a concrete suggested action per email.
3. Extract schedule items (meetings, deadlines, appointments).

Every response carries a *fingerprint* — a stable hash of all window
emails' change-relevant state. Clients send back the fingerprint of the
data they already rendered; if nothing in the window changed, the server
serves the cached result without calling the LLM, so "refresh" clicks are
instant and free.

When no AI key is configured, falls back to a rule-based heuristic that
sorts by priority_score + action urgency.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import MailDirection, UnifiedEmail
from app.services.ai_config import AiConfig, load_ai_config

_log = logging.getLogger(__name__)

# Analysis window: only recent mail feeds the advisor board.
SCHEDULE_WINDOW_DAYS = 7

# Cache: (result, fingerprint, expires_at). Single-entry per account.
# The fingerprint is the primary invalidation signal; the TTL is only a
# safety net (e.g. AI-memory edits that don't call invalidate_cache).
_cache: dict[str, tuple[dict, str, float]] = {}
_CACHE_TTL = 600  # 10 minutes max staleness

# In-flight analysis tasks per cache key — overlapping polls (30s interval,
# ~20s LLM call) must share one LLM call instead of stacking duplicates.
_inflight: dict[str, "asyncio.Task[ScheduleResult]"] = {}


def invalidate_cache() -> None:
    """Drop all cached schedule results (called when AI settings change)."""
    _cache.clear()


async def get_window_fingerprint(db: AsyncSession) -> str:
    """Stable hash over every window email's change-relevant state.

    Mirrors the candidate filter of ``get_schedule_candidates`` (inbox,
    last 7 days, non-ad): new mail, handled/read/replied flags, archive
    state and AI category/action/priority updates all change the hash,
    so callers can detect "anything new?" with one cheap DB query.
    """
    since = datetime.now(timezone.utc) - timedelta(days=SCHEDULE_WINDOW_DAYS)
    stmt = (
        select(
            UnifiedEmail.id,
            UnifiedEmail.handled_at,
            UnifiedEmail.is_read,
            UnifiedEmail.has_reply,
            UnifiedEmail.is_archived,
            UnifiedEmail.suggested_action,
            UnifiedEmail.priority_score,
            UnifiedEmail.category,
        )
        .where(
            UnifiedEmail.direction == MailDirection.INBOX,
            UnifiedEmail.received_at >= since,
            UnifiedEmail.is_advertisement.is_(False)
            | (UnifiedEmail.is_advertisement.is_(None)),
        )
        .order_by(UnifiedEmail.id)
        .limit(1000)
    )
    result = await db.execute(stmt)
    digest = hashlib.sha1()
    for row in result.all():
        digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


_SCHEDULE_PROMPT = """你是一位高效的首席执行助理（Chief of Staff），负责为老板处理邮箱。今天是 {today}。
以下是用户最近 7 天内的重要邮件（JSON数组），每封包含 id, priority, action, subject, summary, body, received_at。请优先从 body 和 subject 中识别信息。

用户偏好记忆（分析时优先遵守）：
{memories}

你的任务：不要罗列信息，直接给出可执行的行动建议——像助理在晨间简报里给老板下指令一样。

只返回一个JSON对象（不要任何额外文字、不要markdown代码块），格式如下：
{{
  "daily_brief": "行动简报：先说现在最该做的一件事，再说其余要点，最后说哪些可以放一放（中文，命令式，100字以内）",
  "priority_queue": [
    {{"email_id": 邮件ID, "action": "具体动作，动词开头，如：回复确认周三15:00的面试时间", "reason": "为什么现在处理（12字以内）", "urgency": "high|medium|low", "estimated_minutes": 预计处理时间(整数)}}
  ],
  "schedule_items": [
    {{"title": "会议/日程标题", "date": "YYYY-MM-DD或空", "time": "HH:MM或空", "email_id": 邮件ID, "type": "meeting|deadline|appointment|reminder", "group": "today|tomorrow|this_week|upcoming"}}
  ]
}}

要求：
- daily_brief 直接下指令，例如"先回复张总确认签约时间，再把报价单转给财务审核；其他通知型邮件无需处理"——禁止写成"有几封邮件"这类信息复述
- priority_queue 按建议的处理顺序排列，最多8条：今天到期的 > 明天到期的 > 本周的 > 无明确期限的；需要回复的高于仅需查看的
- action 必须具体可执行：动词开头、包含关键对象/时间（如"回复确认周三15:00面试"），禁止写"处理邮件"这类空话
- 广告、营销、纯通知类邮件不要进入 priority_queue
- type=meeting 仅用于真实的会议邀请或日历会议；账单日、行程提醒、物流通知等一般性时间提醒用 reminder，明确截止期限用 deadline，预约确认用 appointment
- 从正文和主题中识别会议邀请、日历通知、截止提醒、预约等时间敏感事项，提取"明天下午3点"、"1月15日截止"、"周五 15:00"等表达并换算成真实日期
- 根据 {today} 计算实际日期，将 schedule_items 的 group 设为 today/tomorrow/this_week/upcoming
- 邮件内容没有明确时间信息时不要臆造日期，也不要放入 schedule_items"""


async def auto_handle_emails(db: AsyncSession) -> int:
    """Auto-mark emails as handled when they no longer need dashboard attention.

    Conditions:
    - has_reply = true (already replied)
    - suggested_action = 'none' (no action needed)
    - is_read = true AND suggested_action = 'notice' (read + FYI only)
    - is_read = true AND received_at older than 3 days (stale read mail the
      user already saw but didn't act on — no longer time-sensitive)

    Returns the number of newly handled emails.
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=3)
    stmt = (
        update(UnifiedEmail)
        .where(
            UnifiedEmail.handled_at.is_(None),
            UnifiedEmail.direction == MailDirection.INBOX,
        )
        .where(
            # Condition 1: already replied
            UnifiedEmail.has_reply.is_(True)
            |
            # Condition 2: no action needed
            (UnifiedEmail.suggested_action == "none")
            |
            # Condition 3: read + FYI only
            (
                UnifiedEmail.is_read.is_(True)
                & (UnifiedEmail.suggested_action == "notice")
            )
            |
            # Condition 4: read + stale (older than 3 days) — user saw it,
            # didn't act, no longer urgent for the dashboard.
            (
                UnifiedEmail.is_read.is_(True)
                & UnifiedEmail.received_at.is_not(None)
                & (UnifiedEmail.received_at < stale_cutoff)
            )
        )
        .values(handled_at=now)
    )
    result = await db.execute(stmt)
    await db.commit()
    count = result.rowcount or 0
    if count > 0:
        _log.info("Auto-handled %d emails from dashboard", count)
    return count


async def get_schedule_candidates(
    db: AsyncSession, limit: int = 30
) -> list[UnifiedEmail]:
    """Fetch emails likely to contain schedule info (meetings/deadlines).

    Scans the last ``SCHEDULE_WINDOW_DAYS`` days of non-ad, non-archived,
    unhandled inbox mail regardless of the suggested action — meeting invites
    and calendar notifications are usually classified as 'notice', not
    'reply', and would otherwise never reach the schedule extractor.
    """
    since = datetime.now(timezone.utc) - timedelta(days=SCHEDULE_WINDOW_DAYS)
    stmt = (
        select(UnifiedEmail)
        .where(
            UnifiedEmail.direction == MailDirection.INBOX,
            UnifiedEmail.handled_at.is_(None),
            UnifiedEmail.is_archived.is_(False),
            UnifiedEmail.received_at >= since,
            UnifiedEmail.is_advertisement.is_(False)
            | (UnifiedEmail.is_advertisement.is_(None)),
        )
        .order_by(
            UnifiedEmail.priority_score.desc().nullslast(),
            UnifiedEmail.received_at.desc().nullslast(),
        )
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def mark_handled(db: AsyncSession, email_id: int) -> bool:
    """Mark a single email as handled on the dashboard."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(UnifiedEmail)
        .where(
            UnifiedEmail.id == email_id,
            UnifiedEmail.handled_at.is_(None),
        )
        .values(handled_at=now)
    )
    result = await db.execute(stmt)
    await db.commit()
    return (result.rowcount or 0) > 0


async def analyze_schedule(
    db: AsyncSession,
    account_id: int | None = None,
    client_fp: str | None = None,
) -> ScheduleResult:
    """Run schedule analysis on window emails. Fingerprint-gated.

    Fast path: when the window fingerprint equals the cached one AND the
    client already rendered that same fingerprint (``client_fp`` matches),
    the cached result is returned without any LLM call — the dashboard
    "refresh" button becomes instant and free when nothing changed.
    """
    # Auto-handle first, so stale emails don't clutter the queue.
    await auto_handle_emails(db)

    cache_key = f"schedule:{account_id or 'all'}"
    fingerprint = await get_window_fingerprint(db)
    cached = _cache.get(cache_key)
    if cached and cached[1] == fingerprint:
        data, _, expires_at = cached
        # Serve cache when fresh, or whenever the client already has this
        # exact fingerprint (regenerating identical output is pure waste).
        if expires_at > time.time() or (client_fp is not None and client_fp == fingerprint):
            return ScheduleResult(**data, fingerprint=fingerprint)

    # Share a single analysis across overlapping callers (concurrent polls).
    existing = _inflight.get(cache_key)
    if existing is not None:
        return await asyncio.shield(existing)
    task = asyncio.ensure_future(
        _analyze_schedule_uncached(db, account_id, cache_key, fingerprint)
    )
    _inflight[cache_key] = task
    try:
        return await task
    finally:
        _inflight.pop(cache_key, None)


async def _analyze_schedule_uncached(
    db: AsyncSession,
    account_id: int | None,
    cache_key: str,
    fingerprint: str,
) -> ScheduleResult:
    """Full analysis path (LLM or rules) + cache store. One call per key."""
    emails = await get_schedule_candidates(db, limit=30)
    if not emails:
        result = ScheduleResult(
            daily_brief="暂无待处理邮件，一切就绪。",
            source="rules",
            fingerprint=fingerprint,
        )
        _cache[cache_key] = (
            {
                "schedule_items": result.schedule_items,
                "priority_queue": result.priority_queue,
                "daily_brief": result.daily_brief,
                "source": result.source,
            },
            fingerprint,
            time.time() + _CACHE_TTL,
        )
        return result

    cfg = await load_ai_config(db)
    if cfg.use_ai:
        try:
            result = await _analyze_with_llm(db, emails, cfg)
        except Exception as exc:
            if cfg.analysis_mode == "ai_only":
                raise
            _log.warning("Schedule LLM analysis failed, falling back to rules: %s", exc)
            result = _analyze_with_rules(emails)
    else:
        result = _analyze_with_rules(emails)
    result.fingerprint = fingerprint

    _cache[cache_key] = (
        {
            "schedule_items": result.schedule_items,
            "priority_queue": result.priority_queue,
            "daily_brief": result.daily_brief,
            "source": result.source,
        },
        fingerprint,
        time.time() + _CACHE_TTL,
    )
    return result


async def _analyze_with_llm(
    db: AsyncSession, emails: list[UnifiedEmail], cfg: AiConfig
) -> ScheduleResult:
    """Call the LLM with a batch of pending emails for schedule analysis."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mail_list = []
    for e in emails:
        mail_list.append({
            "id": e.id,
            "priority": e.priority_score or 0,
            "action": e.suggested_action or "review",
            "subject": (e.subject or "(no subject)")[:120],
            "summary": (e.summary or "")[:150],
            # The full body lives in the mailbox; the persisted snippet is the
            # best local proxy for schedule extraction (dates/times usually
            # live in the body, not in the AI summary). 800 chars (P3) keeps
            # the common "time/place/agenda" block intact.
            "body": (e.body_snippet or "")[:800],
            "received_at": e.received_at.isoformat() if e.received_at else "",
        })
    user_content = json.dumps(mail_list, ensure_ascii=False)

    # Inject the user's distilled preferences (AI memory), if any.
    from app.services.memories import get_memory_texts
    memories = await get_memory_texts(db)
    memory_block = "\n".join(f"- {m}" for m in memories) or "（无）"
    system_prompt = _SCHEDULE_PROMPT.format(today=today, memories=memory_block)

    if cfg.provider == "anthropic":
        parsed = await _anthropic_schedule(cfg, system_prompt, user_content)
    else:
        parsed = await _openai_schedule(cfg, system_prompt, user_content)

    return ScheduleResult(
        schedule_items=parsed.get("schedule_items", []),
        priority_queue=parsed.get("priority_queue", []),
        daily_brief=parsed.get("daily_brief", ""),
        source="ai",
    )


async def _openai_schedule(
    cfg: AiConfig, system_prompt: str, user_content: str
) -> dict:
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 2000,
    }
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    return json.loads(data["choices"][0]["message"]["content"])


async def _anthropic_schedule(
    cfg: AiConfig, system_prompt: str, user_content: str
) -> dict:
    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.model,
        "max_tokens": 2000,
        "temperature": 0,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }
    url = cfg.base_url.rstrip("/") + "/messages"
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    content = "".join(block.get("text", "") for block in data.get("content", []))
    return json.loads(content)


# --- rule-based schedule extraction -----------------------------------------

_SCHEDULE_KEYWORDS = (
    "会议", "meeting", "日程", "邀请", "invite", "deadline", "截止",
    "appointment", "预约", "reminder", "提醒", "agenda", "calendar",
    "日历", "明天", "today", "今天", "tomorrow", "下周", "星期", "周",
    "due", "due on", "报到", "签到",
)


def _group_for_date(dt: date, today: date) -> str:
    if dt == today:
        return "today"
    if dt == today + timedelta(days=1):
        return "tomorrow"
    if dt <= today + timedelta(days=7):
        return "this_week"
    return "upcoming"


def _parse_schedule_date(text: str) -> tuple[str, str] | None:
    """Return ``(group, date_iso)`` for common date phrases, or ``None``.

    Handles 今天/明天/后天, "X月X日", 周X/下周X/星期X, and English today/
    tomorrow. Relative phrases (明天 etc.) are resolved against today.
    """
    import re

    today = date.today()
    t = text.lower()
    if re.search(r"今天|今日|today", t):
        return "today", today.isoformat()
    if re.search(r"明天|明日|tomorrow", t):
        return "tomorrow", (today + timedelta(days=1)).isoformat()
    if re.search(r"后天", t):
        return _group_for_date(today + timedelta(days=2), today), (today + timedelta(days=2)).isoformat()

    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                dt = date(year, month, day)
            except ValueError:
                continue
            if dt >= today:
                return _group_for_date(dt, today), dt.isoformat()

    m = re.search(r"((?:下)?周|(?:下)?星期)([一二三四五六日天])", text)
    if m:
        wd = "一二三四五六日天".index(m.group(2))
        days = (wd - today.weekday()) % 7
        if m.group(1).startswith("下"):
            days += 7
        if days == 0:
            days = 7  # "周X" == today's weekday -> next occurrence
        dt = today + timedelta(days=days)
        return _group_for_date(dt, today), dt.isoformat()
    return None


def _parse_schedule_time(text: str) -> str:
    """Extract a ``HH:MM`` time from common expressions, or empty string."""
    import re

    m = re.search(r"(\d{1,2})[:：](\d{2})", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"(?:上午|下午|晚上|中午|早上)?(\d{1,2})\s*点(?:\s*(半|(\d{1,2})分))?", text)
    if m:
        h = int(m.group(1))
        if re.search(r"下午|晚上", text) and h < 12:
            h += 12
        if m.group(2) == "半":
            return f"{h:02d}:30"
        if m.group(3):
            return f"{h:02d}:{int(m.group(3)):02d}"
        return f"{h:02d}:00"
    return ""


def _extract_schedule_item(e: UnifiedEmail) -> dict | None:
    """Rule-based schedule detection for a single email, or ``None``."""
    text = f"{e.subject or ''} {e.summary or ''} {e.body_snippet or ''}"
    if not any(kw in text.lower() for kw in _SCHEDULE_KEYWORDS):
        return None

    lower = text.lower()
    if any(k in lower for k in ("meeting", "会议", "agenda", "日程")):
        stype = "meeting"
    elif any(k in lower for k in ("deadline", "截止", "due")):
        stype = "deadline"
    elif any(k in lower for k in ("预约", "appointment")):
        stype = "appointment"
    else:
        stype = "reminder"

    parsed = _parse_schedule_date(text)
    group = parsed[0] if parsed else "upcoming"
    date_str = parsed[1] if parsed else ""
    return {
        "title": (e.subject or "(no subject)")[:80],
        "date": date_str,
        "time": _parse_schedule_time(text),
        "email_id": e.id,
        "type": stype,
        "group": group,
    }


def _analyze_with_rules(emails: list[UnifiedEmail]) -> ScheduleResult:
    """Rule-based fallback: sort by priority + action urgency + recency."""
    from datetime import date, timedelta

    today = date.today()

    queue: list[dict] = []
    for e in emails:
        score = e.priority_score or 0
        action = e.suggested_action or "review"

        if action == "reply" and score >= 70:
            urgency = "high"
            reason = "需尽快回复"
            act = "回复这封邮件"
        elif action == "reply":
            urgency = "medium"
            reason = "待回复邮件"
            act = "回复这封邮件"
        elif score >= 60:
            urgency = "medium"
            reason = "需查看处理"
            act = "查看并跟进"
        else:
            urgency = "low"
            reason = "可稍后处理"
            act = "浏览确认"

        queue.append({
            "email_id": e.id,
            "action": act,
            "reason": reason,
            "urgency": urgency,
            "estimated_minutes": 3 if action == "reply" else 2,
        })

    # Detect schedule items from subject + summary + body snippet.
    items: list[dict] = []
    for e in emails:
        item = _extract_schedule_item(e)
        if item:
            items.append(item)

    urgent = sum(1 for q in queue if q["urgency"] == "high")
    total = len(queue)
    if urgent > 0:
        brief = f"有 {urgent} 封紧急邮件需要优先处理，共 {total} 封待处理。"
    elif total > 0:
        brief = f"共 {total} 封待处理邮件，暂无紧急事项。"
    else:
        brief = "暂无待处理邮件。"

    return ScheduleResult(
        schedule_items=items[:10],
        priority_queue=queue,
        daily_brief=brief,
        source="rules",
    )


@dataclass
class ScheduleResult:
    schedule_items: list[dict] = field(default_factory=list)
    priority_queue: list[dict] = field(default_factory=list)
    daily_brief: str = ""
    source: str = "ai"  # "ai" or "rules"
    fingerprint: str = ""  # window-state hash, echoed by clients for fast refresh
