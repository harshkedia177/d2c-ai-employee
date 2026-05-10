"""FakeLLMClient — used by tests to script tool-use loops without any API.

Usage:
    fake = FakeLLMClient([
        LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "gmv"})]),
        LLMResponse(text="GMV last week was {{m:gmv_0}}."),
    ])
    out = await fake.generate(...)  # returns first response, then second, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from packages.llm.client import LLMResponse


class FakeLLMClient:
    def __init__(self, scripted: list[LLMResponse]):
        self._scripted = list(scripted)
        self.calls: list[tuple[str, list[dict], list[dict], str]] = []

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str = "gemini-3-pro",
    ) -> LLMResponse:
        self.calls.append((system, messages, tools, model))
        if not self._scripted:
            raise RuntimeError("FakeLLMClient: no more scripted responses")
        return self._scripted.pop(0)
