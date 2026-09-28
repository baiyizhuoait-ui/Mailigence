"""AI analysis accuracy eval — measures, don't guess.

Two layers:
* ``test_rules_baseline`` — always runs (no network). The keyword fallback is
  the floor every LLM must beat; it also documents the rules' known failures
  (unsubscribe-footer false positives, personal/social blind spots).
* ``test_llm_accuracy`` — live eval against the configured provider, gated
  behind ``RUN_AI_EVAL=1`` so normal test runs never burn tokens:

      cd backend && RUN_AI_EVAL=1 python -m pytest tests/test_analyzer_eval.py -s

The case set deliberately includes the traps that made v1 accuracy poor:
transactional mail WITH unsubscribe footers, bills containing promo wording,
newsletters that are not ads, and low-information mails that must land in
``other`` with low confidence instead of being force-classified.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

import pytest

from app.services.ai_analyzer import (
    CATEGORIES,
    AnalysisResult,
    _analyze_with_rules,
    _build_system_prompt,
    _deterministic_result,
    _extract_json,
    _format_signal_headers,
    _is_local_model,
    _parse_analysis,
    _render_feedback_block,
    analyze_email,
)
from app.services.ai_config import AiConfig


@dataclass
class EvalCase:
    name: str
    subject: str
    body: str
    expected_category: str
    expected_is_ad: bool
    sender: str = ""
    headers: dict = field(default_factory=dict)
    action: str | None = None
    priority: tuple[int, int] | None = None  # acceptable range


def _c(**kw) -> EvalCase:
    return EvalCase(**kw)


EVAL_CASES: list[EvalCase] = [
    # --- marketing (真广告) ---
    _c(name="promo-zh", subject="【限时特惠】秋季焕新季 全场5折起", sender="会员中心 <news@shop.example>",
       body="亲爱的会员，全场限时5折，点击立即抢购！活动截止本周日。如不想再收到邮件，退订请点这里。",
       expected_category="marketing", expected_is_ad=True, action="ignore", priority=(0, 25)),
    _c(name="promo-en", subject="50% OFF Summer Sale — Today Only", sender="Deals <deals@store.example>",
       headers={"List-Unsubscribe": "<https://store.example/unsub>"},
       body="Huge discounts on everything. Shop the deal before it ends. Unsubscribe anytime.",
       expected_category="marketing", expected_is_ad=True, action="ignore", priority=(0, 25)),
    _c(name="double11", subject="双十一预售开启 定金翻倍", sender="天猫 <promo@example>",
       body="年度大促，预售定金膨胀，跨店满减，立即抢购。",
       expected_category="marketing", expected_is_ad=True, action="ignore", priority=(0, 25)),
    _c(name="flash-sale-zh", subject="限时闪购 全场特价", sender="特卖 <sale@example>",
       body="今日闪购特惠专场，折扣低至三折，手慢无。",
       expected_category="marketing", expected_is_ad=True, action="ignore", priority=(0, 25)),
    # --- shopping (带退订页脚的交易邮件 — v1 高发误判) ---
    _c(name="order-shipped-trap", subject="您的订单已发货", sender="顺丰商城 <no-reply@sf.example>",
       body="亲爱的顾客，您的订单 20260901 已由顺丰揽收，运单号 SF123456789，点击查看物流。如不想接收此类邮件可退订。",
       expected_category="shopping", expected_is_ad=False, action="review", priority=(40, 70)),
    _c(name="order-shipped-en-trap", subject="Your order has shipped", sender="Shop <orders@shop.example>",
       body="Tracking number 1Z999AA10123456784. Your package arrives in 3 days. You are receiving this because you subscribed to shipping updates. Unsubscribe.",
       expected_category="shopping", expected_is_ad=False, action="review", priority=(40, 70)),
    _c(name="logistics", subject="快递派送提醒", sender="菜鸟 <notice@cainiao.example>",
       body="您的包裹已到达菜鸟驿站，凭取件码 8823 领取。",
       expected_category="shopping", expected_is_ad=False, priority=(40, 70)),
    _c(name="refund", subject="退款已原路返回", sender="淘宝 <pay@taobao.example>",
       body="您申请的退换货已处理，退款 89.00 元将在 1-3 个工作日内到账。",
       expected_category="shopping", expected_is_ad=False, priority=(40, 70)),
    # --- finance (含营销字样的账单 — 陷阱) ---
    _c(name="invoice-zh", subject="九月企业宽带账单", sender="中国电信 <bill@example>",
       body="您的9月账单已出，合计 320 元，请及时缴费，避免影响使用。",
       expected_category="finance", expected_is_ad=False, action="review", priority=(45, 75)),
    _c(name="bill-promo-trap", subject="您的信用卡账单（本月有优惠活动）", sender="招商银行 <creditcard@cmb.example>",
       body="您本期账单金额 2,350 元，最后还款日 10 月 12 日。分期可享优惠费率。",
       expected_category="finance", expected_is_ad=False, action="review", priority=(45, 75)),
    _c(name="invoice-en", subject="Your statement is ready", sender="Bank <statements@bank.example>",
       body="Your credit card statement is available. Payment due: Oct 12. Minimum payment: $45.",
       expected_category="finance", expected_is_ad=False, action="review", priority=(45, 75)),
    _c(name="receipt-en", subject="Payment received — invoice #2043", sender="SaaS Billing <billing@saas.example>",
       body="We received your payment of $99.00 for the Pro plan. Invoice attached.",
       expected_category="finance", expected_is_ad=False, priority=(45, 75)),
    # --- notification ---
    _c(name="otp-zh", subject="【某云】验证码", sender="某云 <noreply@cloud.example>",
       body="验证码 482913，5 分钟内有效，请勿泄露给他人。",
       expected_category="notification", expected_is_ad=False, action="note", priority=(25, 60)),
    _c(name="security-en", subject="Security alert: new sign-in", sender="Google <no-reply@accounts.example>",
       body="Your account was just signed in from a new device. If this was you, you can safely ignore this alert.",
       expected_category="notification", expected_is_ad=False, action="note", priority=(25, 60)),
    _c(name="server-alert", subject="服务器 CPU 告警", sender="监控平台 <alert@monitor.example>",
       body="prod-web-01 CPU 使用率连续 5 分钟超过 90%，请登录控制台查看。",
       expected_category="notification", expected_is_ad=False, priority=(25, 65)),
    # --- work ---
    _c(name="work-review-zh", subject="关于周三需求评审的安排", sender="张工 <zhang@company.example>",
       body="各位好，附件是评审文档，请周三上午 10 点前查看并回复确认，涉及项目上线时间。",
       expected_category="work", expected_is_ad=False, action="reply", priority=(65, 100)),
    _c(name="work-deadline-en", subject="Q3 report deadline moved up", sender="Manager <boss@company.example>",
       body="The quarterly report deadline moved to this Friday. Please send me your section and any blockers today.",
       expected_category="work", expected_is_ad=False, action="reply", priority=(65, 100)),
    _c(name="work-contract", subject="采购合同审批", sender="采购部 <procure@company.example>",
       body="附件是新版采购合同，请审批确认后回传，走下周付款流程。",
       expected_category="work", expected_is_ad=False, action="reply", priority=(65, 100)),
    _c(name="work-okr", subject="Q3 OKR 填报提醒", sender="HR 专员 <hr@company.example>",
       body="本季度 OKR 需在本周五前完成填报，报告模板见附件。",
       expected_category="work", expected_is_ad=False, priority=(60, 100)),
    # --- meeting ---
    _c(name="meeting-invite-zh", subject="会议邀请：产品周会", sender="日历 <calendar@company.example>",
       body="张工邀请您参加周三 14:00-15:00 的产品周会，地点 A 座 302，议程见附件。",
       expected_category="meeting", expected_is_ad=False, action="reply", priority=(65, 100)),
    _c(name="meeting-en", subject="Invitation: Sprint Planning @ Thu 10am", sender="Calendar <calendar@example>",
       body="You are invited to Sprint Planning. Agenda attached.",
       expected_category="meeting", expected_is_ad=False, priority=(65, 100)),
    # --- travel ---
    _c(name="flight-zh", subject="航班出行提醒", sender="航旅纵横 <notice@example>",
       body="您 10 月 2 日 MU5107 上海虹桥—北京首都的航班，值机已开放，行程信息请查看附件。",
       expected_category="travel", expected_is_ad=False, priority=(40, 75)),
    _c(name="hotel-en", subject="Your hotel booking confirmation", sender="Booking <confirm@booking.example>",
       body="Reservation #12345 at Grand Hotel, check-in Oct 2, check-out Oct 5.",
       expected_category="travel", expected_is_ad=False, priority=(40, 75)),
    _c(name="train-zh", subject="火车票预订成功", sender="12306 <notice@example>",
       body="您已成功预订 10 月 1 日 G102 次列车二等座，行程单请在 app 内查看。",
       expected_category="travel", expected_is_ad=False, priority=(40, 75)),
    # --- newsletter (订阅内容 ≠ 广告 — 陷阱) ---
    _c(name="digest-zh", subject="科技周刊 第 128 期", sender="订阅 <weekly@tech.example>",
       headers={"List-Unsubscribe": "<https://tech.example/unsub>"},
       body="本周 AI 领域重要资讯汇总：模型进展、开源动态、行业融资。您订阅本刊后每周一发。",
       expected_category="newsletter", expected_is_ad=False, action="note", priority=(0, 35)),
    _c(name="digest-en", subject="This week in web dev", sender="Newsletter <digest@web.example>",
       body="You subscribed to this digest. Top stories: framework releases, browser updates, performance tips.",
       expected_category="newsletter", expected_is_ad=False, action="note", priority=(0, 35)),
    _c(name="product-updates-trap", subject="Product updates — September", sender="SaaS <updates@saas.example>",
       headers={"List-Unsubscribe": "<https://saas.example/unsub>"},
       body="You subscribed to product updates. New this month: dark mode, API v2, export improvements.",
       expected_category="newsletter", expected_is_ad=False, priority=(0, 35)),
    # --- social / personal / other ---
    _c(name="social-linkedin", subject="Add me on LinkedIn", sender="Li Lei <leilei@linkedin.example>",
       body="Li Lei would like to connect with you on LinkedIn.",
       expected_category="social", expected_is_ad=False, priority=(30, 70)),
    _c(name="personal", subject="周末爬山吗", sender="韩梅梅 <han@example>",
       body="这周六天气不错，去爬香山吗？早点出发避开人流。",
       expected_category="personal", expected_is_ad=False, priority=(40, 80)),
    _c(name="ambiguous", subject="你好", sender="陌生 <unknown@example>",
       body="在吗？",
       expected_category="other", expected_is_ad=False, action="note"),
]


# --- unit tests for the robustness layer (no network) ------------------------


def test_extract_json_handles_fences_and_prose():
    fenced = '```json\n{"category":"work"}\n```'
    assert _extract_json(fenced) == {"category": "work"}
    assert _extract_json('好的，这是结果：{"category":"other"} 请查收') == {"category": "other"}
    assert _extract_json('{"category":"work",}') == {"category": "work"}  # trailing comma
    assert _extract_json('{"a":"含}} brace"}') == {"a": "含}} brace"}  # braces in strings
    assert _extract_json("no json here") is None
    assert _extract_json("") is None


def test_parse_analysis_unwraps_provider_wrappers():
    assert _parse_analysis('{"analysis":{"category":"work"}}') == {"category": "work"}
    assert _parse_analysis('{"category":"work","confidence":0.9}')["category"] == "work"
    assert _parse_analysis("模型拒绝了请求") is None


def test_signal_headers_rendered():
    assert "List-Unsubscribe=有" in _format_signal_headers({"List-Unsubscribe": "<>"})
    assert "Precedence=bulk" in _format_signal_headers({"Precedence": "Bulk"})
    assert _format_signal_headers({}) == "无"


def test_local_model_detection():
    cfg = AiConfig(base_url="http://127.0.0.1:11434", api_key="x", model="qwen2.5:7b")
    assert _is_local_model(cfg) is True
    cfg2 = AiConfig(base_url="http://1234:8080/v1", api_key="x", model="m")
    assert _is_local_model(cfg2) is True
    cfg3 = AiConfig(base_url="https://api.deepseek.com/v1", api_key="x", model="m")
    assert _is_local_model(cfg3) is False


# --- P1/P2: feedback injection + deterministic engine (no network) ------------


def test_render_feedback_block():
    fb = [{"subject": "【内部活动】羽毛球招新", "corrected_category": "work", "corrected_ad": False}]
    block = _render_feedback_block(fb)
    assert "正确类别=work" in block
    assert "广告=否" in block
    assert "羽毛球" in block
    assert "仍需独立判断" in block or "手工修正" in block
    # Lite variant stays compact but keeps the verdict.
    lite = _render_feedback_block(fb, lite=True)
    assert "正确类别=work" in lite
    # Empty / None / unusable items → no block at all.
    assert _render_feedback_block([]) == ""
    assert _render_feedback_block(None) == ""
    assert _render_feedback_block([{"subject": "x", "corrected_category": " ", "corrected_ad": None}]) == ""


def test_system_prompt_includes_feedback():
    fb = [{"subject": "退款通知", "corrected_category": "shopping", "corrected_ad": False}]
    prompt = _build_system_prompt(["work", "shopping"], feedback=fb)
    assert "人工修正" in prompt and "shopping" in prompt
    # Without feedback the prompt must be byte-identical to the P0 behaviour.
    assert _build_system_prompt(["work", "shopping"]) == _build_system_prompt(["work", "shopping"], feedback=None)


def test_deterministic_fast_paths():
    # Promo subject + bulk sender, no transactional markers → marketing.
    r = _deterministic_result(
        "限时闪购 全场特价", "今日闪购特惠专场，折扣低至三折。", {}, "特卖 <sale@example>"
    )
    assert r is not None and r.category == "marketing" and r.is_advertisement
    assert r.confidence is not None and r.confidence >= 0.9
    # OTP keyword + digit code → notification.
    r = _deterministic_result(
        "【某云】验证码", "验证码 482913，5 分钟内有效。", {}, "某云 <noreply@cloud.example>"
    )
    assert r is not None and r.category == "notification"
    # Calendar invite prefix → meeting.
    r = _deterministic_result(
        "Invitation: Sprint Planning @ Thu 10am", "Agenda attached.", {}, "Calendar <calendar@example>"
    )
    assert r is not None and r.category == "meeting"


def test_deterministic_vetoes():
    # Transactional content veto: order mail (even from no-reply@ with an
    # unsubscribe header) must NOT be fast-pathed to marketing.
    r = _deterministic_result(
        "您的订单已发货", "订单已由顺丰揽收，运单号SF123456789。",
        {"List-Unsubscribe": "<https://x/unsub>"}, "Shop <no-reply@sf.example>",
    )
    assert r is None
    # No promo subject → no fast-path for ambiguous mail.
    assert _deterministic_result("你好", "在吗？", {}, "陌生 <unknown@example>") is None
    # Promo subject but personal one-to-one sender without bulk evidence.
    assert _deterministic_result("给你个优惠码", "朋友推荐", {}, "韩梅梅 <han@example>") is None


def test_deterministic_disabled_by_setting(monkeypatch):
    from app.services import ai_analyzer

    monkeypatch.setattr(ai_analyzer.settings, "ai_skip_deterministic", False)
    assert (
        ai_analyzer._deterministic_result(
            "限时闪购 全场特价", "折扣", {}, "特卖 <sale@example>"
        )
        is None
    )


def test_feedback_disables_fast_path(monkeypatch):
    """With user feedback present, the LLM decides even on bulk-looking mail."""
    from app.services import ai_analyzer

    calls = {"n": 0}

    async def fake_llm(*_args, **_kwargs):
        calls["n"] += 1
        return AnalysisResult("work", False, 70, "内部活动", "reply", 0.9)

    monkeypatch.setattr(ai_analyzer, "_analyze_with_llm", fake_llm)
    cfg = AiConfig(
        analysis_mode="ai_only", provider="openai",
        base_url="http://example/v1", api_key="k", model="m",
    )
    fb = [{"subject": "【内部活动】下午茶", "corrected_category": "work", "corrected_ad": False}]
    r = asyncio.run(
        analyze_email("限时闪购 全场特价", "折扣", {}, config=cfg, sender="sale@example", feedback=fb)
    )
    assert r.category == "work" and calls["n"] == 1
    # Without feedback the fast-path answers marketing with zero LLM calls.
    r2 = asyncio.run(
        analyze_email("限时闪购 全场特价", "折扣", {}, config=cfg, sender="sale@example")
    )
    assert r2.category == "marketing" and calls["n"] == 1


# --- metrics -----------------------------------------------------------------


def _metrics(results: list[AnalysisResult]) -> dict:
    cases = EVAL_CASES
    n = len(cases)
    cat_ok = sum(1 for r, c in zip(results, cases) if r.category == c.expected_category)
    ad_ok = [r.is_advertisement == c.expected_is_ad for r, c in zip(results, cases)]
    tp = sum(1 for r, c in zip(results, cases) if r.is_advertisement and c.expected_is_ad)
    fp = sum(1 for r, c in zip(results, cases) if r.is_advertisement and not c.expected_is_ad)
    fn = sum(1 for r, c in zip(results, cases) if not r.is_advertisement and c.expected_is_ad)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    prio = []
    for r, c in zip(results, cases):
        if c.priority:
            lo, hi = c.priority
            if not (lo <= r.priority_score <= hi):
                prio.append(f"{c.name}: {r.priority_score} not in {c.priority}")
    action_ok = sum(
        1 for r, c in zip(results, cases) if c.action is None or r.suggested_action == c.action
    )
    misses = [
        f"{c.name}: got={r.category!r} want={c.expected_category!r}"
        for r, c in zip(results, cases)
        if r.category != c.expected_category
    ]
    return {
        "n": n,
        "category_acc": cat_ok / n,
        "ad_acc": sum(ad_ok) / n,
        "ad_f1": f1,
        "ad_precision": precision,
        "ad_recall": recall,
        "action_acc": action_ok / n,
        "priority_violations": prio,
        "misses": misses,
        # Rule fallback results carry confidence=None; the LLM path sets a float.
        "fallbacks": sum(1 for r in results if r.confidence is None),
    }


def _report(tag: str, m: dict) -> None:
    print(f"\n[{tag}] n={m['n']} category_acc={m['category_acc']:.2f} "
          f"ad_acc={m['ad_acc']:.2f} ad_f1={m['ad_f1']:.2f} "
          f"(P={m['ad_precision']:.2f} R={m['ad_recall']:.2f}) action_acc={m['action_acc']:.2f}")
    for line in m["misses"]:
        print(f"  MISS {line}")
    for line in m["priority_violations"]:
        print(f"  PRIO {line}")


# --- baseline: the rule fallback ---------------------------------------------


def test_rules_baseline():
    """Keyword fallback floor — must beat random, and documents its failures."""
    results = [_analyze_with_rules(c.subject, c.body, c.headers) for c in EVAL_CASES]
    m = _metrics(results)
    _report("rules-baseline", m)
    assert m["category_acc"] >= 0.6, "rules baseline regressed below floor"
    assert m["ad_f1"] >= 0.4, "rules ad-detection floor regressed"


# --- live LLM eval (opt-in) --------------------------------------------------


def _live_config() -> AiConfig | None:
    from app.services.ai_analyzer import _default_config

    cfg = _default_config()
    return cfg if cfg.use_ai else None


@pytest.mark.skipif(
    os.environ.get("RUN_AI_EVAL") != "1", reason="live LLM eval: set RUN_AI_EVAL=1"
)
def test_llm_accuracy():
    cfg = _live_config()
    if cfg is None:
        pytest.skip("No AI provider configured (.env)")

    async def run():
        out = []
        for c in EVAL_CASES:
            r = await analyze_email(
                c.subject, c.body, c.headers, config=cfg,
                categories=list(CATEGORIES), sender=c.sender,
            )
            out.append(r)
        return out

    results = asyncio.run(run())
    m = _metrics(results)
    _report(f"llm[{cfg.model}]", m)
    assert m["category_acc"] >= 0.85, f"category accuracy {m['category_acc']:.2f} < 0.85"
    assert m["ad_f1"] >= 0.8, f"ad F1 {m['ad_f1']:.2f} < 0.8"


# --- P1 live eval: classification follows user feedback (opt-in) --------------
# Static cases can't show the feedback gain (nothing was ever corrected for
# these senders), so these cases are only meaningful WITH the feedback context
# passed in — they verify the injection actually steers the verdict.

FEEDBACK_CASES: list[tuple[EvalCase, list[dict]]] = [
    (
        _c(
            name="fb-internal-activity",
            subject="【内部活动】羽毛球俱乐部招新",
            sender="行政部 <hr-blast@company.example>",
            body="公司内部活动报名：本周五下午羽毛球友谊赛，想参加的同事请在周五前填表报名，场地费由公司承担。",
            expected_category="work", expected_is_ad=False,
        ),
        [{"subject": "【内部活动】下午茶聚会报名", "corrected_category": "work", "corrected_ad": False}],
    ),
    (
        _c(
            name="fb-vendor-bulletin",
            subject="【服务通报】9月服务使用简报",
            sender="服务商 <bulletin@vendor.example>",
            body="您好，9月服务使用简报已生成：调用量、存储用量与上月持平。完整报告见控制台，如有疑问请回复本邮件。",
            expected_category="work", expected_is_ad=False,
        ),
        [{"subject": "【服务通报】8月使用简报", "corrected_category": "work", "corrected_ad": False}],
    ),
]


@pytest.mark.skipif(
    os.environ.get("RUN_AI_EVAL") != "1", reason="live LLM eval: set RUN_AI_EVAL=1"
)
def test_llm_feedback_follows():
    """P1 acceptance: with corrections injected, the model follows them."""
    cfg = _live_config()
    if cfg is None:
        pytest.skip("No AI provider configured (.env)")

    async def run(with_feedback: bool):
        out = []
        for c, fb in FEEDBACK_CASES:
            r = await analyze_email(
                c.subject, c.body, c.headers, config=cfg,
                categories=list(CATEGORIES), sender=c.sender,
                feedback=fb if with_feedback else None,
            )
            out.append(r)
        return out

    before = asyncio.run(run(with_feedback=False))
    after = asyncio.run(run(with_feedback=True))
    for c, _ in FEEDBACK_CASES:
        b = next(r for r, (cc, _) in zip(before, FEEDBACK_CASES) if cc.name == c.name)
        a = next(r for r, (cc, _) in zip(after, FEEDBACK_CASES) if cc.name == c.name)
        print(f"[feedback] {c.name}: without={b.category!r} with={a.category!r} want={c.expected_category!r}")
    followed = sum(1 for r, (c, _fb) in zip(after, FEEDBACK_CASES) if r.category == c.expected_category)
    print(f"[feedback] followed {followed}/{len(FEEDBACK_CASES)}")
    assert followed == len(FEEDBACK_CASES), (
        "P1 feedback injection did not steer the model to the corrected category"
    )
