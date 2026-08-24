"""AI cross-mailbox chat QA endpoints.

``POST /api/chat`` answers a natural-language question about the user's mail
by retrieving the most relevant email fragments (pg_trgm similarity search)
and asking the configured LLM to answer strictly from that material.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email import UnifiedEmail
from app.services.ai_config import load_ai_config
from app.services.chat_qa_service import chat_qa

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


class ChatResponse(BaseModel):
    answer: str
    cited_emails: list[CitedEmail]


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
            history=[{"role": m.role, "content": m.content} for m in payload.history[-3:]],
        )
    except Exception:
        # Any LLM failure (network / quota / timeout) is surfaced uniformly.
        raise HTTPException(status_code=502, detail="AI 服务暂时不可用，请稍后重试")

    ids: list[int] = []
    for raw_id in result.cited_ids:
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
                )
            )

    return ChatResponse(answer=result.answer, cited_emails=cited_emails)
