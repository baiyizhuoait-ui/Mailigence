"""Local model context-window probing.

``AI_NUM_CTX=0`` used to mean "rely on the server default" — but Ollama's
default is often 2048-4096 (not the model's real window) unless the Modelfile
sets ``num_ctx``, and an undersized window makes Ollama *silently truncate*
long prompts. This module detects the model's true maximum via Ollama's
``/api/show`` and empirically finds the largest context window this machine
can actually serve, by sending near-real-app-size test prompts.

Key design points:

* **Pure rules, works for local and cloud profiles** — probing only ever
  targets local servers; cloud profiles never hit these code paths.
* **Truncation-aware validation** — Ollama returns HTTP 200 even when the
  prompt exceeded ``num_ctx`` (it keeps head+tail and drops the middle), so a
  bare 200 response proves nothing. We compare the response's
  ``prompt_eval_count`` (native) / ``usage.prompt_tokens`` (OpenAI-compatible)
  against the sent token estimate; a big shortfall means the candidate
  truncated and must be rejected.
* **Descending candidates, first healthy response wins** — the largest window
  this machine can serve without OOM/truncation. OOM / timeout / connection
  reset / 5xx → try the next smaller candidate; all fail → floor value (with
  a warning, never raising — startup must not depend on probing).
* Results are cached per (base_url, model) in ``local_model_context_cache``;
  the settings API's probe endpoint upserts it (manual re-probe).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.local_model_context_cache import LocalModelContextCache

_log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 45  # per-candidate; big prompts evaluate slowly
SHOW_TIMEOUT_SECONDS = 8

# Candidate windows, high → low. Filtered by the app's minimum requirement
# and by the model's detected maximum before probing.
CANDIDATE_SEQ = (32768, 16384, 12288, 8192, 4096, 2048)

# Conservative upper bound of tokens per character for CJK-heavy mixed text
# (Qwen-family tokenizers run ≈0.6-0.7 on Chinese; 1.2 still covers worst-case
# tokenizers without inflating the floor above the empirically working 16384).
CN_TOKENS_PER_CHAR = 1.2

# A response counts as truncated when fewer than this fraction of the sent
# tokens were actually ingested by the server.
_TRUNCATION_RATIO = 0.85

_PROBE_FILLER = (
    "这是一段用于上下文窗口探测的中文测试文本，混合 English words and numbers 12345，"
    "模拟真实邮件问答场景中的长度与密度。"
)


# ---- app minimum context ----------------------------------------------------


def compute_app_min_ctx() -> int:
    """Tokens the chat-QA local budget needs at worst (input + output).

    Derived from chat_qa_service's LOCAL_* constants so the floor moves
    automatically when the retrieval budget changes: retrieval fragments +
    important-mail digest + stats block + capped history + template overhead,
    converted with the CJK safety factor, plus the output reserve (num_ctx
    must hold prompt *and* generation).
    """
    from app.services.chat_qa_service import (
        HISTORY_ROUNDS,
        HISTORY_TURN_MAX_LOCAL,
        LOCAL_DIGEST_LIMIT,
        LOCAL_MAX_TOKENS,
        LOCAL_RETRIEVAL_LIMIT,
        LOCAL_SNIPPET_MAX_CHARS,
    )

    input_chars = (
        LOCAL_RETRIEVAL_LIMIT * LOCAL_SNIPPET_MAX_CHARS  # fragments
        + LOCAL_DIGEST_LIMIT * 120                       # digest lines
        + 800                                            # stats block
        + HISTORY_TURN_MAX_LOCAL * HISTORY_ROUNDS        # conversation history
        + 600                                            # system + template text
    )
    return int(input_chars * CN_TOKENS_PER_CHAR + LOCAL_MAX_TOKENS)


def candidate_sequence(app_min_ctx: int, model_max: int | None) -> list[int]:
    """Probe candidates: ≥ app_min_ctx, ≤ model_max (when known), high→low."""
    candidates = [c for c in CANDIDATE_SEQ if c >= app_min_ctx]
    if not candidates:
        # Absurdly large budget: probe the requirement itself.
        candidates = [app_min_ctx]
    if model_max:
        filtered = [c for c in candidates if c <= model_max]
        if filtered:
            candidates = filtered
    return candidates


# ---- Ollama /api/show -------------------------------------------------------


def _is_local_base_url(base_url: str) -> bool:
    host = (base_url or "").lower()
    return (
        "localhost" in host
        or "127.0.0.1" in host
        or "[::1]" in host
        or "0.0.0.0" in host
    )


def _is_ollama_base_url(base_url: str) -> bool:
    url = (base_url or "").lower()
    return "11434" in url or "ollama" in url


def _native_base(base_url: str) -> str:
    return (base_url or "").rstrip("/").removesuffix("/v1")


def extract_context_length(payload: dict) -> int | None:
    """Pull ``<arch>.context_length`` out of an /api/show ``model_info`` dict."""
    model_info = payload.get("model_info") or {}
    for key, value in model_info.items():
        if key.endswith(".context_length"):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


async def get_model_max_context(base_url: str, model: str) -> int | None:
    """Model's full supported context window, or None when undetectable.

    Only Ollama exposes this (``POST /api/show`` → model_info has
    ``qwen2.context_length``-style keys). LM Studio / vLLM / llama.cpp return
    None — probing still works for them, just without the upper bound.
    """
    base = _native_base(base_url)
    if not base or not model or not _is_ollama_base_url(base):
        return None
    try:
        async with httpx.AsyncClient(timeout=SHOW_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{base}/api/show", json={"model": model})
        if resp.status_code != 200:
            return None
        return extract_context_length(resp.json())
    except Exception as exc:  # noqa: BLE001 — probing must never raise
        _log.info("Context probe: /api/show failed for %s: %s", model, exc)
        return None


# ---- candidate probing ------------------------------------------------------


def _build_probe_prompt(target_tokens: int) -> str:
    """Filler prompt of ≈ ``target_tokens`` tokens (CJK factor applied)."""
    chars = max(int(target_tokens / CN_TOKENS_PER_CHAR), 64)
    repeats = -(-chars // len(_PROBE_FILLER))  # ceil division
    return (_PROBE_FILLER * repeats)[:chars]


def _response_prompt_tokens(payload: dict) -> int | None:
    """Tokens the server actually ingested (native vs OpenAI-compatible)."""
    native = payload.get("prompt_eval_count")
    if isinstance(native, (int, float)) and native > 0:
        return int(native)
    usage = payload.get("usage") or {}
    compat = usage.get("prompt_tokens")
    if isinstance(compat, (int, float)) and compat > 0:
        return int(compat)
    return None


def _was_truncated(payload: dict, target_tokens: int) -> bool:
    """True when the server ingested far fewer tokens than we sent.

    Ollama silently truncates over-window prompts instead of erroring, so a
    200 response alone cannot validate a candidate.
    """
    seen = _response_prompt_tokens(payload)
    if seen is None:
        return False  # server didn't report usage — accept the 200
    return seen < target_tokens * _TRUNCATION_RATIO


async def _try_candidate(
    base_url: str, model: str, candidate: int, prompt: str, target_tokens: int
) -> bool:
    """Send one test request with ``options.num_ctx=candidate``.

    Returns True when the server answered 200 within the timeout *and*
    ingested the full prompt (no silent truncation). OOM / crash / timeout /
    5xx → False (typical signs the window doesn't fit this machine).
    """
    base = _native_base(base_url)
    ollama = _is_ollama_base_url(base_url)
    if ollama:
        url = f"{base}/api/chat"
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {
                "num_ctx": candidate,
                "num_predict": 16,
                "temperature": 0,
            },
        }
    else:
        # OpenAI-compatible servers own their window; we can only verify the
        # prompt fits (no per-request num_ctx exists on this protocol).
        url = f"{base_url.rstrip('/')}/chat/completions"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "temperature": 0,
        }
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url, json=body, headers={"Content-Type": "application/json"}
            )
    except (httpx.TimeoutException, httpx.HTTPError, OSError) as exc:
        _log.info("Context probe: num_ctx=%s failed (%s)", candidate, exc)
        return False
    if resp.status_code != 200:
        _log.info(
            "Context probe: num_ctx=%s rejected (HTTP %s)", candidate, resp.status_code
        )
        return False
    try:
        payload = resp.json()
    except ValueError:
        return False
    if _was_truncated(payload, target_tokens):
        _log.info("Context probe: num_ctx=%s truncated the prompt", candidate)
        return False
    return True


async def probe_safe_num_ctx(cfg, app_min_ctx: int) -> int:
    """Largest context window this machine can serve for the app's budget.

    Tries candidates high→low; each must return 200 in time *and* ingest the
    full test prompt (≈ app_min_ctx minus the output reserve — a short "ok"
    probe would never surface long-context OOM). Falls back to the smallest
    candidate with a warning; never raises.
    """
    model_max = await get_model_max_context(cfg.base_url, cfg.model)
    candidates = candidate_sequence(app_min_ctx, model_max)
    # Input-side size: num_ctx must hold prompt + generation.
    target_tokens = max(app_min_ctx - _output_reserve(), 1024)
    prompt = _build_probe_prompt(target_tokens)
    _log.info(
        "Context probe: model=%s max=%s candidates=%s target_tokens=%s",
        cfg.model, model_max, candidates, target_tokens,
    )
    for candidate in candidates:
        if await _try_candidate(cfg.base_url, cfg.model, candidate, prompt, target_tokens):
            _log.info("Context probe: num_ctx=%s OK for %s", candidate, cfg.model)
            return candidate
    _log.warning(
        "Context probe: all candidates failed for %s — falling back to %s",
        cfg.model, candidates[-1],
    )
    return candidates[-1]


def _output_reserve() -> int:
    from app.services.chat_qa_service import LOCAL_MAX_TOKENS

    return LOCAL_MAX_TOKENS


# ---- cache ------------------------------------------------------------------


async def get_cached_num_ctx(db: AsyncSession, base_url: str, model: str) -> int | None:
    """Cached probe result for (base_url, model), or None."""
    if not base_url or not model:
        return None
    row = (
        await db.execute(
            select(LocalModelContextCache).where(
                LocalModelContextCache.base_url == base_url,
                LocalModelContextCache.model == model,
            )
        )
    ).scalar_one_or_none()
    return row.probed_num_ctx if row else None


async def save_probed_ctx(
    db: AsyncSession,
    base_url: str,
    model: str,
    probed_num_ctx: int,
    model_max_context: int | None,
) -> None:
    """Upsert the probe result (manual re-probe overwrites)."""
    stmt = pg_insert(LocalModelContextCache).values(
        base_url=base_url,
        model=model,
        probed_num_ctx=probed_num_ctx,
        model_max_context=model_max_context,
        probed_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_localmodel_ctx",
        set_={
            "probed_num_ctx": stmt.excluded.probed_num_ctx,
            "model_max_context": stmt.excluded.model_max_context,
            "probed_at": stmt.excluded.probed_at,
        },
    )
    await db.execute(stmt)
    await db.commit()
