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
import re
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

# ---------------------------------------------------------------------------
# Prompt v2 — tuned after studying Inbox Zero / Mail-0 Zero / Dify email
# templates: reasoning before conclusion, per-category definitions with
# counter-examples, worked few-shots including the classic traps (an order
# email WITH an unsubscribe footer is NOT an ad), explicit low-confidence
# escape hatch instead of forced classification.
#
# Two variants:
# * full  — cloud models: reasoning field + 4 few-shots (≈900 tokens)
# * lite  — local 8B-class models (auto when base_url looks like Ollama):
#           no reasoning field, one-line definitions, 1 trap few-shot
#           (≈300 tokens) so a 4k-context local model never overflows.
# ---------------------------------------------------------------------------

_FULL_HEAD = """你是一名严谨的邮件分析助手。分析给定的邮件，只返回一个 JSON 对象
（不要任何额外文字、不要 markdown 代码块），字段必须完整：
{
  "reasoning": "≤40字的判定依据（先推理，后结论）",
  "category": "见分类说明",
  "confidence": 0到1的小数，对分类判断的确信度，
  "is_advertisement": true 或 false,
  "priority_score": 0-100 的整数，越需要用户尽快处理越高，
  "summary": "一句话中文摘要，不超过50字",
  "suggested_action": "reply|review|note|ignore 之一"
}

分类说明（选择语义最匹配的类别标识；仅在现有类别确实都无法描述且 confidence≥0.7
时才新建一个 2-8 个汉字或 1-3 个英文单词的小写标识）：
- work: 项目/任务/报告/审批/同事协作，需要用户回应
- meeting: 会议邀请/日程/议程（日历邀请类，常带 ICS 附件或时间地点）
- finance: 账单/发票/银行/报销/支付确认（含"优惠"字样的账单仍是 finance）
- notification: 验证码/安全提醒/系统通知，无需回复
- social: 人脉/好友请求/社区消息
- travel: 机票/酒店/行程/预订确认
- shopping: 订单确认/物流/退换货（即使邮件末尾有"退订"链接也不是广告）
- marketing: 批量促销/推广/限时优惠，以引导购买为主要目的
- newsletter: 订阅的资讯/周报/期刊，以内容阅读为目的
- personal: 私人往来
- other: 以上都不是，或信息太少无法判断

判定规则（按优先级）：
1. 准确优先于完整：信息不足或拿不准时，category 用 other 且 confidence≤0.5，不要强行归类。
2. 退订链接/List-Unsubscribe 只是弱信号：交易类邮件（订单/账单/行程）常带退订页脚，
   若邮件主体是用户主动产生的交易结果，is_advertisement 应为 false。
3. 以"引导购买/点击推广"为主要目的才算广告；同时满足营销与交易特征时看邮件主体。
4. priority_score：需要用户回应的工作/会议 70-95；交易确认 45-65；验证码/通知 30-55；
   营销/简报 0-20。
5. confidence 诚实评分：示例仅作参考，仍需独立判断当前邮件。"""

_FULL_FEW_SHOTS = """
示例（输入→输出 JSON）：
1. 主题"【限时特惠】秋季焕新 全场5折" 正文"亲爱的会员 全场限时5折 立即抢购 退订请点这里"
   → {"reasoning":"批量促销，以引导购买为目的","category":"marketing","confidence":0.95,"is_advertisement":true,"priority_score":10,"summary":"秋季全场5折限时促销","suggested_action":"ignore"}
2. 主题"您的订单已发货" 正文"亲爱的顾客 订单20260901已由顺丰揽收 运单号SF123 如不想再接收物流邮件可退订"
   → {"reasoning":"交易结果的物流通知，退订页脚不改变其性质","category":"shopping","confidence":0.9,"is_advertisement":false,"priority_score":55,"summary":"订单已发货含顺丰运单号","suggested_action":"review"}
3. 主题"关于周三需求评审的安排" 正文"各位好 附件是评审文档 请周三上午10点前查看并回复确认 涉及项目上线时间"
   → {"reasoning":"同事协作且需要回复确认","category":"work","confidence":0.9,"is_advertisement":false,"priority_score":80,"summary":"周三需求评审需回复确认","suggested_action":"reply"}
4. 主题"你好" 正文"在吗？"
   → {"reasoning":"信息过少无法判断","category":"other","confidence":0.3,"is_advertisement":false,"priority_score":50,"summary":"仅有问候语无正文内容","suggested_action":"note"}"""

_LITE_HEAD = """你是邮件分类器。只返回一个 JSON 对象，不要任何解释或代码块，字段完整：
{"category":"类别标识","confidence":0到1的小数,"is_advertisement":true或false,
 "priority_score":0到100的整数,"summary":"≤40字中文摘要","suggested_action":"reply|review|note|ignore 之一"}

类别（选最匹配的一个；确实都不匹配才用 other，拿不准时 confidence≤0.5）：
work=工作任务；meeting=会议日程；finance=账单发票银行；notification=验证码系统通知；
social=社交人脉；travel=机票酒店行程；shopping=订单物流退换；marketing=批量促销广告；
newsletter=订阅资讯；personal=私人邮件；other=其他或信息不足。

规则：交易邮件（订单/账单/行程）即使末尾有"退订"链接也不是广告；以引导购买为目的才算广告；
需要回复的工作邮件优先级 70-95，营销简报 0-20。"""

_LITE_FEW_SHOT = """
示例：主题"您的订单已发货" 正文"订单已由顺丰揽收 运单号SF123 如不想接收可退订"
→ {"category":"shopping","confidence":0.9,"is_advertisement":false,"priority_score":55,"summary":"订单已发货含运单号","suggested_action":"review"}"""

_PROMPT_WITH_CATEGORIES = """
现有类别（标识: 名称）：{categories}
只优先复用上面的现有标识。"""

_PROMPT_NO_CATEGORIES = """
为这封邮件新建一个简洁的小写类别标识（2-8 个汉字或 1-3 个英文单词）。"""

_PROMPT_MEMORIES = """
用户偏好记忆（分析时优先遵守）：
{memories}"""

_PROMPT_FEEDBACK_FULL = """
用户对该发件人历史邮件的人工修正（重要参考；当前邮件仍需独立判断）：
{feedback}"""

_PROMPT_FEEDBACK_LITE = """
用户历史修正（同发件人，仅作提示）：
{feedback}"""


def _build_system_prompt(
    categories: list[str] | None,
    memories: list[str] | None = None,
    feedback: list[dict] | None = None,
    *,
    lite: bool = False,
) -> str:
    head = _LITE_HEAD if lite else _FULL_HEAD
    shots = _LITE_FEW_SHOT if lite else _FULL_FEW_SHOTS
    prompt = head + shots + (
        _PROMPT_WITH_CATEGORIES.format(categories="；".join(categories))
        if categories
        else _PROMPT_NO_CATEGORIES
    )
    if memories:
        listed = "\n".join(f"- {m}" for m in memories)
        prompt += _PROMPT_MEMORIES.format(memories=listed)
    if feedback:
        prompt += _render_feedback_block(feedback, lite=lite)
    return prompt


def _render_feedback_block(feedback: list[dict] | None, *, lite: bool = False) -> str:
    """Render user corrections into the prompt few-shot block (P1).

    ``feedback`` items: ``{"subject": str, "corrected_category": str|None,
    "corrected_ad": bool|None}`` — the human-corrected verdicts for recent
    mail from the same sender. Empty/None → no block at all.
    """
    items = [f for f in (feedback or []) if isinstance(f, dict)]
    if not items:
        return ""
    lines = []
    for f in items[:5]:
        subject = str(f.get("subject") or "")[:40].replace("\n", " ")
        cat = str(f.get("corrected_category") or "").strip()
        ad = f.get("corrected_ad")
        if not cat and ad is None:
            continue
        bits = []
        if cat:
            bits.append(f"正确类别={cat}")
        if ad is not None:
            bits.append(f"广告={'是' if ad else '否'}")
        lines.append(f'- 主题"{subject}" → {"；".join(bits)}（用户手工修正）')
    if not lines:
        return ""
    template = _PROMPT_FEEDBACK_LITE if lite else _PROMPT_FEEDBACK_FULL
    return template.format(feedback="\n".join(lines))


@dataclass
class AnalysisResult:
    category: str
    is_advertisement: bool
    priority_score: int
    summary: str
    suggested_action: str
    confidence: float | None = None  # model self-reported certainty (0-1)


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
    categories: list[str] | None = None,
    memories: list[str] | None = None,
    sender: str | None = None,
    feedback: list[dict] | None = None,
) -> AnalysisResult:
    """Analyze one email according to the effective AI config.

    ``categories`` lists the currently-registered category names; the LLM is
    told to reuse them and only invent a new one when nothing fits.
    ``memories`` are the user's distilled preferences, injected into the
    system prompt so classification / priority follows them.
    ``sender`` (display name + address) is fed to the LLM as a strong
    classification signal — it sees what rules fallback alone used to see.
    ``feedback`` is the user's recent corrections for this sender (list of
    ``{subject, corrected_category, corrected_ad}``); it is injected as
    few-shot context, and its presence disables the deterministic fast-path
    so a human correction always wins over a hard-coded rule.
    auto: LLM when configured, graceful rule fallback otherwise.
    ai_only: always LLM (errors propagate).
    rules_only: pure programmatic analysis.
    """
    cfg = config or _default_config()
    if cfg.use_ai:
        # Deterministic fast-path (P2): unambiguous evidence → skip the LLM.
        # Any user feedback for this sender disables it (corrections override).
        fast = _deterministic_result(subject, snippet, raw_headers, sender)
        if fast is not None and not feedback:
            return fast
        try:
            return await _analyze_with_llm(
                subject, snippet, raw_headers, cfg, categories, memories, sender,
                feedback=feedback,
            )
        except Exception:
            if cfg.analysis_mode == "ai_only":
                raise
            # Network / quota / parse errors degrade gracefully to rules so a
            # single bad call never blocks the whole batch.
            return _analyze_with_rules(subject, snippet, raw_headers)
    return _analyze_with_rules(subject, snippet, raw_headers)


# --- LLM path ---------------------------------------------------------------

# Body budget. 2500 chars ≈ 800-1600 tokens of Chinese/English — plenty for
# classification and safe for cloud models. Local 8B-class models often run
# 4k contexts (Ollama default), so the lite prompt + a shorter body keeps the
# whole request comfortably inside. Override both via AI_MAX_BODY_CHARS.
_BODY_CHARS_CLOUD = 2500
_BODY_CHARS_LOCAL = 1200


def _is_local_model(cfg: AiConfig) -> bool:
    """Rough "is this a small local server" check driving the lite prompt.

    Local Ollama/LM Studio endpoints serve 7B-14B models with 4k-8k contexts
    and weak instruction-following — they need the compact prompt variant.
    """
    return _is_ollama(cfg.base_url) or "11434" in (cfg.base_url or "") or "1234" in (cfg.base_url or "")


async def _analyze_with_llm(
    subject: str,
    snippet: str,
    raw_headers: dict[str, Any] | None,
    cfg: AiConfig,
    categories: list[str] | None,
    memories: list[str] | None = None,
    sender: str | None = None,
    feedback: list[dict] | None = None,
) -> AnalysisResult:
    lite = _is_local_model(cfg)
    limit = settings.ai_max_body_chars or (_BODY_CHARS_LOCAL if lite else _BODY_CHARS_CLOUD)
    # Truncate to control latency / cost.
    body = (snippet or "")[:limit]
    signals = _format_signal_headers(raw_headers)
    user_content = _build_user_content(sender, signals, subject, body)
    hints = _deterministic_hints(subject, snippet, raw_headers)
    if hints:
        # Non-skip cases: surface the deterministic evidence so the LLM can
        # weigh it, without forcing the verdict.
        user_content += "\n确定性信号提示：" + "；".join(hints)
    system_prompt = _build_system_prompt(categories, memories, feedback, lite=lite)

    content = await _call_llm(cfg, user_content, system_prompt)
    parsed = _parse_analysis(content)
    if parsed is None:
        # One strict retry — local models occasionally wrap JSON in prose.
        retry_content = await _call_llm(
            cfg,
            user_content + "\n\n（上一次回复无法解析。请只输出一个 JSON 对象，"
            "以 { 开头、以 } 结尾，不要任何其他文字。）",
            system_prompt,
        )
        parsed = _parse_analysis(retry_content)
    if parsed is None:
        raise ValueError(f"LLM returned unparseable JSON: {content[:200]!r}")
    return AnalysisResult(
        category=_clamp_category(parsed.get("category"), categories),
        is_advertisement=bool(parsed.get("is_advertisement")),
        priority_score=_clamp_int(parsed.get("priority_score"), 0, 100),
        summary=str(parsed.get("summary") or "")[:300],
        suggested_action=_clamp_action(parsed.get("suggested_action")),
        confidence=_clamp_confidence(parsed.get("confidence")),
    )


# --- deterministic engine (P2) ----------------------------------------------
# Hard-coded verdicts only when the evidence is unambiguous; anything short of
# that goes to the LLM as a *hint* (_deterministic_hints) instead. Precision
# first: a wrong fast-path verdict can never be appealed by the model.

_OTP_RE = re.compile(r"\b\d{4,8}\b")
_OTP_WORDS = (
    "验证码", "校验码", "动态码", "动态密码",
    "verification code", "verification_code", "code is", "your code", "otp:",
)
# Explicit promo wording, subject-level only — footer words like 退订 are NOT
# promo evidence (the classic unsubscribe-footer false positive).
_PROMO_ZH = ("优惠", "折扣", "促销", "特惠", "限时", "秒杀", "立减", "满减",
             "大促", "特价", "闪购", "抢购", "低至", "预售")
_PROMO_EN_RE = re.compile(r"\b(sale|deals?|discount|promo|offer|off)\b")
# Machine-only sender local parts — bulk evidence alongside unsubscribe/promo.
_BULK_LOCALS = (
    "noreply", "no-reply", "no_reply", "donotreply", "news", "newsletter",
    "notifications", "promo", "deals", "sale", "marketing",
)
# Transactional content markers veto the marketing fast-path: order/bill/
# travel mails often ship from no-reply@ WITH an unsubscribe footer.
_TRANSACTIONAL_MARKERS = (
    "订单", "运单", "物流", "快递", "包裹", "账单", "发票", "还款", "退款",
    "支付", "行程", "航班", "酒店", "预订", "车票", "取件码", "对账",
    "order", "tracking", "invoice", "payment", "refund", "shipment",
    "delivery", "booking", "flight", "statement",
)


def _sender_local_part(sender: str | None) -> str:
    """``张工 <zhang@x.com>`` → ``zhang``; bare address → its local part."""
    value = (sender or "").strip()
    if "<" in value and ">" in value:
        value = value.split("<", 1)[1].rsplit(">", 1)[0]
    value = value.strip().lower()
    return value.split("@", 1)[0] if "@" in value else ""


def _deterministic_result(
    subject: str,
    snippet: str,
    raw_headers: dict[str, Any] | None,
    sender: str | None,
) -> AnalysisResult | None:
    """Fast-path verdict for unambiguous mail; None = let the LLM decide.

    Rules, in order:
    1. Google-style "Invitation:" subject → meeting.
    2. OTP keyword + a 4-8 digit code → notification.
    3. Explicit promo subject + bulk evidence (bulk sender local part,
       List-Unsubscribe, or Precedence: bulk) and NO transactional marker
       → marketing.
    """
    if not settings.ai_skip_deterministic:
        return None
    headers = raw_headers or {}
    subj = (subject or "").strip()
    subj_low = subj.lower()
    text = f"{subj} {snippet or ''}"[:1200].lower()

    if subj_low.startswith("invitation:") or subj_low.startswith("updated invitation:"):
        return AnalysisResult("meeting", False, 80, subj[:50] or "日历邀请", "reply", 0.95)

    if any(w in text for w in _OTP_WORDS) and _OTP_RE.search(text):
        return AnalysisResult("notification", False, 45, subj[:50] or "验证码通知", "note", 0.9)

    promo_subject = any(w in subj for w in _PROMO_ZH) or bool(_PROMO_EN_RE.search(subj_low))
    if promo_subject:
        local = _sender_local_part(sender)
        bulk = (
            any(local.startswith(k) or k in local for k in _BULK_LOCALS)
            or bool(headers.get("List-Unsubscribe"))
            or str(headers.get("Precedence") or "").lower() == "bulk"
        )
        if bulk and not any(m in text for m in _TRANSACTIONAL_MARKERS):
            return AnalysisResult("marketing", True, 5, subj[:50] or "促销广告", "ignore", 0.9)
    return None


def _deterministic_hints(
    subject: str,
    snippet: str,
    raw_headers: dict[str, Any] | None,
) -> list[str]:
    """Deterministic evidence surfaced to the LLM when we did NOT fast-path.

    Short, hedged lines — the model still owns the verdict (plan P2-2).
    """
    headers = raw_headers or {}
    hints: list[str] = []
    subj = (subject or "").strip()
    text = f"{subj} {snippet or ''}"[:1200]
    if headers.get("List-Unsubscribe") or str(headers.get("Precedence") or "").lower() == "bulk":
        hints.append(
            "带批量发送特征（List-Unsubscribe/Precedence bulk），"
            "但交易类邮件也常带退订页脚，需结合主体内容判断"
        )
    if any(w in text.lower() for w in _OTP_WORDS) and _OTP_RE.search(text):
        hints.append("疑似验证码/系统通知")
    subj_low = subj.lower()
    if subj_low.startswith("invitation:") or subj_low.startswith("updated invitation:"):
        hints.append("疑似日历邀请")
    return hints


def _call_llm(cfg: AiConfig, user_content: str, system_prompt: str) -> str:
    """Provider-dispatching chat call with per-variant budgets."""
    if cfg.provider == "anthropic":
        return _anthropic_chat(cfg, user_content, system_prompt, max_tokens=1024, timeout=90)
    return _openai_chat(cfg, user_content, system_prompt, max_tokens=1024, timeout=90)


def _format_signal_headers(raw_headers: dict[str, Any] | None) -> str:
    """Render bulk-mail evidence from stored headers for the LLM.

    These are the same signals the rule fallback uses (List-Unsubscribe /
    Precedence) — previously the LLM path never saw them.
    """
    raw_headers = raw_headers or {}
    lines = []
    if raw_headers.get("List-Unsubscribe"):
        lines.append("List-Unsubscribe=有")
    precedence = str(raw_headers.get("Precedence") or "").lower()
    if precedence:
        lines.append(f"Precedence={precedence}")
    if raw_headers.get("Auto-Submitted"):
        lines.append(f"Auto-Submitted={raw_headers['Auto-Submitted']}")
    return "；".join(lines) if lines else "无"


def _build_user_content(
    sender: str | None, signals: str, subject: str, body: str
) -> str:
    parts = [f"发件人：{sender or '（未知）'}", f"批量信号头：{signals}"]
    parts.append(f"主题：{subject or '(无主题)'}")
    parts.append(f"正文：{body or '(无正文)'}")
    return "\n".join(parts)


def _extract_json(content: str) -> dict[str, Any] | None:
    """Tolerant JSON extraction.

    Handles the real-world failure modes of 8B-class local models: markdown
    code fences, leading/trailing prose, and trailing commas after nested
    objects. Returns None when nothing JSON-shaped is found.
    """
    text = (content or "").strip()
    if not text:
        return None
    # Strip a wrapping code fence if present.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    if start < 0:
        return None
    # Find the matching closing brace (strings-aware, no nesting depth tricks).
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Trailing commas are the most common local-model slip.
                    cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        return None
    return None


def _parse_analysis(content: str) -> dict[str, Any] | None:
    """Extract the analysis JSON, unwrapping {"analysis": {...}} style wrappers."""
    data = _extract_json(content)
    if data is None:
        return None
    # Some providers force json_object and the model nests the payload.
    for key in ("analysis", "result", "data"):
        inner = data.get(key) if isinstance(data, dict) else None
        if isinstance(inner, dict):
            return inner
    return data if isinstance(data, dict) else None


def _clamp_confidence(value: Any) -> float | None:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, c))


def _llm_error_hint(text: str) -> str:
    """Append an actionable Chinese hint when the provider rejects a request.

    Two distinct failure classes get distinct hints:
    1. Input too long / context overflow — common on local small-context models
       (the draft path retries with a truncated body automatically).
    2. The configured model name doesn't match an available model.
    """
    low = (text or "").lower()
    if any(k in low for k in ("context", "num_predict", "max length", "too long", "exceeds", "token")):
        return (
            "（提示：输入过长——本地/小上下文模型放不下整封邮件，已自动尝试截断重试。"
            "若仍失败，请换用上下文更大的模型，或换一封较短的邮件）"
        )
    if any(
        k in low
        for k in (
            "invalid model",
            "model not found",
            "model does not exist",
            "model not exist",
            "unknown model",
            "model_error",
        )
    ):
        return (
            "（提示：模型名称无效——请确认设置中填写的模型名与本地/云端实际可用的模型完全一致，"
            "注意大小写与 :tag 标签，如 qwen2.5:7b；本地模型请先确认已用 ollama pull 拉取）"
        )
    return ""


def _is_ollama(base_url: str) -> bool:
    """Best-effort detection of a local Ollama server.

    Ollama's OpenAI-compatible endpoint ignores ``think`` and lets thinking
    models (qwen3 & co.) burn the whole token budget + minutes on reasoning
    before answering. Its native ``/api/chat`` honours ``think: false``, which
    is dramatically faster, so we route Ollama calls through that endpoint.
    """
    url = (base_url or "").lower()
    return "11434" in url or "ollama" in url


async def _ollama_chat(
    cfg: AiConfig,
    user_content: str,
    system_prompt: str,
    max_tokens: int,
    json_mode: bool,
    timeout: float,
) -> str:
    """Native Ollama /api/chat with thinking disabled (fast path)."""
    body: dict = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        # Disable reasoning for thinking models — otherwise they spend the
        # whole budget (and minutes) thinking before answering.
        "think": False,
        "options": {"temperature": 0, "num_predict": max_tokens},
    }
    # Context window priority: per-config resolved value (profile manual /
    # probe cache) > env AI_NUM_CTX > omit (server default, e.g. Modelfile).
    # We never force a small window at runtime — Ollama would silently
    # truncate long prompts.
    num_ctx = cfg.resolved_num_ctx or (settings.ai_num_ctx if settings.ai_num_ctx > 0 else None)
    if num_ctx and num_ctx > 0:
        body["options"]["num_ctx"] = num_ctx
    if json_mode:
        body["format"] = "json"
    base = cfg.base_url.rstrip("/")
    url = base.removesuffix("/v1") + "/api/chat"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers={"Content-Type": "application/json"}, json=body)
        if resp.status_code != 200:
            raise RuntimeError(
                f"LLM HTTP {resp.status_code}: {resp.text[:200]}{_llm_error_hint(resp.text)}"
            )
        data = resp.json()
    return data["message"]["content"]


async def _openai_chat(
    cfg: AiConfig,
    user_content: str,
    system_prompt: str,
    max_tokens: int = 300,
    json_mode: bool = True,
    timeout: float = 30,
) -> str:
    """OpenAI-compatible chat completions (OpenAI / DeepSeek / Kimi / Ollama ...).

    Ollama is routed through its native ``/api/chat`` with thinking disabled
    (see ``_ollama_chat``). ``json_mode`` forces a JSON answer; set it to
    ``False`` for calls that must emit free-form prose (e.g. the chat QA
    answer with its ``---CITED_IDS---`` trailing marker). ``timeout`` should be
    raised for long outputs / slow local models.
    """
    if _is_ollama(cfg.base_url):
        return await _ollama_chat(
            cfg, user_content, system_prompt, max_tokens=max_tokens, json_mode=json_mode, timeout=timeout
        )

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
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(
                f"LLM HTTP {resp.status_code}: {resp.text[:200]}{_llm_error_hint(resp.text)}"
            )
        data = resp.json()
    return data["choices"][0]["message"]["content"]


async def _anthropic_chat(
    cfg: AiConfig, user_content: str, system_prompt: str, max_tokens: int = 400, timeout: float = 30
) -> str:
    """Anthropic Messages API (provider=anthropic)."""
    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": user_content}],
        "system": system_prompt,
    }
    url = cfg.base_url.rstrip("/") + "/messages"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Anthropic HTTP {resp.status_code}: {resp.text[:200]}{_llm_error_hint(resp.text)}"
            )
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

def _clamp_category(value: Any, known: list[str] | None = None) -> str:
    """Normalize an LLM category value.

    Known categories are returned verbatim (reuse); unknown values are trimmed,
    truncated and returned as-is so the analysis service can auto-register them
    as new categories. Falls back to ``other`` for empty/garbage input.
    """
    value = str(value or "").strip().strip("\"'`")
    if not value or len(value) > 64:
        return "other"
    if known and value in known:
        return value
    if value.lower() in CATEGORIES:
        return value.lower()
    # Accept a brand-new, sensible category name from the LLM.
    return value[:64]


def _clamp_action(value: Any) -> str:
    value = str(value or "").strip()
    return value if value in ACTIONS else "note"


def _clamp_int(value: Any, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 50
    return max(lo, min(hi, v))
