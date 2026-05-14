"""FakeLLMClient for tests."""

from __future__ import annotations

from typing import Any

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
        model: str = "gemini-3-flash-preview",
    ) -> LLMResponse:
        self.calls.append((system, messages, tools, model))
        if not self._scripted:
            raise RuntimeError("FakeLLMClient: no more scripted responses")
        return self._scripted.pop(0)
