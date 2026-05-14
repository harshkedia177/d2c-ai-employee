"""The chat tool-use loop."""

from __future__ import annotations

import datetime as _dt
import logging
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from packages.chat.renderer import RenderResult, render
from packages.chat.tools import TOOL_REGISTRY, TOOL_SCHEMAS
from packages.chat.verifier import find_violations
from packages.llm.client import LLMClient, LLMResponse

log = logging.getLogger(__name__)

MAX_TURNS = 8
MAX_VERIFY_RETRIES = 2

SYSTEM_PROMPT = """You are a D2C analytics assistant for an Indian D2C brand.

CRITICAL RULES (non-negotiable):
1. NEVER type a literal numeral (any digit 0-9) in your final answer. This
   includes numbers from the user's question. Rephrase to drop them:
     - "last 30 days" → "last month" or "the last thirty days" (words, not digits)
     - "top 10 pincodes" → "top pincodes" or "the leading pincodes"
     - "post-RTO ROAS last 7 days" → "post-RTO ROAS for the past week"
   Numbers ONLY appear via placeholders of the form {{m:metric_id_N}} where
   the id matches a compute_metric result from this turn. Every successful
   compute_metric response contains a `placeholder` field (for scalar
   results) or one `placeholder` field per row (for dimensional results) —
   use those exact tokens verbatim in your final answer. Do NOT invent new
   ids or guess the index. Dimension values (pincodes, ad ids, dates) MAY
   appear as bare strings — they are keys, not measurements. Spelling out
   a number in words is fine; typing a digit anywhere else is forbidden.
2. Use compute_metric for every number. Use search_examples for novel
   questions to find precedent plans. Call get_schema first when uncertain
   which metric or filter to use — its `time_column` and `filter_examples`
   fields tell you the exact key name to pass for date filters.
3. Time filters MUST use the metric's declared `time_column` from
   get_schema (e.g. `placed_at__gte` for gmv/aov, `shipped_at__gte` for
   rto_rate, `date__gte` for cac/post_rto_roas). Operators: `__gte`,
   `__lte`, `__eq`, `__in`. Do NOT invent generic names like
   `created_at__gte` unless that column appears in the schema.
4. If the user asks you to "estimate", "approximate", "guess", or compare
   against an "industry average / benchmark / typical D2C brand", REFUSE
   directly — do not silently pivot to a different metric. Reply with:
   "I can only report numbers I compute from your data. I don't have
   industry benchmarks or estimates." Then offer a precise alternative
   ("If you specify a date range I can compute your actual GMV for that
   period."). Never make up a number, and never substitute a related
   metric for the one that can't be answered.
5. Cite by reference, not invention. The renderer attaches footnotes to
   each placeholder; the user clicks through to source rows.

When you have all the data you need, emit a single final answer. The
{{m:...}} tokens will be substituted with formatted values + footnotes.
A regex verifier will reject your answer if it contains any literal
numeral not produced by the placeholders. If your answer is rejected,
restate using only placeholders.
"""


async def chat_turn(
    tenant_id: str,
    user_message: str,
    llm: LLMClient,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message},
    ]
    metric_results: dict[str, dict[str, Any]] = {}
    metric_counter: dict[str, int] = {}
    formats: dict[str, str] = {}
    # Dimension values (pincodes, ad_ids, dates) are keys, not measurements —
    # admit them as already-cited so the verifier doesn't reject them.
    permitted_literals: set[str] = set()

    for _turn in range(MAX_TURNS):
        resp: LLMResponse = await llm.generate(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        log.info(
            "turn=%d tool_calls=%s draft=%r",
            _turn,
            [(tc.name, tc.arguments) for tc in (resp.tool_calls or [])],
            (resp.text or "")[:200],
        )

        if resp.tool_calls:
            for tc in resp.tool_calls:
                tool = TOOL_REGISTRY.get(tc.name)
                if tool is None:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": tc.name,
                            "content": {"error": f"unknown tool: {tc.name}"},
                        }
                    )
                    continue
                try:
                    result = await tool(tenant_id=tenant_id, **tc.arguments)
                except Exception as e:
                    log.warning("tool %s raised: %s", tc.name, e)
                    result = {"error": str(e)}

                placeholder_key: str | None = None
                row_placeholders: list[str] = []
                tool_payload = _serializable(result)
                if tc.name == "compute_metric" and "value" in result:
                    metric_id = tc.arguments.get("metric_id", "metric")
                    n = metric_counter.get(metric_id, 0)
                    placeholder_key = f"{metric_id}_{n}"
                    metric_counter[metric_id] = n + 1
                    metric_results[placeholder_key] = result
                    formats[placeholder_key] = _format_for(metric_id)
                elif tc.name == "compute_metric" and "rows" in result:
                    metric_id = tc.arguments.get("metric_id", "metric")
                    base_n = metric_counter.get(metric_id, 0)
                    provenance = result.get("provenance") or {}
                    rows_out: list[dict[str, Any]] = []
                    # Use already-serialized rows so date/UUID/Decimal don't leak.
                    serialized_rows = (
                        tool_payload.get("rows") if isinstance(tool_payload, dict) else []
                    ) or []
                    _num_re = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
                    for i, srow in enumerate(serialized_rows):
                        pkey = f"{metric_id}_{base_n}_{i}"
                        per_row_result = {
                            "value": srow.get("value"),
                            "provenance": {
                                "metric_id": metric_id,
                                "query_hash": provenance.get("query_hash"),
                                "citations": srow.get("citations")
                                or _serializable(provenance.get("citations")),
                                "sample_size": srow.get("sample_size")
                                or provenance.get("sample_size"),
                            },
                        }
                        metric_results[pkey] = per_row_result
                        formats[pkey] = _format_for(metric_id)
                        row_placeholders.append(pkey)
                        for k, v in srow.items():
                            if k in ("value", "citations", "sample_size"):
                                continue
                            if v is None:
                                continue
                            sv = str(v)
                            permitted_literals.add(sv)
                            for _m in _num_re.finditer(sv):
                                permitted_literals.add(_m.group())
                        rows_out.append({**srow, "placeholder": f"{{{{m:{pkey}}}}}"})
                    metric_counter[metric_id] = base_n + len(rows_out)
                    if isinstance(tool_payload, dict):
                        tool_payload["rows"] = rows_out

                if placeholder_key is not None and isinstance(tool_payload, dict):
                    tool_payload["placeholder"] = f"{{{{m:{placeholder_key}}}}}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tc.name,
                        "content": tool_payload,
                    }
                )
            continue

        draft = resp.text or ""
        rendered: RenderResult
        try:
            rendered = render(draft, metric_results, formats=formats)
        except Exception as e:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"REJECTED: render failed ({e}). "
                        "Use only metric placeholders that match results "
                        "from compute_metric calls in this turn."
                    ),
                }
            )
            continue

        allowed = rendered.substituted_values | frozenset(permitted_literals)
        violations = find_violations(rendered.text, allowed)
        if violations:
            verify_attempts = sum(
                1
                for m in messages
                if m.get("role") == "system" and "VERIFIER REJECTED" in str(m.get("content", ""))
            )
            if verify_attempts >= MAX_VERIFY_RETRIES:
                # Hard refuse — do not emit text with uncited numerals.
                return {
                    "text": (
                        "I cannot give a numerical answer without computing "
                        "it from data. Please specify a metric and date range."
                    ),
                    "footnotes": [],
                    "status": "refused_verifier_exhausted",
                    "violations": violations,
                }
            offending = ", ".join(f"'{v['numeral']}'" for v in violations[:3])
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"VERIFIER REJECTED. Literal numeral(s) {offending} "
                        "appeared in your answer. You MUST rephrase to remove "
                        "every digit. If the numeral came from the user's "
                        "question (e.g. 'last 30 days'), restate it in words "
                        "('last month', 'past thirty days') or drop the period "
                        "reference entirely. Keep the {{m:placeholder}} tokens "
                        "for metric values. Do not invent new placeholder ids — "
                        "only use ids returned by compute_metric this turn."
                    ),
                }
            )
            continue

        return {
            "text": rendered.text,
            "footnotes": rendered.footnotes,
            "status": "ok",
        }

    return {
        "text": "I couldn't complete the request within the turn budget.",
        "footnotes": [],
        "status": "exhausted_turns",
    }


def _format_for(metric_id: str) -> str:
    if "_rate" in metric_id or metric_id == "rto_rate":
        return "pct"
    if metric_id in ("gmv", "aov", "cac", "contribution_margin_per_order"):
        return "inr"
    return "auto"


def _serializable(obj: Any) -> Any:
    """Convert decimals/dates/UUIDs to plain types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serializable(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, _dt.date | _dt.datetime):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    return obj
