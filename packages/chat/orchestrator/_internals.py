"""Internal helpers shared across orchestrator stages: trace-id context,
log redaction, JSON-safe coercion, and the sanitized tool-error mapper.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as _dt
import logging
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel

log = logging.getLogger("packages.chat.orchestrator")

# Per-request trace id, surfaced in logs and the chat response so support
# can correlate a single conversation across workers.
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def set_trace_id(trace_id: str) -> contextvars.Token[str]:
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: contextvars.Token[str]) -> None:
    _trace_id_var.reset(token)


def current_trace_id() -> str:
    return _trace_id_var.get()


def redact(text: str, max_len: int = 200) -> str:
    """PII-aware redaction for log lines.

    Replaces digit runs (>=4 in a row), emails, and long bearer-looking
    strings with shape markers. Truncates to max_len.
    """
    if not text:
        return ""
    s = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "<email>", text)
    s = re.sub(r"\b\d{4,}\b", "<digits>", s)
    s = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "<token>", s)
    return s[:max_len]


def safe_tool_error(tool_name: str, exc: BaseException) -> dict[str, Any]:
    """Map an exception to a safe, structured tool-error payload.

    The LLM gets `error_code` + a short generic message. Full exception
    text is logged with the trace_id but never leaks to the model.
    """
    log.warning(
        "tool_error trace_id=%s tool=%s type=%s msg=%s",
        current_trace_id(),
        tool_name,
        type(exc).__name__,
        redact(str(exc), 300),
    )
    if isinstance(exc, ValueError):
        return {"error": True, "error_code": "bad_argument", "tool": tool_name}
    if isinstance(exc, KeyError):
        return {"error": True, "error_code": "unknown_key", "tool": tool_name}
    if isinstance(exc, asyncio.TimeoutError):
        return {"error": True, "error_code": "timeout", "tool": tool_name}
    return {"error": True, "error_code": "internal_error", "tool": tool_name}


def to_jsonable(obj: Any) -> Any:
    """Convert decimals/dates/UUIDs/pydantic models to plain types for JSON."""
    if isinstance(obj, BaseModel):
        return to_jsonable(obj.model_dump(exclude_none=True))
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, _dt.date | _dt.datetime):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    return obj


