"""LLMClient Protocol + shared types.

This module is provider-agnostic. Concrete impls live in `gemini.py` and
`fake.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """A single turn of model output.

    A response either has tool_calls (zero or more) OR text. If both are
    present, tool_calls take precedence (caller should run the tools and
    feed results back).
    """

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@runtime_checkable
class LLMClient(Protocol):
    """Provider-agnostic client.

    `tools` is a list of OpenAI-style JSON-schema function definitions:
        {"name": str, "description": str, "parameters": {<json-schema>}}
    Concrete impls translate to provider-specific shapes.
    """

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str = "gemini-3-pro",
    ) -> LLMResponse: ...
