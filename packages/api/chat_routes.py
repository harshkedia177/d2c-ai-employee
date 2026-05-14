"""POST /chat — orchestrate one chat turn."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from packages.chat.planner import chat_turn
from packages.llm.gemini import GeminiClient

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    tenant_id: str
    message: str


class ChatResponse(BaseModel):
    text: str
    footnotes: list[dict]
    status: str


_default_llm: GeminiClient | None = None


def _llm() -> GeminiClient:
    global _default_llm
    if _default_llm is None:
        _default_llm = GeminiClient()
    return _default_llm


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    llm = _llm()
    out = await chat_turn(tenant_id=req.tenant_id, user_message=req.message, llm=llm)
    return ChatResponse(
        text=out["text"],
        footnotes=out.get("footnotes") or [],
        status=out.get("status", "ok"),
    )
