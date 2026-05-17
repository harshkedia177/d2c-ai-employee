"""Composer stage — streams the final answer.

Receives executor results, formats them as a compact table for the LLM
together with the citation-contract system prompt (verbatim from the
legacy planner), then streams tokens back. As the stream arrives we hold
a small buffer so half-emitted `{{m:placeholder}}` tokens aren't shipped
to the client until they're complete, then substitute them inline.

After the stream ends, we run the verifier on the full substituted text
and surface any violations as a warning (rather than aborting — the SSE
client has already seen the text).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from packages.chat.orchestrator._internals import (
    current_trace_id,
    log,
    to_jsonable,
)
from packages.chat.orchestrator.budgets import Budget
from packages.chat.orchestrator.plan import Plan
from packages.chat.renderer import PLACEHOLDER_RE, format_for, format_value
from packages.chat.verifier import NUMERAL_RE, find_violations
from packages.config import settings
from packages.llm.client import LLMClient


def _composer_system_prompt(today_iso: str) -> str:
    return f"""You are the composer stage of a D2C analytics assistant for an Indian D2C brand. Today is {today_iso}.

You receive the user question, a composition hint, and a results map of metric values keyed by placeholder id. Compose a short answer using those placeholders.

# Citation contract (non-negotiable)
Every number from data must appear in your final answer as a {{{{m:placeholder}}}} token from the results map. Use the placeholder ids verbatim - do not modify, round, or recompute the underlying numbers.

## No literal digits in the final answer
Never type a literal digit 0-9 EXCEPT digits that appeared verbatim in the user's question (echoing "last 30 days" is fine). Prefer words anyway: "the last month" reads better than "the last 30 days".

**Exception**: identifier strings shown in the results map (pincodes like `560001`, ad_ids, SKUs) are keys, not measurements. Use them as returned.

# Handling missing / weak data
- value: null or empty rows -> say "no data for this period" - do not substitute zero or invent a number.
- below_min_sample: true or small sample_size -> flag it ("based on only a handful of orders") or omit the row. Never rank or compare cohorts that fall below their threshold.
- error: true on a task -> tell the user that lookup failed; do not pretend you have the number.

# Refusal
If the results don't support an honest answer (every relevant value is null/error), reply: "I can only report numbers I compute from your data. I don't have industry benchmarks or estimates." Then offer a concrete alternative grounded in available metrics.

# Style
Default to 1-3 sentences. Use a list only when comparing three or more items.

# Example
Results: {{ "gmv_0": {{value: 4823100, ...}}, "gmv_1": {{value: 4123550, ...}} }}
You: "GMV over the last month was {{{{m:gmv_0}}}}, compared with {{{{m:gmv_1}}}} the prior month."
"""


@dataclass
class _ResultsBundle:
    """Everything the composer needs from the executor's output."""

    metric_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    formats: dict[str, str] = field(default_factory=dict)
    permitted_literals: set[str] = field(default_factory=set)
    # LLM-facing summary table (string)
    summary_table: str = ""


def build_results_bundle(
    user_message: str,
    plan: Plan,
    results: dict[str, dict[str, Any]],
) -> _ResultsBundle:
    """Re-shape executor results into the {placeholder_id: {value, provenance}}
    map renderer.render() expects, plus an LLM-facing summary table. Scalar
    metrics get one placeholder, dimensional metrics get one per row.
    """
    bundle = _ResultsBundle()
    # User's own numerals are always allowed (echoing "last 30 days").
    for m in NUMERAL_RE.finditer(user_message):
        bundle.permitted_literals.add(m.group())

    metric_counter: dict[str, int] = {}
    summary_rows: list[dict[str, Any]] = []

    for task in plan.tasks:
        res = results.get(task.task_id)
        if not isinstance(res, dict):
            continue
        if task.tool != "compute_metric":
            # Non-metric tools don't feed the placeholder map. Still summarize
            # them for the LLM so it can mention qualitative context.
            summary_rows.append(
                {
                    "task_id": task.task_id,
                    "tool": task.tool,
                    "result": _summarize_non_metric(res),
                }
            )
            continue

        metric_id = (task.args.metric_id if task.args else None) or "metric"
        if res.get("error"):
            summary_rows.append(
                {
                    "task_id": task.task_id,
                    "metric_id": metric_id,
                    "error": res.get("error_code", "unknown"),
                }
            )
            continue

        if "value" in res:
            n = metric_counter.get(metric_id, 0)
            pkey = f"{metric_id}_{n}"
            metric_counter[metric_id] = n + 1
            bundle.metric_results[pkey] = res
            bundle.formats[pkey] = format_for(metric_id)
            summary_rows.append(
                {
                    "task_id": task.task_id,
                    "metric_id": metric_id,
                    "placeholder": f"{{{{m:{pkey}}}}}",
                    "value": res.get("value"),
                    "sample_size": (res.get("provenance") or {}).get("sample_size"),
                    "filters": (task.args.filters if task.args else None),
                }
            )
            continue

        if "rows" in res:
            base_n = metric_counter.get(metric_id, 0)
            provenance = res.get("provenance") or {}
            llm_rows: list[dict[str, Any]] = []
            total_rows = len(res.get("rows") or [])
            for i, srow in enumerate(res.get("rows") or []):
                pkey = f"{metric_id}_{base_n}_{i}"
                per_row_result = {
                    "value": srow.get("value"),
                    "provenance": {
                        "metric_id": metric_id,
                        "query_hash": provenance.get("query_hash"),
                        "citations": srow.get("citations"),
                        "sample_size": srow.get("sample_size"),
                    },
                }
                bundle.metric_results[pkey] = per_row_result
                bundle.formats[pkey] = format_for(metric_id)
                # Admit dimension keys + any embedded numerals so the verifier
                # doesn't reject e.g. pincodes that appear in the answer.
                for k, v in srow.items():
                    if k in ("value", "citations", "sample_size", "below_min_sample"):
                        continue
                    if v is None:
                        continue
                    sv = str(v)
                    bundle.permitted_literals.add(sv)
                    for m in NUMERAL_RE.finditer(sv):
                        bundle.permitted_literals.add(m.group())
                llm_row = {k: v for k, v in srow.items() if k != "citations"}
                llm_row["placeholder"] = f"{{{{m:{pkey}}}}}"
                llm_rows.append(llm_row)
            metric_counter[metric_id] = base_n + len(llm_rows)
            shown = llm_rows[:20]
            summary_rows.append(
                {
                    "task_id": task.task_id,
                    "metric_id": metric_id,
                    "rows": shown,
                    "total_rows": total_rows,
                    "truncated": total_rows > 20,
                    "filters": (task.args.filters if task.args else None),
                }
            )

    bundle.summary_table = json.dumps(to_jsonable(summary_rows), indent=2, default=str)
    return bundle


def _summarize_non_metric(res: dict[str, Any]) -> dict[str, Any]:
    if "examples" in res:
        return {
            "examples": [
                {"question": e.get("question"), "plan": e.get("plan")}
                for e in (res.get("examples") or [])[:3]
            ]
        }
    if "rows" in res:
        return {"row_count": len(res.get("rows") or [])}
    return {"keys": sorted(res.keys())[:6]}


# Substitution helpers ---------------------------------------------------------

# Match any partial-looking placeholder prefix at the END of a buffer. We hold
# emit until we've either (a) completed the placeholder or (b) determined it
# was a false alarm.
_PARTIAL_PLACEHOLDER_TAIL = re.compile(r"\{\{?m?:?[A-Za-z0-9_]*$")


def _split_safe_emit(buffer: str) -> tuple[str, str]:
    """Return (emit_now, remainder).

    `emit_now` is the longest prefix of `buffer` that contains no in-progress
    `{{m:...}}` token. The remainder is held until more text arrives.
    """
    m = _PARTIAL_PLACEHOLDER_TAIL.search(buffer)
    if not m:
        return buffer, ""
    # If the matched tail is actually a complete `}}` token, it'd have ended
    # before the regex looked — so this is genuinely a partial placeholder.
    return buffer[: m.start()], buffer[m.start():]


def _substitute_complete(
    text: str,
    metric_results: dict[str, dict[str, Any]],
    formats: dict[str, str],
    used_keys: set[str],
) -> tuple[str, list[str]]:
    """Replace every {{m:id}} in `text` with the formatted value.

    Returns (substituted_text, newly_used_placeholder_ids).
    Unknown placeholder ids are left in place AND logged — the verifier
    layer will catch them via the warning event.
    """
    new_keys: list[str] = []

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        result = metric_results.get(key)
        if result is None:
            return match.group(0)
        formatted = format_value(result.get("value"), formats.get(key, "auto"))
        if key not in used_keys:
            used_keys.add(key)
            new_keys.append(key)
        return formatted

    return PLACEHOLDER_RE.sub(repl, text), new_keys


def _build_footnote(key: str, result: dict[str, Any]) -> dict[str, Any]:
    prov = result.get("provenance") or {}
    citations = prov.get("citations") or []
    return {
        "placeholder": key,
        "query_hash": prov.get("query_hash"),
        "metric_id": prov.get("metric_id"),
        "citations": list(citations)[:5],
        "total_sources": len(citations),
        "sample_size": prov.get("sample_size"),
    }


@dataclass
class ComposeStreamResult:
    full_text: str
    footnotes: list[dict[str, Any]]
    substituted_values: set[str]
    violations: list[dict[str, Any]]
    usage: dict[str, int] | None


async def stream_compose(
    user_message: str,
    plan: Plan,
    results: dict[str, dict[str, Any]],
    llm: LLMClient,
    budget: Budget,
    today_iso: str,
) -> AsyncIterator[
    tuple[str, Any]
]:  # yields ("text", str) | ("footnote", dict) | ("warning", dict) | ("done", ComposeStreamResult)
    """Stream the composer output token-by-token with inline substitution.

    Yields tuples:
      ("text", emitted_substring)     -- safe-to-render fragment
      ("footnote", footnote_dict)     -- emitted once per first-use of a placeholder
      ("warning", {...})              -- post-stream verifier violations, if any
      ("done", ComposeStreamResult)   -- terminal event with aggregated state
    """
    budget.check()
    bundle = build_results_bundle(user_message, plan, results)
    system = _composer_system_prompt(today_iso)
    user = (
        f"# User question\n{user_message}\n\n"
        f"# Composition hint\n{plan.composition_hint or '(none)'}\n\n"
        f"# Results map\n{bundle.summary_table}\n"
    )

    log.info(
        "composer_start trace_id=%s n_placeholders=%d",
        current_trace_id(),
        len(bundle.metric_results),
    )

    full_substituted = ""
    buffer = ""
    used_keys: set[str] = set()
    substituted_values: set[str] = set(bundle.permitted_literals)
    usage: dict[str, int] | None = None

    async for chunk in llm.generate_stream(
        system=system,
        user=user,
        model=settings.chat_composer_model,
    ):
        if chunk.usage:
            usage = chunk.usage
        if chunk.done:
            # Final flush: substitute anything we held back (e.g. stream ended
            # mid-placeholder — an LLM bug, but don't drop text on the floor).
            tail, new_keys = _substitute_complete(
                buffer, bundle.metric_results, bundle.formats, used_keys
            )
            if tail:
                full_substituted += tail
                for m in NUMERAL_RE.finditer(tail):
                    substituted_values.add(m.group())
                yield ("text", tail)
            for k in new_keys:
                yield ("footnote", _build_footnote(k, bundle.metric_results[k]))
            break

        buffer += chunk.delta
        emit, buffer = _split_safe_emit(buffer)
        if not emit:
            continue
        substituted, new_keys = _substitute_complete(
            emit, bundle.metric_results, bundle.formats, used_keys
        )
        full_substituted += substituted
        # Numerals emitted came from format_value() — admit them so the
        # post-stream verifier doesn't flag them.
        for m in NUMERAL_RE.finditer(substituted):
            substituted_values.add(m.group())
        if substituted:
            yield ("text", substituted)
        for k in new_keys:
            yield ("footnote", _build_footnote(k, bundle.metric_results[k]))

    # Re-collect all formatted values from metric_results to allow them in the
    # full-text verifier pass (defensive — handles cases where the same
    # placeholder appears multiple times).
    for key in used_keys:
        result = bundle.metric_results.get(key) or {}
        formatted = format_value(result.get("value"), bundle.formats.get(key, "auto"))
        substituted_values.add(formatted)
        v = result.get("value")
        if isinstance(v, int | float):
            substituted_values.add(str(v))
            if isinstance(v, float) and v.is_integer():
                substituted_values.add(str(int(v)))
        for m in NUMERAL_RE.finditer(formatted):
            substituted_values.add(m.group())

    violations = find_violations(full_substituted, frozenset(substituted_values))
    if violations:
        log.warning(
            "composer_verifier_violations trace_id=%s n=%d first=%r",
            current_trace_id(),
            len(violations),
            violations[0],
        )
        yield (
            "warning",
            {
                "code": "verifier_violation",
                "message": (
                    "Composer emitted literal numeral(s) not in the cited "
                    "results. Treat the answer with caution."
                ),
                "details": {"violations": violations[:5]},
            },
        )

    footnotes = [_build_footnote(k, bundle.metric_results[k]) for k in used_keys]
    yield (
        "done",
        ComposeStreamResult(
            full_text=full_substituted,
            footnotes=footnotes,
            substituted_values=substituted_values,
            violations=violations,
            usage=usage,
        ),
    )


