"""GeminiClient — wrapper around the google-genai SDK."""

from __future__ import annotations

from typing import Any

from packages.config import settings
from packages.llm.client import LLMResponse, ToolCall


def _to_gemini_schema(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def _to_gemini_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        if role == "tool":
            out.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": m.get("tool_name", "unknown"),
                                "response": m.get("content", {}),
                            }
                        }
                    ],
                }
            )
        else:
            content = m.get("content", "")
            if isinstance(content, str):
                out.append(
                    {
                        "role": "user" if role == "user" else "model",
                        "parts": [{"text": content}],
                    }
                )
            else:
                out.append(
                    {
                        "role": "user" if role == "user" else "model",
                        "parts": [{"text": str(content)}],
                    }
                )
    return out


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.gemini_api_key
        # Lazy-import so tests that don't call the API don't need google-genai installed.
        self._client = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from google import genai  # type: ignore[import-not-found]

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str = "gemini-3-flash-preview",
    ) -> LLMResponse:
        client = self._ensure_client()
        gemini_tools = [{"function_declarations": _to_gemini_schema(tools)}] if tools else None
        contents = _to_gemini_messages(messages)
        config: dict[str, Any] = {"system_instruction": system}
        if gemini_tools:
            config["tools"] = gemini_tools

        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        try:
            parts = response.candidates[0].content.parts or []
        except (AttributeError, IndexError):
            parts = []

        for p in parts:
            fc = getattr(p, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                args = dict(getattr(fc, "args", {}) or {})
                tool_calls.append(ToolCall(name=fc.name, arguments=args))
            elif getattr(p, "text", None):
                text_parts.append(p.text)

        return LLMResponse(
            text="".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
        )
