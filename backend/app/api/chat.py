"""AI cross-mailbox chat QA endpoints.

``POST /api/chat`` answers a natural-language question about the user's mail
by retrieving the most relevant email fragments (pg_trgm similarity search)
and asking the configured LLM to answer strictly from that material.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email import UnifiedEmail
from app.services.ai_config import load_ai_config
from app.services.chat_qa_service import chat_qa, chat_qa_stream

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class CitedEmail(BaseModel):
    id: int
    subject: str
    sender: str
    date: datetime | None = None
    # Distinguishing info for the chat reference cards: sender address,
    # category chip, read state, suggested action and a body preview.
    sender_email: str = ""
    category: str | None = None
    unread: bool = False
    action: str | None = None
    preview: str = ""


class ChatResponse(BaseModel):
    answer: str
    cited_emails: list[CitedEmail]
    # Provenance flag surfaced in the UI: "ai" = LLM answer. Reserved for a
    # future degraded path so the user always knows who produced an answer.
    source: str = "ai"


async def _resolve_cited(db: AsyncSession, cited_ids: list) -> list[CitedEmail]:
    """Load cited email rows and shape them into UI reference cards."""
    ids: list[int] = []
    for raw_id in cited_ids:
        try:
            ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue  # skip non-numeric IDs

    cited_emails: list[CitedEmail] = []
    if ids:
        rows = (
            await db.execute(select(UnifiedEmail).where(UnifiedEmail.id.in_(ids)))
        ).scalars().all()
        by_id = {row.id: row for row in rows}
        for email_id in ids:
            row = by_id.get(email_id)
            if row is None:
                continue
            cited_emails.append(
                CitedEmail(
                    id=row.id,
                    subject=row.subject or "",
                    sender=row.sender or "",
                    date=row.received_at,
                    sender_email=row.sender_email or "",
                    category=row.category,
                    unread=not bool(row.is_read),
                    action=row.suggested_action,
                    preview=(row.body_snippet or "")[:140],
                )
            )
    return cited_emails


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")

    cfg = await load_ai_config(db)
    if not cfg.use_ai:
        raise HTTPException(
            status_code=400,
            detail="当前为规则模式（未配置 AI Key）。请在「设置 → AI 分析」中配置模型后使用聊天问答。",
        )

    try:
        result = await chat_qa(
            db,
            cfg,
            message,
            history=[
                {"role": m.role, "content": m.content}
                for m in payload.history[-10:]
            ],
        )
    except Exception:
        # Any LLM failure (network / quota / timeout) is surfaced uniformly.
        raise HTTPException(status_code=502, detail="AI 服务暂时不可用，请稍后重试")

    cited_emails = await _resolve_cited(db, result.cited_ids)

    return ChatResponse(
        answer=result.answer, cited_emails=cited_emails, source=result.source
    )


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE variant of POST /api/chat.

    Emits ``{"t": "..."}`` text deltas while the model is still generating,
    then a final ``{"done": true, ...}`` event carrying the full answer, the
    cited cards, and the serving tier ("local" | "cloud") so the UI can badge
    cloud deep-mode answers.
    """
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")

    cfg = await load_ai_config(db)
    if not cfg.use_ai:
        raise HTTPException(
            status_code=400,
            detail="当前为规则模式（未配置 AI Key）。请在「设置 → AI 分析」中配置模型后使用聊天问答。",
        )
    history = [
        {"role": m.role, "content": m.content} for m in payload.history[-10:]
    ]

    async def event_gen():
        try:
            async for ev in chat_qa_stream(db, cfg, message, history=history):
                if ev["type"] == "delta":
                    yield f"data: {json.dumps({'t': ev['text']}, ensure_ascii=False)}\n\n"
                else:
                    cited = await _resolve_cited(db, ev["cited_ids"])
                    done = {
                        "done": True,
                        "answer": ev["answer"],
                        "cited_emails": [c.model_dump(mode="json") for c in cited],
                        "source": "ai",
                        "tier": "local" if ev.get("local") else "cloud",
                    }
                    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        except Exception:
            # Any LLM failure (network / quota / timeout) is surfaced uniformly.
            yield f"data: {json.dumps({'error': 'AI 服务暂时不可用，请稍后重试'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
