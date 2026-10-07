"""Model capability probing for cloud/API profiles.

A connected model can be *reachable* yet still too weak for the app's real
work (JSON classification, Chinese date extraction, schedule conflict
reasoning, grounded Q&A). This module runs a small suite of cheap test
prompts against the configured model and verifies each answer
programmatically — no LLM-as-judge, every check is deterministic:

* **json**          strict JSON-only structured output (classifier/extractors)
* **extract**       Chinese relative-date resolution (schedule analyzer)
* **classify**      3-way email triage accuracy (spam/meeting/customer)
* **reasoning**     overlap detection with a fixed answer (meeting conflicts)
* **hallucination** must refuse to invent info absent from the email (chat QA)
* **instruction**   obeys a hard brevity constraint (prompt templates)

Each item records its own latency so slow models are visible in the report.
Follows context_probe.py conventions: never raise upward, temperature 0,
small max_tokens, per-call timeout.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

_log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 90  # thinking models can be slow even on small prompts
MAX_TOKENS = 800            # room for reasoning tokens before the visible answer


# ---- result types ------------------------------------------------------------


@dataclass
class ItemResult:
    key: str
    title: str
    passed: bool
    detail: str
    latency_ms: int = 0


@dataclass
class ProbeReport:
    model: str
    items: list[ItemResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for i in self.items if i.passed)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def level(self) -> str:
        ratio = self.passed / self.total if self.total else 0
        if ratio == 1:
            return "good"
        if ratio >= 4 / 6:
            return "ok"
        return "poor"

    @property
    def message(self) -> str:
        failed = {i.key for i in self.items if not i.passed}
        if not failed:
            return "全部通过，可放心用于邮件分析与问答。"
        notes: list[str] = []
        if failed & {"json", "extract", "classify"}:
            notes.append("结构化输出/抽取不稳，邮件分类与日程解析可能出错")
        if "reasoning" in failed:
            notes.append("时间推理较弱，日程冲突判断不可信")
        if "hallucination" in failed:
            notes.append("会编造邮件中不存在的信息，问答场景需谨慎")
        if "instruction" in failed:
            notes.append("不遵守输出格式约束")
        return "；".join(notes) + "。"


# ---- answer helpers ----------------------------------------------------------


def _strip_fences(text: str) -> str:
    """Remove ```json fences and stray prose around the JSON payload."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text.strip()


def _extract_json(text: str):
    """First JSON value found in the answer, or None."""
    s = _strip_fences(text)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        if start == -1:
            continue
        end = s.rfind(closer)
        if end > start:
            try:
                return json.loads(s[start : end + 1])
            except ValueError:
                continue
    return None


_REFUSAL_HINTS = (
    "未提及", "没有提到", "没有说明", "未提供", "未说明", "未提到",
    "无法确定", "不知道", "找不到", "没有给出", "并未", "没有相关",
    "无此信息", "没有提及", "未包含", "没有出现", "不包含", "无从得知",
)


def _looks_refusal(text: str) -> bool:
    return any(h in text for h in _REFUSAL_HINTS)


# ---- test suite --------------------------------------------------------------


async def _run_json(call) -> tuple[bool, str]:
    prompt = (
        "只输出一个 JSON 对象，不要输出任何其他文字。\n"
        "分析这封邮件：\n"
        "主题：紧急！服务器宕机\n"
        "正文：生产数据库 14:00 起无法连接，客户下单全部失败，请立即处理。\n"
        '格式：{"urgency": "high|medium|low", "reply_needed": true或false, "summary": "一句话"}'
    )
    ans, _ = await call(prompt)
    obj = _extract_json(ans)
    if not isinstance(obj, dict):
        return False, f"无法解析出 JSON 对象：{ans[:80]!r}"
    if obj.get("urgency") not in ("high", "medium", "low"):
        return False, f"urgency 非法：{obj.get('urgency')!r}"
    if obj.get("reply_needed") is not True:
        return False, f"reply_needed 应为 true，得到：{obj.get('reply_needed')!r}"
    if not str(obj.get("summary", "")).strip():
        return False, "缺少 summary"
    return True, "JSON 合法且字段正确"


async def _run_extract(call) -> tuple[bool, str]:
    prompt = (
        "假设今天是 2026 年 3 月 9 日（星期一）。\n"
        "邮件正文：这周三下午 3 点在会议室 B 和产品团队对齐 Q3 路线图，请准时参加。\n"
        '只输出 JSON：{"meeting_date": "YYYY-MM-DD", "meeting_time": "HH:MM"}'
    )
    ans, _ = await call(prompt)
    obj = _extract_json(ans)
    if not isinstance(obj, dict):
        return False, f"无法解析出 JSON 对象：{ans[:80]!r}"
    date = str(obj.get("meeting_date", ""))
    time_s = str(obj.get("meeting_time", ""))
    ok_date = date == "2026-03-11"
    ok_time = time_s.startswith("15:")
    if ok_date and ok_time:
        return True, f"正确解析为 {date} {time_s}"
    return False, f"期望 2026-03-11 15:xx，得到 {date or '?'} {time_s or '?'}"


async def _run_classify(call) -> tuple[bool, str]:
    prompt = (
        "把三封邮件分类，type 只能是 meeting / spam / customer。\n"
        '只输出 JSON 数组：[{"id": 1, "type": "..."}, {"id": 2, "type": "..."}, {"id": 3, "type": "..."}]\n'
        "1. 尊敬的用户，恭喜您中奖！点击链接立即领取 8888 元奖励，名额有限。\n"
        "2. 王经理：明天上午十点开季度复盘会，请提前准备数据。\n"
        "3. 你好，我上周购买的设备无法开机，请问可以退货吗？"
    )
    ans, _ = await call(prompt)
    obj = _extract_json(ans)
    expected = {1: "spam", 2: "meeting", 3: "customer"}
    if not isinstance(obj, list):
        return False, f"无法解析出 JSON 数组：{ans[:80]!r}"
    got: dict[int, str] = {}
    for item in obj:
        if isinstance(item, dict) and "id" in item and "type" in item:
            try:
                got[int(item["id"])] = str(item["type"]).strip().lower()
            except (TypeError, ValueError):
                continue
    wrong = [f"#{i}:{got.get(i, '?')}≠{exp}" for i, exp in expected.items() if got.get(i) != exp]
    if wrong:
        return False, "分类错误：" + "，".join(wrong)
    return True, "3 封全部分类正确"


async def _run_reasoning(call) -> tuple[bool, str]:
    prompt = (
        "小明周五已有两个会：10:00-11:30 和 13:00-14:00。\n"
        "现在想插入一个 11:00-11:45 的会，会和已有的会冲突吗？\n"
        '只输出 JSON：{"conflict": true或false, "reason": "不超过30字"}'
    )
    ans, _ = await call(prompt)
    obj = _extract_json(ans)
    if not isinstance(obj, dict):
        return False, f"无法解析出 JSON 对象：{ans[:80]!r}"
    if obj.get("conflict") is True:
        return True, "正确识别时间段重叠"
    return False, f"应判断 conflict=true（11:00-11:45 与 10:00-11:30 重叠），得到：{obj.get('conflict')!r}"


async def _run_hallucination(call) -> tuple[bool, str]:
    prompt = (
        "邮件正文：您的订单 20260311 已于今日上午发货，预计三天内送达，物流单号 SF123456。\n"
        "请仅根据以上邮件回答问题：这封邮件提到的退款期限是多少天？"
    )
    ans, _ = await call(prompt)
    if _looks_refusal(ans) and not re.search(r"\d+\s*天", ans):
        return True, "正确拒绝回答邮件中不存在的信息"
    return False, f"疑似编造（邮件中没有退款期限）：{ans[:80]!r}"


async def _run_instruction(call) -> tuple[bool, str]:
    prompt = "请用不超过 15 个字回答：IMAP 是什么？（不要解释、不要列点）"
    ans, _ = await call(prompt)
    clean = re.sub(r"\s", "", ans)
    if len(clean) > 20:
        return False, f"超出 15 字限制（{len(clean)} 字）：{ans[:60]!r}"
    if not any(k in clean for k in ("邮", "消息", "访问", "协议", "mail", "Mail")):
        return False, f"回答与 IMAP 含义无关：{ans[:60]!r}"
    return True, f"遵守约束且正确（{len(clean)} 字）"


TESTS = [
    ("json", "结构化 JSON 输出", _run_json),
    ("extract", "中文日期抽取", _run_extract),
    ("classify", "邮件三分类", _run_classify),
    ("reasoning", "时段冲突推理", _run_reasoning),
    ("hallucination", "拒答防幻觉", _run_hallucination),
    ("instruction", "指令遵循", _run_instruction),
]


# ---- model calling -----------------------------------------------------------


def _openai_body(model: str, prompt: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }


def _openai_text(payload: dict) -> str:
    try:
        msg = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    content = msg.get("content")
    if isinstance(content, list):  # some gateways return content blocks
        content = "".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    text = (content or "").strip()
    if not text:
        # Thinking models may leave content empty when the budget is consumed
        # by reasoning; fall back to the reasoning channel so the check can
        # still grade the answer.
        text = (msg.get("reasoning_content") or "").strip()
    return text


async def _anthropic_text(base_url: str, model: str, api_key: str, prompt: str) -> str:
    url = f"{base_url.rstrip('/')}/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, json=body, headers=headers)
    resp.raise_for_status()
    parts = resp.json().get("content") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


async def probe_capabilities(
    provider_type: str, base_url: str, model: str, api_key: str
) -> ProbeReport:
    """Run the suite sequentially; individual failures never abort the run."""
    report = ProbeReport(model=model)
    is_anthropic = provider_type == "anthropic"
    url = f"{(base_url or '').rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for key, title, runner in TESTS:
        item = ItemResult(key=key, title=title, passed=False, detail="未执行")

        async def call(prompt: str) -> tuple[str, int]:
            started = time.perf_counter()
            if is_anthropic:
                text = await _anthropic_text(base_url, model, api_key, prompt)
            else:
                async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                    resp = await client.post(
                        url, json=_openai_body(model, prompt), headers=headers
                    )
                resp.raise_for_status()
                text = _openai_text(resp.json())
            return text, int((time.perf_counter() - started) * 1000)

        try:
            # Bind latency from the first call inside the runner.
            captured: list[int] = []

            async def timed_call(prompt: str, _cap: list[int] = captured) -> tuple[str, int]:
                text, ms = await call(prompt)
                _cap.append(ms)
                return text, ms

            ok, detail = await runner(timed_call)
            item.passed = ok
            item.detail = detail
            item.latency_ms = captured[0] if captured else 0
        except httpx.TimeoutException:
            item.detail = f"请求超时（>{PROBE_TIMEOUT_SECONDS}s）"
        except httpx.HTTPError as exc:
            code = getattr(exc.response, "status_code", None) if hasattr(exc, "response") else None
            item.detail = f"HTTP 错误{f' {code}' if code else ''}：{exc.__class__.__name__}"
        except Exception as exc:  # noqa: BLE001 — one bad test must not kill the run
            item.detail = f"异常：{exc.__class__.__name__}: {exc}"
        _log.info("Capability probe %s[%s]: passed=%s %s", model, key, item.passed, item.detail)
        report.items.append(item)
    return report
