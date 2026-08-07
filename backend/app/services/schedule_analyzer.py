"""AI schedule & priority analysis for the dashboard.

Takes the user's pending emails (needs reply/review, not yet handled) and
asks the LLM to:
1. Extract schedule items (meetings, deadlines, appointments) from email content.
2. Produce a priority queue — which emails to handle first and why.
3. Generate a brief daily summary.

Results are cached in-memory for 3 minutes to avoid excessive LLM calls.

When no AI key is configured, falls back to a rule-based heuristic that
sorts by priority_score + action urgency.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import MailDirection, UnifiedEmail
from app.services.ai_config import AiConfig, load_ai_config

_log = logging.getLogger(__name__)

# Cache: (result, expires_at). Single-entry cache for the dashboard.
_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 180  # 3 minutes


def invalidate_cache() -> None:
    """Drop all cached schedule results (called when AI settings change)."""
    _cache.clear()

_SCHEDULE_PROMPT = """你是一名邮件优先级分析助手。以下是用户待处理的邮件列表（JSON数组），每封邮件包含 id, priority, action, subject, summary, received_at 字段。
今天是 {today}。请分析这些邮件并返回处理建议。

只返回一个JSON对象（不要任何额外文字、不要markdown代码块），格式如下：
{{
  "schedule_items": [
    {{"title": "会议/日程标题", "date": "YYYY-MM-DD或空", "time": "HH:MM或空", "email_id": 邮件ID, "type": "meeting|deadline|appointment|reminder", "group": "today|tomorrow|this_week|upcoming"}}
  ],
  "priority_queue": [
    {{"email_id": 邮件ID, "reason": "为什么这封邮件需要优先处理（15字以内）", "urgency": "high|medium|low", "estimated_minutes": 预计处理时间(整数)}}
  ],
  "daily_brief": "今日邮件概要，包含紧急事项和需要关注的重点（50字以内）"
}}

分析要点：
- 识别邮件中提到的会议、截止日期、预约等时间敏感事项
- 提取日期和时间信息，如"明天下午3点"、"1月15日截止"、"下周一开会"等
- 根据 {today} 计算实际日期，将 schedule_items 的 group 设为 today/tomorrow/this_week/upcoming
- 按紧急程度排序优先级队列：今天到期的 > 明天到期的 > 本周的 > 无明确期限的
- 需要回复的邮件优先级高于仅需查看的
- 如果邮件内容没有明确时间信息，不要臆造日期
- priority_queue 的顺序就是建议的处理顺序
- daily_brief 用中文，简洁有力"""


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


async def get_pending_emails(
    db: AsyncSession, limit: int = 30
) -> list[UnifiedEmail]:
    """Fetch emails that need user action and haven't been handled yet.

    Filters:
    - direction = INBOX
    - handled_at IS NULL (not yet dismissed from dashboard)
    - suggested_action IN ('reply', 'review')
    - not advertisement
    Sorted by priority_score DESC, then received_at DESC.
    """
    stmt = (
        select(UnifiedEmail)
        .where(
            UnifiedEmail.direction == MailDirection.INBOX,
            UnifiedEmail.handled_at.is_(None),
            UnifiedEmail.suggested_action.in_(["reply", "review"]),
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
    db: AsyncSession, account_id: int | None = None
) -> ScheduleResult:
    """Run schedule analysis on pending emails. Uses cache when available."""
    # Auto-handle first, so stale emails don't clutter the queue.
    await auto_handle_emails(db)

    cache_key = f"schedule:{account_id or 'all'}"
    cached = _cache.get(cache_key)
    if cached and cached[1] > time.time():
        data, _ = cached
        return ScheduleResult(**data)

    emails = await get_pending_emails(db, limit=30)
    if not emails:
        result = ScheduleResult(
            daily_brief="暂无待处理邮件，一切就绪。",
            source="rules",
        )
        _cache[cache_key] = (
            {
                "schedule_items": result.schedule_items,
                "priority_queue": result.priority_queue,
                "daily_brief": result.daily_brief,
                "source": result.source,
            },
            time.time() + _CACHE_TTL,
        )
        return result

    cfg = await load_ai_config(db)
    if cfg.use_ai:
        try:
            result = await _analyze_with_llm(emails, cfg)
        except Exception as exc:
            if cfg.analysis_mode == "ai_only":
                raise
            _log.warning("Schedule LLM analysis failed, falling back to rules: %s", exc)
            result = _analyze_with_rules(emails)
    else:
        result = _analyze_with_rules(emails)

    _cache[cache_key] = (
        {
            "schedule_items": result.schedule_items,
            "priority_queue": result.priority_queue,
            "daily_brief": result.daily_brief,
            "source": result.source,
        },
        time.time() + _CACHE_TTL,
    )
    return result


async def _analyze_with_llm(
    emails: list[UnifiedEmail], cfg: AiConfig
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
            "summary": (e.summary or e.body_snippet or "")[:200],
            "received_at": e.received_at.isoformat() if e.received_at else "",
        })
    user_content = json.dumps(mail_list, ensure_ascii=False)
    system_prompt = _SCHEDULE_PROMPT.format(today=today)

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
        "max_tokens": 1500,
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
        elif action == "reply":
            urgency = "medium"
            reason = "待回复邮件"
        elif score >= 60:
            urgency = "medium"
            reason = "需查看处理"
        else:
            urgency = "low"
            reason = "可稍后处理"

        queue.append({
            "email_id": e.id,
            "reason": reason,
            "urgency": urgency,
            "estimated_minutes": 3 if action == "reply" else 2,
        })

    # Detect schedule items via keyword matching.
    schedule_keywords = (
        "会议", "meeting", "日程", "邀请", "deadline", "截止",
        "appointment", "预约", "reminder", "提醒", "agenda",
        "明天", "today", "今天", "tomorrow", "下周",
    )
    items: list[dict] = []
    for e in emails:
        text = f"{e.subject or ''} {e.summary or ''}".lower()
        if any(kw in text for kw in schedule_keywords):
            stype = "meeting" if any(k in text for k in ("会议", "meeting", "agenda")) else "reminder"

            # Try to determine the date group.
            group = "upcoming"
            if any(k in text for k in ("今天", "today")):
                group = "today"
            elif any(k in text for k in ("明天", "tomorrow")):
                group = "tomorrow"
            elif "下周" in text or "next week" in text:
                group = "this_week"

            items.append({
                "title": (e.subject or "(no subject)")[:80],
                "date": "",
                "time": "",
                "email_id": e.id,
                "type": stype,
                "group": group,
            })

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
