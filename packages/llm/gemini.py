"""GeminiClient — wrapper around the google-genai SDK.

Adds three call shapes used by the orchestrator:
- `generate()`   — legacy tool-using call (kept for backward compat).
- `generate_structured()` — JSON output bound to a Pydantic schema, no tools.
- `generate_stream()`     — token-streaming free-form text, no tools.

All production calls use `thinking_level="minimal"` because Gemini 3 cannot
fully disable thinking (per Google's migration note: minimal is the closest
equivalent to thinking_budget=0 for latency-critical paths).
"""

from __future__ import annotations

import functools
import json
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from pydantic import BaseModel

from packages.config import settings
from packages.llm.client import (
    LLMResponse,
    StreamChunk,
    StructuredResponse,
    ToolCall,
)

T = TypeVar("T", bound=BaseModel)

# Default model for all stages unless overridden. gemini-3.1-flash-lite is
# the fastest GA 3.x option and defaults to minimal thinking.
DEFAULT_MODEL = "gemini-3.1-flash-lite"

# Explicit HTTP timeout — the SDK otherwise passes timeout=None, which leaks
# hung connections (pydantic-ai #4031). Milliseconds.
_HTTP_TIMEOUT_MS = 60_000


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
            text = content if isinstance(content, str) else str(content)
            out.append(
                {
                    "role": "user" if role == "user" else "model",
                    "parts": [{"text": text}],
                }
            )
    return out


@functools.lru_cache(maxsize=32)
def _gemini_compatible_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON schema dict accepted by Gemini's mldev backend.

    Pydantic V2 emits two keywords mldev rejects:
    - `additionalProperties` (both `false` on closed objects and `{}` on
      `dict[str, Any]` fields) — stripped everywhere.
    - `$ref` / `$defs` for nested models — inlined so the resulting schema
      is self-contained.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                # "#/$defs/Foo" -> "Foo"
                key = ref.rsplit("/", 1)[-1]
                target = defs.get(key, {})
                return inline(target)
            return {k: inline(v) for k, v in node.items() if k != "additionalProperties"}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    return inline(raw)


def _extract_usage(meta: Any) -> dict[str, int] | None:
    if meta is None:
        return None
    usage: dict[str, int] = {}
    for src_key, dst_key in (
        ("prompt_token_count", "prompt_tokens"),
        ("candidates_token_count", "completion_tokens"),
        ("total_token_count", "total_tokens"),
        ("cached_content_token_count", "cached_tokens"),
    ):
        v = getattr(meta, src_key, None)
        if v is not None:
            usage[dst_key] = int(v)
    return usage or None


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.gemini_api_key
        self._client: Any = None  # google.genai.Client, lazily constructed

    def _ensure_client(self) -> Any:
        if self._client is None:
            from google import genai  # type: ignore[import-not-found]
            from google.genai.types import HttpOptions  # type: ignore[import-not-found]

            self._client = genai.Client(
                api_key=self.api_key,
                http_options=HttpOptions(timeout=_HTTP_TIMEOUT_MS),
            )
        return self._client

    @staticmethod
    def _minimal_thinking_config() -> dict[str, Any]:
        return {"thinking_config": {"thinking_level": "minimal"}}

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str = DEFAULT_MODEL,
    ) -> LLMResponse:
        client = self._ensure_client()
        gemini_tools = [{"function_declarations": _to_gemini_schema(tools)}] if tools else None
        contents = _to_gemini_messages(messages)
        config: dict[str, Any] = {"system_instruction": system, **self._minimal_thinking_config()}
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
            usage=_extract_usage(getattr(response, "usage_metadata", None)),
        )

    async def generate_structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        model: str = DEFAULT_MODEL,
    ) -> StructuredResponse[T]:
        """Single-turn JSON output bound to a Pydantic schema.

        Used by the planner and joiner. Returns a parsed Pydantic instance —
        the SDK enforces the schema, so callers don't need to re-validate.
        """
        client = self._ensure_client()
        config: dict[str, Any] = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "response_json_schema": _gemini_compatible_schema(schema),
            **self._minimal_thinking_config(),
        }
        response = await client.aio.models.generate_content(
            model=model,
            contents=[{"role": "user", "parts": [{"text": user}]}],
            config=config,
        )
        # SDK doesn't auto-parse with response_json_schema (only with
        # response_schema=PydanticClass). Parse from .text ourselves.
        raw = getattr(response, "text", "") or ""
        parsed = schema.model_validate(json.loads(raw))
        return StructuredResponse(
            parsed=parsed,
            usage=_extract_usage(getattr(response, "usage_metadata", None)),
        )

    async def generate_stream(
        self,
        system: str,
        user: str,
        model: str = DEFAULT_MODEL,
    ) -> AsyncIterator[StreamChunk]:
        """Stream tokens for free-form composer output.

        Yields `StreamChunk(delta=...)` per token batch. The final chunk
        carries `done=True` and the aggregated `usage` (Gemini surfaces
        usage_metadata only on the final stream chunk).
        """
        client = self._ensure_client()
        config: dict[str, Any] = {
            "system_instruction": system,
            **self._minimal_thinking_config(),
        }
        stream = await client.aio.models.generate_content_stream(
            model=model,
            contents=[{"role": "user", "parts": [{"text": user}]}],
            config=config,
        )
        usage: dict[str, int] | None = None
        async for chunk in stream:
            delta = getattr(chunk, "text", None) or ""
            meta = getattr(chunk, "usage_metadata", None)
            if meta is not None:
                usage = _extract_usage(meta) or usage
            if delta:
                yield StreamChunk(delta=delta)
        yield StreamChunk(delta="", usage=usage, done=True)
