"""Render LLM drafts by substituting {{m:metric_id}} placeholders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from packages.chat.verifier import NUMERAL_RE

PLACEHOLDER_RE = re.compile(r"\{\{m:([a-zA-Z0-9_]+)\}\}")


@dataclass(frozen=True)
class RenderResult:
    text: str
    substituted_values: frozenset[str]
    footnotes: list[dict[str, Any]]


class UnresolvedPlaceholder(ValueError):  # noqa: N818 — public API name
    pass


def format_inr(v: float | int | None) -> str:
    if v is None:
        return "₹—"
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        whole = int(v)
        return "₹" + _indian_group(str(whole))
    s = f"{v:.2f}"
    int_part, dot, frac = s.partition(".")
    return "₹" + _indian_group(int_part) + dot + frac


def _indian_group(int_str: str) -> str:
    """Insert commas Indian-style: last 3 digits, then groups of 2."""
    s = int_str.lstrip("-")
    sign = "-" if int_str.startswith("-") else ""
    if len(s) <= 3:
        return sign + s
    last3 = s[-3:]
    head = s[:-3]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups) + "," + last3


def format_pct(v: float | None) -> str:
    if v is None:
        return "—%"
    return f"{v * 100:.1f}%"


def format_value(v: Any, fmt: str = "auto") -> str:
    if v is None:
        return "—"
    if fmt == "inr":
        return format_inr(v)
    if fmt == "pct":
        return format_pct(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return f"{v:.2f}"
    return str(v)


def render(
    draft: str,
    metric_results: dict[str, dict[str, Any]],
    formats: dict[str, str] | None = None,
) -> RenderResult:
    """Substitute {{m:placeholder_id}} with formatted values from metric_results."""
    formats = formats or {}
    used: set[str] = set()
    footnotes: list[dict[str, Any]] = []

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in metric_results:
            raise UnresolvedPlaceholder(f"placeholder {{{{m:{key}}}}} has no metric result")
        result = metric_results[key]
        value = result.get("value")
        formatted = format_value(value, formats.get(key, "auto"))
        used.add(formatted)
        if isinstance(value, (int, float)):
            used.add(str(value))
            if isinstance(value, float) and value.is_integer():
                used.add(str(int(value)))
        for m in NUMERAL_RE.finditer(formatted):
            used.add(m.group())
        prov = result.get("provenance") or {}
        footnotes.append(
            {
                "placeholder": key,
                "query_hash": prov.get("query_hash"),
                "metric_id": prov.get("metric_id"),
                "citations": (prov.get("citations") or [])[:5],
                "total_sources": len(prov.get("citations") or []),
                "sample_size": prov.get("sample_size"),
            }
        )
        return formatted

    rendered = PLACEHOLDER_RE.sub(repl, draft)
    return RenderResult(
        text=rendered,
        substituted_values=frozenset(used),
        footnotes=footnotes,
    )
