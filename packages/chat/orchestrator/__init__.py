"""Plan -> Parallel-Execute -> Join -> Compose orchestrator.

Replaces the legacy ReAct loop (packages/chat/planner.py) with three serial
LLM stages and parallel tool execution between Plan and Compose. Drop-in
replacement for the public `chat_turn`-style entry point, but designed
around streaming SSE events.
"""

from __future__ import annotations

from packages.chat.orchestrator.events import (
    ComposeStartEvent,
    DoneEvent,
    ErrorEvent,
    FootnoteEvent,
    JoinEvent,
    OrchestratorEvent,
    PlanEvent,
    TokenEvent,
    ToolResultEvent,
    ToolStartEvent,
    WarningEvent,
)
from packages.chat.orchestrator.orchestrator import chat_turn, chat_turn_stream

__all__ = [
    "chat_turn",
    "chat_turn_stream",
    "OrchestratorEvent",
    "PlanEvent",
    "ToolStartEvent",
    "ToolResultEvent",
    "JoinEvent",
    "ComposeStartEvent",
    "TokenEvent",
    "FootnoteEvent",
    "DoneEvent",
    "ErrorEvent",
    "WarningEvent",
]
