"""The chat tool-use loop.

Single entry point: chat_turn(tenant_id, user_message, llm).

Wiring:
  user message
    -> LLM (with tool schemas + system prompt)
    -> if tool_calls: dispatch via TOOL_REGISTRY, feed results back
    -> if final text: render placeholders, verify no uncited numerals
    -> on verify failure: prompt LLM to restate using only tool-derived metrics

The system prompt is the contract written for the model. It says:
  - Never type a literal numeral.
  - Use {{m:metric_id_N}} placeholders for numbers.
  - Refuse to estimate or approximate without a tool result.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from packages.chat.renderer import RenderResult, render
from packages.chat.tools import TOOL_REGISTRY, TOOL_SCHEMAS
from packages.chat.verifier import find_violations

if TYPE_CHECKING:
    from packages.llm.client import LLMClient, LLMResponse

log = logging.getLogger(__name__)

MAX_TURNS = 8
MAX_VERIFY_RETRIES = 2

SYSTEM_PROMPT = """You are a D2C analytics assistant for an Indian D2C brand.

CRITICAL RULES (non-negotiable):
1. NEVER type a literal numeral in your final answer. Numbers MUST be
   referenced via placeholders of the form {{m:metric_id_N}} where the id
   matches a result you have received from compute_metric in this turn.
2. Use compute_metric for every number. Use search_examples for novel
   questions to find precedent plans.
3. If the user asks you to "estimate", "approximate", or "guess" a number
   without sufficient data, REFUSE and ask them to specify a date range or
   filter so you can compute it precisely.
4. Cite by reference, not invention. The renderer attaches footnotes to
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
    """Run one chat turn end-to-end. Returns {text, footnotes, status}."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message},
    ]
    metric_results: dict[str, dict[str, Any]] = {}
    metric_counter: dict[str, int] = {}
    formats: dict[str, str] = {}

    for _turn in range(MAX_TURNS):
        resp: LLMResponse = await llm.generate(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )

        # If the model called tools, run them and loop.
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
                except Exception as e:  # tool error -- feed back, let LLM recover
                    log.warning("tool %s raised: %s", tc.name, e)
                    result = {"error": str(e)}

                # If this was a compute_metric, register the result for placeholder use.
                if tc.name == "compute_metric" and "value" in result:
                    metric_id = tc.arguments.get("metric_id", "metric")
                    n = metric_counter.get(metric_id, 0)
                    placeholder_key = f"{metric_id}_{n}"
                    metric_counter[metric_id] = n + 1
                    metric_results[placeholder_key] = result
                    # Heuristic format: pct for *_rate metrics, inr for spend/revenue
                    if metric_id.endswith("_rate") or metric_id == "rto_rate":
                        formats[placeholder_key] = "pct"
                    elif metric_id in (
                        "gmv",
                        "aov",
                        "cac",
                        "contribution_margin_per_order",
                    ):
                        formats[placeholder_key] = "inr"
                    else:
                        formats[placeholder_key] = "auto"

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tc.name,
                        "content": _serializable(result),
                    }
                )
            continue  # loop back to LLM

        # No tool calls -- model emitted a final draft.
        draft = resp.text or ""
        rendered: RenderResult
        try:
            rendered = render(draft, metric_results, formats=formats)
        except Exception as e:  # unresolved placeholder
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

        violations = find_violations(rendered.text, rendered.substituted_values)
        if violations:
            # Up to MAX_VERIFY_RETRIES, ask the model to restate.
            verify_attempts = sum(
                1
                for m in messages
                if m.get("role") == "system" and "VERIFIER REJECTED" in str(m.get("content", ""))
            )
            if verify_attempts >= MAX_VERIFY_RETRIES:
                # Hard refuse -- do NOT emit text with uncited numerals.
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
                        "appeared in your answer. You MUST restate using only "
                        "{{m:placeholder}} tokens that match compute_metric "
                        "results from this turn. Do not type any digit yourself."
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


def _serializable(obj: Any) -> Any:
    """Convert decimals/dates to plain types for JSON serialization."""
    import datetime as _dt
    from decimal import Decimal

    if isinstance(obj, dict):
        return {k: _serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serializable(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, _dt.date | _dt.datetime):
        return obj.isoformat()
    return obj
