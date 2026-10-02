"""Tests for context-window probing (pure logic + DB cache; no network)."""
from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.services.context_probe import (  # noqa: E402
    _build_probe_prompt,
    _was_truncated,
    candidate_sequence,
    compute_app_min_ctx,
    extract_context_length,
    get_cached_num_ctx,
    save_probed_ctx,
)


def test_extract_context_length_architectures():
    qwen = {"model_info": {"general.architecture": "qwen2", "qwen2.context_length": 32768}}
    assert extract_context_length(qwen) == 32768
    llama = {"model_info": {"llama.context_length": 8192, "general.architecture": "llama"}}
    assert extract_context_length(llama) == 8192
    assert extract_context_length({"model_info": {}}) is None
    assert extract_context_length({}) is None
    bad = {"model_info": {"qwen2.context_length": "not-a-number"}}
    assert extract_context_length(bad) is None


def test_candidate_sequence_filters():
    app_min = compute_app_min_ctx()
    # Floor between 12288 and 32768 → only the big two qualify.
    assert candidate_sequence(app_min, None) == [32768, 16384]
    # Model max caps the list; a max below the floor falls back to… nothing
    # left → keep the filtered list as-is (probe will fail into fallback).
    assert candidate_sequence(app_min, 16384) == [16384]
    # Absurdly large budget probes the requirement itself.
    assert candidate_sequence(40000, None) == [40000]
    # Small budget → grid above the floor (2048 < 3000 is correctly excluded).
    assert candidate_sequence(3000, None) == [32768, 16384, 12288, 8192, 4096]
    assert candidate_sequence(2000, None) == [32768, 16384, 12288, 8192, 4096, 2048]


def test_app_min_ctx_sane():
    app_min = compute_app_min_ctx()
    # Must exceed 8192 (fragments 10×420 chars + history + output reserve)
    # but stay below 16384 so the empirically working window stays reachable.
    assert 8192 < app_min < 16384


def test_truncation_detection():
    target = 10000
    # Native Ollama shape, ingested far less than sent → truncated.
    assert _was_truncated({"prompt_eval_count": 4096}, target)
    # OpenAI-compatible shape, full ingest → fine.
    assert not _was_truncated({"usage": {"prompt_tokens": 9800}}, target)
    # No usage reported → cannot tell, accept (not truncated).
    assert not _was_truncated({}, target)


def test_build_probe_prompt_matches_target_scale():
    prompt = _build_probe_prompt(10000)
    # chars ≈ tokens / 1.2 (CJK safety factor)
    assert 7000 < len(prompt) < 9500


def test_cache_roundtrip():
    async def _run():
        from app.database import Base, SessionLocal, engine
        from app.models.local_model_context_cache import LocalModelContextCache

        # The running server may predate this table; create it if missing.
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: LocalModelContextCache.__table__.create(
                    sync_conn, checkfirst=True
                )
            )

        async with SessionLocal() as db:
            await save_probed_ctx(db, "http://localhost:11434", "probe-test:1b", 16384, 32768)
            first = await get_cached_num_ctx(db, "http://localhost:11434", "probe-test:1b")
            # Re-probe overwrites.
            await save_probed_ctx(db, "http://localhost:11434", "probe-test:1b", 8192, None)
            second = await get_cached_num_ctx(db, "http://localhost:11434", "probe-test:1b")
            miss = await get_cached_num_ctx(db, "http://localhost:11434", "other:model")
            # cleanup
            from sqlalchemy import delete

            from app.models.local_model_context_cache import LocalModelContextCache

            await db.execute(
                delete(LocalModelContextCache).where(
                    LocalModelContextCache.model == "probe-test:1b"
                )
            )
            await db.commit()
            return first, second, miss

    first, second, miss = asyncio.run(_run())
    assert first == 16384
    assert second == 8192
    assert miss is None
