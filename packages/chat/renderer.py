"""Render LLM drafts into final answers by substituting {{m:metric_id}} placeholders.

The renderer is the ONLY way numerical values become text in a final answer.
Combined with the regex Verifier, it guarantees the citation contract:
no numeral reaches the user without an attached footnote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{\{m:([a-zA-Z0-9_]+)\}\}")
# Mirror verifier's NUMERAL_RE so we register every numeral substring the
# verifier will see inside our formatted outputs (e.g. inside "₹4,82,310").
NUMERAL_RE_PLAIN = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")


@dataclass(frozen=True)
class RenderResult:
    text: str  # rendered text (placeholders → values)
    substituted_values: frozenset[str]  # exact strings that got injected
    footnotes: list[dict[str, Any]]  # per-placeholder citation block


class UnresolvedPlaceholder(ValueError):  # noqa: N818 — public API name
    """A {{m:...}} placeholder referred to a metric not in metric_results."""


def format_inr(v: float | int | None) -> str:
    """Format a number with Indian numbering (₹4,82,310 not ₹482,310)."""
    if v is None:
        return "₹—"
    # decide on integer vs decimal display
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        whole = int(v)
        return "₹" + _indian_group(str(whole))
    # show 2 decimals if non-integer
    s = f"{v:.2f}"
    int_part, dot, frac = s.partition(".")
    return "₹" + _indian_group(int_part) + dot + frac


def _indian_group(int_str: str) -> str:
    """Insert commas Indian-style: last 3 digits, then groups of 2.

    Example: '4823100' → '48,23,100'.
    """
    s = int_str.lstrip("-")
    sign = "-" if int_str.startswith("-") else ""
    if len(s) <= 3:
        return sign + s
    last3 = s[-3:]
    head = s[:-3]
    # group head from right in chunks of 2
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
    """Pick a sensible string format. INR for money, % for ratios <= 1."""
    if v is None:
        return "—"
    if fmt == "inr":
        return format_inr(v)
    if fmt == "pct":
        return format_pct(v)
    # auto: ratio if 0 < v < 1.5 (covers RTO rates, ROAS in some cases — but ROAS can be >1.5)
    # Conservative: only treat as pct if explicitly < 1 AND looks ratio-shaped.
    # Default to integer/decimal stringification for general numbers.
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
    """Substitute {{m:placeholder_id}} with formatted values from metric_results.

    metric_results: {placeholder_id: {value: <number>, provenance: {...}}}
    formats: {placeholder_id: 'inr' | 'pct' | 'auto'}

    Raises UnresolvedPlaceholder if any placeholder lacks a result.
    """
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
        # also add the bare numeric form so e.g. "1247" is acceptable when
        # rendered text might use a different separator
        if isinstance(value, (int, float)):
            used.add(str(value))
            if isinstance(value, float) and value.is_integer():
                used.add(str(int(value)))
        # The verifier scans with \b\d[\d,]*(?:\.\d+)?\b which will match the
        # numeral *inside* a formatted string like "₹4,82,310" → "4,82,310",
        # or "34.0%" → "34.0". Add every plain-numeral substring of the
        # formatted output so the verifier treats it as cited.
        for m in NUMERAL_RE_PLAIN.finditer(formatted):
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
