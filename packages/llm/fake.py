"""FakeLLMClient for tests.

Supports all three call shapes: generate(), generate_structured(), and
generate_stream(). Each has its own scripted queue so tests can mix shapes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, TypeVar

from pydantic import BaseModel

from packages.llm.client import LLMResponse, StreamChunk, StructuredResponse

T = TypeVar("T", bound=BaseModel)


class FakeLLMClient:
    def __init__(
        self,
        scripted: list[LLMResponse] | None = None,
        structured: list[BaseModel] | None = None,
        streams: list[list[str]] | None = None,
        stream_usages: list[dict[str, int] | None] | None = None,
    ):
        self._scripted = list(scripted or [])
        self._structured = list(structured or [])
        self._streams = list(streams or [])
        self._stream_usages = list(stream_usages or [])
        self.calls: list[tuple[str, list[dict], list[dict], str]] = []
        self.structured_calls: list[tuple[str, str, type[BaseModel], str]] = []
        self.stream_calls: list[tuple[str, str, str]] = []

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str = "gemini-3.1-flash-lite",
    ) -> LLMResponse:
        self.calls.append((system, messages, tools, model))
        if not self._scripted:
            raise RuntimeError("FakeLLMClient: no more scripted generate() responses")
        return self._scripted.pop(0)

    async def generate_structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        model: str = "gemini-3.1-flash-lite",
    ) -> StructuredResponse[T]:
        self.structured_calls.append((system, user, schema, model))
        if not self._structured:
            raise RuntimeError("FakeLLMClient: no more scripted structured responses")
        parsed = self._structured.pop(0)
        if not isinstance(parsed, schema):
            raise TypeError(
                f"FakeLLMClient scripted {type(parsed).__name__} but caller asked for {schema.__name__}"
            )
        return StructuredResponse(parsed=parsed, usage=None)

    async def generate_stream(
        self,
        system: str,
        user: str,
        model: str = "gemini-3.1-flash-lite",
    ) -> AsyncIterator[StreamChunk]:
        self.stream_calls.append((system, user, model))
        if not self._streams:
            raise RuntimeError("FakeLLMClient: no more scripted streams")
        chunks = self._streams.pop(0)
        usage = self._stream_usages.pop(0) if self._stream_usages else None
        for c in chunks:
            yield StreamChunk(delta=c)
        yield StreamChunk(delta="", usage=usage, done=True)
