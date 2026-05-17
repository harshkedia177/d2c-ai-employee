"""POST /chat — orchestrate one chat turn (JSON, backward-compat).
POST /chat/stream — same orchestrator, streamed as SSE events.

Also exposes GET /chat/provenance/{query_hash} so the UI can click-through
a footnote without burning another chat turn.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from packages.chat.orchestrator import chat_turn, chat_turn_stream
from packages.chat.tools import get_provenance as _get_provenance_tool
from packages.llm.client import LLMClient
from packages.llm.gemini import GeminiClient

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    text: str
    footnotes: list[dict]
    status: str
    trace_id: str | None = None
    usage: dict[str, int] | None = None


@lru_cache(maxsize=1)
def get_llm() -> LLMClient:
    """FastAPI dependency. Overridable in tests via `app.dependency_overrides`."""
    return GeminiClient()


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    llm: Annotated[LLMClient, Depends(get_llm)],
    x_trace_id: Annotated[str | None, Header(alias="X-Trace-Id")] = None,
) -> ChatResponse:
    """JSON endpoint — drains the orchestrator stream into a single response.

    Kept for backward compatibility with callers that don't want SSE.
    """
    trace_id = x_trace_id or uuid.uuid4().hex[:12]
    out = await chat_turn(
        tenant_id=req.tenant_id,
        user_message=req.message,
        llm=llm,
        trace_id=trace_id,
    )
    return ChatResponse(
        text=out["text"],
        footnotes=out.get("footnotes") or [],
        status=out.get("status", "ok"),
        trace_id=out.get("trace_id") or trace_id,
        usage=out.get("usage"),
    )


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    llm: Annotated[LLMClient, Depends(get_llm)],
    x_trace_id: Annotated[str | None, Header(alias="X-Trace-Id")] = None,
) -> StreamingResponse:
    """SSE endpoint — yields orchestrator events as they happen.

    Event format follows the SSE spec:
        event: <event_type>
        data: <json-encoded event payload>

    Clients can dispatch on `event` (plan, tool_start, tool_result,
    join_decision, compose_start, token, footnote, warning, done, error).
    The `done` event is the canonical end-of-stream marker.
    """
    trace_id = x_trace_id or uuid.uuid4().hex[:12]

    async def event_source():
        async for evt in chat_turn_stream(
            tenant_id=req.tenant_id,
            user_message=req.message,
            llm=llm,
            trace_id=trace_id,
        ):
            yield f"event: {evt.event}\ndata: {evt.model_dump_json()}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


@router.get("/provenance/{query_hash}")
async def chat_provenance(query_hash: str, tenant_id: str) -> dict:
    """Re-execute a cached compute_metric query, scoped to tenant_id.

    NOTE: `tenant_id` here MUST come from authenticated context once auth
    lands. Passing it as a query parameter is a stop-gap that matches the
    rest of the API; treat as untrusted until then.
    """
    result = await _get_provenance_tool(tenant_id=tenant_id, query_hash=query_hash)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
