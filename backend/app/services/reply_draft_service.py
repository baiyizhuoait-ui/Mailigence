"""AI reply-draft generation.

Turns an incoming email into 1-2 ready-to-edit reply drafts, following the
tone/formality of the original message and honouring the user's stored
preferences (AI memory).

The LLM client is NOT reimplemented here — this module reuses the same
openai/anthropic/ollama wrappers used by the email classifier
(``app.services.ai_analyzer``), so provider config, endpoints and error
handling stay in one place.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.ai_analyzer import _anthropic_chat, _openai_chat
from app.services.ai_config import AiConfig

_log = logging.getLogger(__name__)

# Body text fed to the LLM is capped to control latency / cost.
BODY_MAX_CHARS = 2000
# Thinking models (e.g. local qwen3) emit a long reasoning block BEFORE the
# answer and are slow; give them enough budget + time so the JSON actually
# gets produced instead of being cut off mid-thought.
MAX_TOKENS = 2048
LLM_TIMEOUT = 120

SYSTEM_PROMPT = """你是用户的邮件助理。根据原始邮件生成 1 到 2 个回复草稿。
要求：
- 语气匹配原邮件（原邮件正式则草稿正式，随意则随意）
- 直接回应原邮件中的问题、请求或待确认事项，不要泛泛而谈
- 严禁编造原邮件未提及的事实、时间、数字或承诺
- 如提供了用户偏好，体现在语气或处理方式上
- 每个草稿正文不超过150字
- 不要输出任何思考过程或解释
- 只输出 JSON 数组，不要代码块，格式：
[{"style": "简短说明风格，如'简洁确认'/'详细说明'", "body": "草稿正文"}]"""

USER_PROMPT_TEMPLATE = """原始邮件：
发件人：{sender}
主题：{subject}
正文：{body_text}

用户偏好参考（可能为空）：{user_memory_preferences}

请生成回复草稿。"""

# Appended to the user content on the one allowed parse-failure retry.
_RETRY_HINT = "\n（上次输出格式不正确，请严格只输出 JSON 数组，不要任何其他文字）"

# Progressive body budgets for the length-rejection retry path. Some local /
# small-context models can't fit a long email + the draft output in one window
# and reject the request (often as a 400, sometimes reported as "invalid model
# name"). When that happens we retry with a shorter body before giving up.
_TRUNCATION_BUDGETS = (1200, 600, 300, 150)


def _build_user_content(sender: str, subject: str, body_text: str, memories: list[str], hint: str = "") -> str:
    return USER_PROMPT_TEMPLATE.format(
        sender=sender or "(未知)",
        subject=subject or "(无主题)",
        body_text=(body_text or "(无正文)")[:BODY_MAX_CHARS],
        user_memory_preferences="\n".join(f"- {m}" for m in memories) or "（无）",
    ) + hint


def _looks_length_related(msg: str) -> bool:
    """True when the provider's rejection is likely about input length.

    Local servers report oversized prompts with messages like "context length
    exceeded" / "num_predict" — but some report it as "invalid model name"
    too, so both are treated as retryable-by-truncation.
    """
    low = msg.lower()
    if any(k in low for k in ("context", "num_predict", "max length", "too long", "exceeds", "token")):
        return True
    return "invalid model" in low


async def _call_llm_with_retry(
    cfg: AiConfig,
    sender: str,
    subject: str,
    body_text: str,
    memories: list[str],
    hint: str = "",
) -> str:
    """Call the LLM, retrying with progressively shorter bodies on rejection.

    A genuinely wrong model name fails every attempt identically (so the
    truncated retries are cheap no-ops and the original error still surfaces).
    """
    last_exc: Exception | None = None
    lengths = [len(body_text)] + [b for b in _TRUNCATION_BUDGETS if b < len(body_text)]
    for length in lengths:
        try:
            return await _call_llm(cfg, _build_user_content(sender, subject, body_text[:length], memories, hint))
        except Exception as exc:  # noqa: BLE001 — retry decision is heuristic
            last_exc = exc
            if not _looks_length_related(str(exc)):
                raise
    raise last_exc  # type: ignore[misc] — always set when the loop runs


def friendly_llm_error(exc: Exception) -> str:
    """Turn a raw LLM/provider exception into a user-facing draft message."""
    msg = str(exc)
    if "（提示：" in msg:
        # Keep the actionable hint (model-name or input-too-long), drop the
        # raw provider JSON blob.
        return "AI 调用失败。" + msg.split("（提示：")[1]
    if msg.startswith("LLM HTTP") or msg.startswith("Anthropic HTTP"):
        return "AI 服务暂时不可用（接口返回错误）。请检查接口地址与模型名称是否正确，本地模型请确认服务已启动。"
    return msg


async def generate_draft_replies(
    cfg: AiConfig,
    sender: str,
    subject: str,
    body_text: str,
    memories: list[str] | None = None,
) -> list[dict]:
    """Ask the LLM for 1-2 reply drafts and return ``[{style, body}]``.

    Parsing is tolerant (the model sometimes wraps the JSON in prose or
    markdown fences); if the output still can't be parsed, one retry is
    attempted before raising ``ValueError`` with a friendly message.
    Long bodies are truncated-and-retried when the provider rejects them.
    """
    memories = memories or []
    raw = await _call_llm_with_retry(cfg, sender, subject, body_text, memories)
    try:
        return _sanitize(extract_json_array(raw))
    except ValueError:
        _log.warning("Draft LLM returned unparseable JSON, retrying once")
    # One fallback retry with a stricter hint appended.
    raw = await _call_llm_with_retry(cfg, sender, subject, body_text, memories, hint=_RETRY_HINT)
    try:
        return _sanitize(extract_json_array(raw))
    except ValueError as exc:
        raise ValueError("AI 返回的回复草稿无法解析，请重试。") from exc


async def _call_llm(cfg: AiConfig, user_content: str) -> str:
    if cfg.provider == "anthropic":
        return await _anthropic_chat(
            cfg, user_content, SYSTEM_PROMPT, max_tokens=MAX_TOKENS, timeout=LLM_TIMEOUT
        )
    return await _openai_chat(
        cfg, user_content, SYSTEM_PROMPT, max_tokens=MAX_TOKENS, timeout=LLM_TIMEOUT
    )


def _repair_json(text: str):
    """Tolerant JSON parse for small local models.

    Tries, in order: the raw text, trailing-comma removal, and escaping of
    literal newlines/tabs that small models often leave inside string values
    (which is invalid JSON but very common in their output).
    """
    attempts: list[str] = [text]
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    if cleaned != text:
        attempts.append(cleaned)

    def _escape_in_strings(raw: str) -> str:
        out: list[str] = []
        in_str = False
        escaped = False
        for ch in raw:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                in_str = not in_str
                out.append(ch)
                continue
            if in_str and ch in "\n\r\t":
                out.append({"\\n": "\\n", "\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
                continue
            out.append(ch)
        return "".join(out)

    for attempt in attempts:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(_escape_in_strings(text))
    except json.JSONDecodeError:
        raise ValueError("LLM output could not be parsed as JSON")


def extract_json_array(raw: str) -> list:
    """Parse the LLM's answer into a list, tolerating extra prose/fences.

    Accepts a bare array or a wrapper object (``{"drafts": [...]}`` etc.),
    mirroring the fallback strategy used by the memory distillation service.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty LLM output")

    def _loads(candidate: str) -> Any:
        return _repair_json(
            candidate.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        )

    try:
        parsed = _loads(text)
    except ValueError:
        # Slice from the first '[' to the last ']' to drop surrounding prose.
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON array found in LLM output")
        try:
            parsed = _loads(text[start : end + 1])
        except ValueError:
            raise ValueError("JSON array slice could not be parsed")

    if isinstance(parsed, dict):
        # A single draft may come back as {"style": ..., "body": ...} instead
        # of an array — normalise it.
        if "style" in parsed and "body" in parsed:
            return [parsed]
        # Some models wrap the array in {"drafts": [...]} / {"replies": [...]}
        # or any other key holding a list — take the first list value.
        for key in ("drafts", "replies", "result", "items"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
        for value in parsed.values():
            if isinstance(value, list):
                return value
        raise ValueError("LLM output is a JSON object without a list field")
    if not isinstance(parsed, list):
        raise ValueError("LLM output is not a JSON array")
    return parsed


def _sanitize(items: list) -> list[dict]:
    """Keep only well-formed ``{style, body}`` entries; drop the rest."""
    drafts: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        style = str(item.get("style") or "").strip()[:50]
        body = str(item.get("body") or "").strip()
        if not body:
            continue
        drafts.append({"style": style, "body": body[:BODY_MAX_CHARS]})
    if not drafts:
        raise ValueError("LLM output contained no usable drafts")
    return drafts
