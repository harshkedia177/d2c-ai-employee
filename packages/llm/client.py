"""LLMClient Protocol + shared types."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] | None = None


@dataclass(frozen=True)
class StructuredResponse[T]:
    """Parsed Pydantic instance + token usage from a structured-output call."""

    parsed: T
    usage: dict[str, int] | None = None


@dataclass(frozen=True)
class StreamChunk:
    """One increment of a streaming response.

    `delta` is the new text since the previous chunk (may be empty for the
    final usage-only chunk). `usage` is non-None only on the final chunk —
    Gemini surfaces usage_metadata exclusively at end-of-stream.
    """

    delta: str = ""
    usage: dict[str, int] | None = None
    done: bool = False


@runtime_checkable
class LLMClient(Protocol):
    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str = "gemini-3.1-flash-lite",
    ) -> LLMResponse: ...

    async def generate_structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        model: str = "gemini-3.1-flash-lite",
    ) -> StructuredResponse[T]: ...

    def generate_stream(
        self,
        system: str,
        user: str,
        model: str = "gemini-3.1-flash-lite",
    ) -> AsyncIterator[StreamChunk]: ...
