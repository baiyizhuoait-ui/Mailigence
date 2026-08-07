"""AI analysis client — OpenAI-compatible (OpenAI / DeepSeek / Kimi / Qwen /
GLM / Ollama) and Anthropic Claude.

Per the spec, a single LLM call per email returns ALL analysis dimensions
(category, advertisement flag, priority, summary, suggested action) as
structured JSON, instead of one call per dimension — to control cost.

Decoupling
----------
This module is the ONLY place that knows about the LLM. The rest of the
backend talks to ``analyze_email()``, so swapping providers or switching to a
rule-based fallback is transparent to callers.

Behaviour
---------
* ``analyze_email(..., config=...)`` receives an ``AiConfig`` (resolved from DB
  + .env by ``app.services.ai_config``).
* ``auto`` mode: LLM when configured, degrade to rules on any failure.
* ``ai_only`` mode: always LLM; errors propagate to the caller.
* ``rules_only`` mode: pure programmatic analysis, no LLM call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.services.ai_config import AiConfig

# Fine-grained categories stored as English keys; the UI maps them to
# localized labels. Keeping keys in English avoids encoding issues and makes
# the filter dropdown match exactly what's in the database.
CATEGORIES = (
    "work",         # 工作（项目/任务/报告/合同/审批）
    "meeting",      # 会议（邀请/日程/议程）
    "finance",      # 财务（账单/发票/银行/报销/支付）
    "notification", # 系统通知（验证码/安全/服务提醒）
    "social",       # 社交（人脉/好友/消息/邀请）
    "travel",       # 旅行（机票/酒店/行程/预订）
    "shopping",     # 购物（订单/物流/退换货）
    "marketing",    # 营销广告（促销/推广/限时优惠）
    "newsletter",   # 订阅简报（资讯/周报/期刊）
    "personal",     # 个人
    "other",        # 其他
)
ACTIONS = ("reply", "review", "note", "ignore")

_SYSTEM_PROMPT = """你是一名邮件分析助手。对给定的邮件主题与正文摘要，进行一次性多任务分析，
只返回一个 JSON 对象（不要任何额外文字、不要 markdown 代码块），字段如下：
{
  "category": "work|meeting|finance|notification|social|travel|shopping|marketing|newsletter|personal|other 之一",
  "is_advertisement": true 或 false,
  "priority_score": 0-100 的整数（越重要越高；marketing/newsletter 通常很低），
  "summary": "一句话中文摘要，不超过50字",
  "suggested_action": "reply|review|note|ignore 之一"
}
分类说明：
- work: 工作相关（项目/任务/报告/合同/审批）
- meeting: 会议邀请/日程/议程
- finance: 财务（账单/发票/银行/报销/支付）
- notification: 系统通知（验证码/安全提醒/服务通知）
- social: 社交（人脉/好友/消息/邀请）
- travel: 旅行（机票/酒店/行程/预订）
- shopping: 购物（订单/物流/退换货）
- marketing: 营销广告（促销/推广/限时优惠）
- newsletter: 订阅简报（资讯/周报/期刊）
- personal: 个人邮件
- other: 其他
判定要点：含 List-Unsubscribe/促销/退订等特征视为 marketing 并低优先级；
会议/工作邮件需要用户回应的高优先级；验证码/通知类为中低优先级。"""


@dataclass
class AnalysisResult:
    category: str
    is_advertisement: bool
    priority_score: int
    summary: str
    suggested_action: str


def is_configured() -> bool:
    """True only when a real LLM endpoint + key are configured (env or DB)."""
    cfg = _default_config()
    return cfg.use_ai


def _default_config() -> AiConfig:
    """Synchronous fallback config (used only when callers can't pass one)."""
    from app.services.ai_config import _env_fallback

    prov, base_url, api_key, model = _env_fallback("")
    return AiConfig(
        analysis_mode=settings.ai_analysis_mode or "auto",
        provider=prov,
        base_url=base_url,
        api_key=api_key,
        model=model,
    )


# --- public entrypoint ------------------------------------------------------

async def analyze_email(
    subject: str,
    snippet: str,
    raw_headers: dict[str, Any] | None,
    config: AiConfig | None = None,
) -> AnalysisResult:
    """Analyze one email according to the effective AI config.

    auto: LLM when configured, graceful rule fallback otherwise.
    ai_only: always LLM (errors propagate).
    rules_only: pure programmatic analysis.
    """
    cfg = config or _default_config()
    if cfg.use_ai:
        try:
            return await _analyze_with_llm(subject, snippet, cfg)
        except Exception:
            if cfg.analysis_mode == "ai_only":
                raise
            # Network / quota / parse errors degrade gracefully to rules so a
            # single bad call never blocks the whole batch.
            return _analyze_with_rules(subject, snippet, raw_headers)
    return _analyze_with_rules(subject, snippet, raw_headers)


# --- LLM path ---------------------------------------------------------------

async def _analyze_with_llm(subject: str, snippet: str, cfg: AiConfig) -> AnalysisResult:
    # Truncate to control latency / cost.
    snippet = (snippet or "")[:800]
    user_content = f"主题：{subject or '(无主题)'}\n正文摘要：{snippet or '(无正文)'}"

    if cfg.provider == "anthropic":
        content = await _anthropic_chat(cfg, user_content)
    else:
        content = await _openai_chat(cfg, user_content)

    parsed = json.loads(content)
    return AnalysisResult(
        category=_clamp_category(parsed.get("category")),
        is_advertisement=bool(parsed.get("is_advertisement")),
        priority_score=_clamp_int(parsed.get("priority_score"), 0, 100),
        summary=str(parsed.get("summary") or "")[:300],
        suggested_action=_clamp_action(parsed.get("suggested_action")),
    )


async def _openai_chat(cfg: AiConfig, user_content: str) -> str:
    """OpenAI-compatible chat completions (OpenAI / DeepSeek / Kimi / Ollama ...)."""
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 300,
    }
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    return data["choices"][0]["message"]["content"]


async def _anthropic_chat(cfg: AiConfig, user_content: str) -> str:
    """Anthropic Messages API (provider=anthropic)."""
    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.model,
        "max_tokens": 400,
        "temperature": 0,
        "messages": [{"role": "user", "content": user_content}],
        "system": _SYSTEM_PROMPT,
    }
    url = cfg.base_url.rstrip("/") + "/messages"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    return "".join(block.get("text", "") for block in data.get("content", []))


# --- rule-based fallback ----------------------------------------------------

# Ordered keyword → category mapping. First match wins, so specific categories
# are listed before broad ones (meeting before work, marketing before other).
_KEYWORD_RULES: list[tuple[tuple[str, ...], str, str, int]] = [
    (("会议", "meeting", "议程", "日程", "invite", "calendar", "invitation"),
     "meeting", "reply", 80),
    (("验证码", "验证", "登录", "安全", "提醒", "notice", "alert", "confirm", "verification"),
     "notification", "note", 45),
    (("账单", "发票", "银行", "信用卡", "对账单", "receipt", "invoice", "payment", "报销", "转账", "缴费"),
     "finance", "review", 60),
    (("订单", "物流", "快递", "发货", "退换", "order", "shipping", "tracking", "配送", "运单"),
     "shopping", "review", 50),
    (("机票", "酒店", "行程", "航班", "预订", "flight", "hotel", "booking", "reservation", "入住"),
     "travel", "review", 55),
    (("项目", "报告", "需求", "评审", "deadline", "review", "合同", "审批", "task", "report", "assignment"),
     "work", "reply", 75),
    (("退订", "unsubscribe", "优惠", "促销", "折扣", "特惠", "限时", "deal", "sale", "营销", "立即抢购", "活动截止"),
     "marketing", "ignore", 10),
    (("newsletter", "周报", "资讯", "期刊", "digest", "subscribe", "订阅期刊"),
     "newsletter", "note", 20),
]

_AD_KEYWORDS = (
    "退订", "unsubscribe", "优惠", "促销", "折扣", "特惠", "限时", "newsletter",
    "订阅", "deal", "sale", "营销", "活动截止", "click here", "立即抢购",
)


def _analyze_with_rules(subject: str, snippet: str, raw_headers: dict[str, Any] | None) -> AnalysisResult:
    raw_headers = raw_headers or {}
    text = f"{subject} {snippet}".lower()
    list_unsub = bool(raw_headers.get("List-Unsubscribe"))
    precedence = str(raw_headers.get("Precedence") or "").lower()

    is_ad = list_unsub or precedence == "bulk" or any(k in text for k in _AD_KEYWORDS)

    category, action, score = "other", "note", 50
    for keywords, cat, act, sc in _KEYWORD_RULES:
        if any(k in text for k in keywords):
            category, action, score = cat, act, sc
            break

    # List-Unsubscribe header with no keyword match still nudges to marketing.
    if list_unsub and category == "other":
        category, action, score = "marketing", "ignore", 15

    summary = (snippet or subject or "")[:50].strip()
    if not summary:
        summary = "（无摘要）"
    return AnalysisResult(
        category=category,
        is_advertisement=is_ad,
        priority_score=score,
        summary=summary,
        suggested_action=action,
    )


# --- output sanitisation ----------------------------------------------------

def _clamp_category(value: Any) -> str:
    value = str(value or "").strip()
    return value if value in CATEGORIES else "other"


def _clamp_action(value: Any) -> str:
    value = str(value or "").strip()
    return value if value in ACTIONS else "note"


def _clamp_int(value: Any, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 50
    return max(lo, min(hi, v))
