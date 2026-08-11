"""AI memory — persistent user preferences distilled from free-form input.

The settings page exposes a small chat box. Whatever the user types ("I want
to watch out for ads from X", "mail from my boss is work") is sent to the LLM,
which distills it into concise, actionable memory entries stored in
``ai_memories``. Every analysis call then injects those entries into its prompt
so classification / priority honours the user's stated preferences.

Pure-rule mode: the API rejects writes (memory needs an LLM), and the settings
UI hides the chat box entirely.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_memory import AiMemory
from app.services.ai_config import AiConfig

_log = logging.getLogger(__name__)

_MEMORY_PROMPT = """你是一个邮件偏好记忆整理助手。用户会说一些关于邮件分类、广告、发件人优先级的话。
请从用户的话中提取有用信息，整理成若干条简洁、可执行、可独立生效的记忆条目。

规则：
- 每条记忆是一句完整的话，说明一个偏好（例如"来自 XXX 的邮件归类为工作"、"包含 XXX 关键词的邮件视为广告并降低优先级"、"重点关注来自 XXX 的邮件"）
- 剔除情绪化、与邮件无关、重复、含糊的表述；只保留能帮助后续邮件分类/优先级判断的有用信息
- 如果用户输入没有可提取的信息，返回空数组 []
- 只返回一个 JSON 字符串数组，不要任何额外文字、不要 markdown 代码块

用户输入：{input}"""


async def _llm_json(cfg: AiConfig, system_prompt: str, user_content: str) -> list[str]:
    """One LLM call returning a JSON array (strings)."""
    if cfg.provider == "anthropic":
        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": cfg.model,
            "max_tokens": 1000,
            "temperature": 0,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
        url = cfg.base_url.rstrip("/") + "/messages"
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
        content = "".join(block.get("text", "") for block in data.get("content", []))
        return _parse_json_array(content)

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
        "max_tokens": 1000,
    }
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    raw = data["choices"][0]["message"]["content"]
    return _parse_json_array(raw)


def _parse_json_array(raw: str) -> list[str]:
    """Parse the LLM's answer into a list of strings, tolerating a wrapper."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Strip markdown fences if the model ignored instructions.
        cleaned = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            _log.warning("Memory LLM returned non-JSON: %s", text[:200])
            return []
    if isinstance(parsed, dict):
        # Some models wrap in {"memories": [...]}.
        for key in ("memories", "items", "result"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


# --- queries ----------------------------------------------------------------

async def get_memory_texts(db: AsyncSession, limit: int = 50) -> list[str]:
    """Return the stored memory entries (most recent first) for prompt use."""
    result = await db.execute(
        select(AiMemory.content)
        .order_by(AiMemory.created_at.desc())
        .limit(limit)
    )
    return [row[0] for row in result.all()]


async def list_memories(db: AsyncSession, limit: int = 100) -> list[AiMemory]:
    result = await db.execute(
        select(AiMemory)
        .order_by(AiMemory.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def distill_and_add(db: AsyncSession, text: str, cfg: AiConfig) -> list[AiMemory]:
    """Ask the LLM to distill ``text`` into memory entries and persist them.

    Requires an actually-configured LLM (rules_only / missing key → error).
    """
    if not cfg.use_ai:
        raise RuntimeError("AI is not configured (or mode is rules_only) — memory needs an LLM")

    entries = await _llm_json(cfg, _MEMORY_PROMPT, text[:4000])
    now = datetime.now(timezone.utc)
    created: list[AiMemory] = []
    for content in entries:
        row = AiMemory(content=content, created_at=now, updated_at=now)
        db.add(row)
        created.append(row)
    if created:
        await db.commit()
        for row in created:
            await db.refresh(row)
    _log.info("Distilled %d memory entries from user input", len(created))
    return created


async def delete_memory(db: AsyncSession, memory_id: int) -> bool:
    row = await db.get(AiMemory, memory_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True
