"""Pure-rule structured filter parsing for chat QA (no LLM involved).

Turns natural-language time/category phrases in a question — "今天",
"垃圾邮件/广告", "上个月的财务类邮件" — into concrete SQL filter values, so
retrieval (and the direct-SQL aggregate bypass) works identically for local
8k models and cloud APIs.

Two module-level constants hold all the domain knowledge; extending coverage
means adding entries, never touching logic:

* ``_CATEGORY_SYNONYMS`` — query phrases → ``UnifiedEmail.category`` enum name
  (the values stored in the DB, seeded from ``database._BUILTIN_CATEGORIES``).
  First matching category wins (dict order = priority).
* ``_DATE_RULES`` — regex → ``(date_from, date_to)`` factory, evaluated in
  order; negating/longer forms (昨天/上周/上个月) precede their shorter
  counterparts so e.g. "yesterday" can never match the "today" rule.

Timezone treatment matches ``chat_qa_service._compute_stats`` (近7天 uses
``datetime.now(timezone.utc)``): "today" is the UTC calendar date, consistent
with how ``received_at`` (timestamptz, UTC) is compared elsewhere.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone


def _utc_today() -> date:
    """Today's date in the same timezone as _compute_stats' 近7天 (UTC)."""
    return datetime.now(timezone.utc).date()


# Query phrases → UnifiedEmail.category enum value (NOT the Chinese label).
# Order matters: first category with a hit wins. Keep high-frequency /
# complaint-prone categories (marketing) at the top.
_CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "marketing": [
        "垃圾邮件", "垃圾", "广告", "推广", "促销", "营销", "推销",
        "骚扰", "spam", "junk", "advertis", "promo",
    ],
    "meeting": ["会议", "日程", "日历", "邀请", "calendar", "meeting"],
    "finance": [
        "财务", "账单", "发票", "付款", "支付", "缴费", "扣款", "报销",
        "invoice", "payment", "billing",
    ],
    "notification": ["系统通知", "通知", "提醒", "公告", "notification"],
    "newsletter": ["订阅", "简报", "周刊", "邮件列表", "newsletter", "digest"],
    "travel": ["旅行", "出差", "机票", "酒店", "行程", "签证", "travel", "trip"],
    "shopping": ["购物", "包裹", "快递", "物流", "订单", "电商", "order", "shopping"],
    "social": ["社交", "社区", "互动", "social"],
    "work": ["工作", "办公", "职场", "work", "office"],
    "personal": ["个人", "私人", "personal"],
    "other": ["其他", "杂项", "other"],
}

# Each rule: (regex, factory(today) -> (date_from, date_to)). Evaluated in
# order — negating/longer forms first so "yesterday" never hits "today".
_DATE_RULES: list[tuple[re.Pattern, object]] = [
    (re.compile(r"昨天|昨日|yesterday"), lambda t: (t - timedelta(days=1),) * 2),
    (re.compile(r"上周|上星期|last week"),
     lambda t: (t - timedelta(days=t.weekday() + 7),
                t - timedelta(days=t.weekday() + 1))),
    (re.compile(r"上个月|上月|last month"),
     lambda t: ((t.replace(day=1) - timedelta(days=1)).replace(day=1),
                t.replace(day=1) - timedelta(days=1))),
    (re.compile(r"今天|今日|today"), lambda t: (t, t)),
    (re.compile(r"这周|本周|这一周|这个星期|本星期|this week"),
     lambda t: (t - timedelta(days=t.weekday()), t)),
    (re.compile(r"本月|这个月|这月|当月|this month"),
     lambda t: (t.replace(day=1), t)),
]

# Enumeration intent: the question asks to list/count senders of a category
# ("谁发的", "有哪些", "几封") rather than to search for specific content.
_ENUM_INTENT_RE = re.compile(
    r"(谁|哪些|都是|有什么|有哪些|什么|列出|列举|分别|几封|几条|多少|几个|统计)"
)


def _match_category(query: str) -> str | None:
    for category, synonyms in _CATEGORY_SYNONYMS.items():
        if any(syn in query for syn in synonyms):
            return category
    return None


def _match_date_range(query: str) -> tuple[date, date] | None:
    for pattern, factory in _DATE_RULES:
        if pattern.search(query):
            return factory(_utc_today())
    return None


def parse_structured_filters(query: str) -> dict:
    """Parse ``{category, date_from, date_to}`` from a natural-language query.

    Every field is ``None`` when absent. Dates are ISO strings (YYYY-MM-DD) —
    the exact shape ``email_search._filter_clause`` expects for its
    ``(:date)::date`` casts. Pure string matching; never calls an LLM.
    """
    q = (query or "").strip().lower()
    date_range = _match_date_range(q)
    return {
        "category": _match_category(q),
        "date_from": date_range[0].isoformat() if date_range else None,
        "date_to": date_range[1].isoformat() if date_range else None,
    }


def has_enumeration_intent(query: str) -> bool:
    """True when the question asks to enumerate a category's senders/counts."""
    return bool(_ENUM_INTENT_RE.search(query or ""))
