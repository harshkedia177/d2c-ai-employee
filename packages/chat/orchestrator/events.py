"""SSE event schemas for the orchestrator stream.

Each event has a discriminator `event` field so the FastAPI layer can emit
`event: <type>\\ndata: <json>\\n\\n` directly without re-tagging, and the
client can dispatch on the same field. The discriminated union
`OrchestratorEvent` is what the orchestrator generator yields.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class PlanEvent(BaseModel):
    event: Literal["plan"] = "plan"
    trace_id: str
    intent: str
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    composition_hint: str = ""


class ToolStartEvent(BaseModel):
    event: Literal["tool_start"] = "tool_start"
    task_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(BaseModel):
    event: Literal["tool_result"] = "tool_result"
    task_id: str
    tool: str
    ok: bool
    summary: str = ""


class JoinEvent(BaseModel):
    event: Literal["join_decision"] = "join_decision"
    action: Literal["finalize", "replan"]
    hint: str | None = None


class ComposeStartEvent(BaseModel):
    event: Literal["compose_start"] = "compose_start"
    trace_id: str


class TokenEvent(BaseModel):
    event: Literal["token"] = "token"
    text: str


class FootnoteEvent(BaseModel):
    event: Literal["footnote"] = "footnote"
    footnote: dict[str, Any]


class WarningEvent(BaseModel):
    event: Literal["warning"] = "warning"
    code: str
    message: str
    details: dict[str, Any] | None = None


class DoneEvent(BaseModel):
    event: Literal["done"] = "done"
    status: str
    text: str | None = None
    footnotes: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int] | None = None
    trace_id: str


class ErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    code: str
    message: str
    trace_id: str


OrchestratorEvent = Annotated[
    PlanEvent
    | ToolStartEvent
    | ToolResultEvent
    | JoinEvent
    | ComposeStartEvent
    | TokenEvent
    | FootnoteEvent
    | WarningEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="event"),
]
