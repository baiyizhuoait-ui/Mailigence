"""AI memory endpoints — manage persistent user preferences for analysis.

* ``GET  /api/ai/memories``       — list stored memory entries
* ``POST /api/ai/memories``       — distill free-form input into memories (LLM)
* ``DELETE /api/ai/memories/{id}``— remove one memory entry

POST requires a configured LLM; pure-rule mode rejects it (and the settings
UI hides the chat box entirely in that mode).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ai_memory import AiMemory
from app.services import memories
from app.services.ai_config import load_ai_config

router = APIRouter(prefix="/api/ai/memories", tags=["ai-memories"])


class MemoryOut(BaseModel):
    id: int
    content: str

    model_config = {"from_attributes": True}


class MemoryInput(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


async def _requeue_and_analyze(db: AsyncSession) -> int:
    """Re-queue stored emails and kick off a background re-classification.

    Returns the number of emails re-queued. The analysis manager runs in the
    background (up to 50 mails per pass) so a large mailbox is re-classified
    progressively without blocking the request; the periodic sweep in main.py
    keeps working the queue until it is drained.
    """
    from app.services.analysis_service import manager as analysis_mgr

    count = await memories.requeue_all_emails(db)
    if count:
        analysis_mgr.start(None)
    return count


@router.get("", response_model=list[MemoryOut])
async def list_memories(db: AsyncSession = Depends(get_db)) -> list[AiMemory]:
    return await memories.list_memories(db)


@router.post("", response_model=list[MemoryOut])
async def create_memory(
    payload: MemoryInput, db: AsyncSession = Depends(get_db)
) -> list[AiMemory]:
    cfg = await load_ai_config(db)
    if not cfg.use_ai:
        raise HTTPException(
            status_code=400,
            detail="AI memory requires a configured LLM (rules_only mode is not supported)",
        )
    try:
        created = await memories.distill_and_add(db, payload.text, cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"AI memory failed: {exc}")
    if created:
        # Apply the new preference to historical mail too: re-queue every
        # stored email for a fresh classification pass.
        await _requeue_and_analyze(db)
    return created


@router.delete("/{memory_id}", response_model=dict)
async def delete_memory(memory_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    ok = await memories.delete_memory(db, memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    # Removing a preference changes classification too — re-queue history.
    await _requeue_and_analyze(db)
    return {"deleted": memory_id}
