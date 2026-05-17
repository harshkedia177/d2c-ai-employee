"""Joiner stage — decides whether to finalize or replan.

ALWAYS ON. Runs after the executor returns its first wave of results.
If data is genuinely missing (null values, sample sizes below threshold for
the scope, or all-error results) the joiner emits action='replan' with a
hint that the planner can use on a second pass.
"""

from __future__ import annotations

import json
from typing import Any

from packages.chat.orchestrator._internals import (
    current_trace_id,
    log,
    to_jsonable,
)
from packages.chat.orchestrator.budgets import Budget
from packages.chat.orchestrator.plan import JoinerDecision, Plan
from packages.config import settings
from packages.llm.client import LLMClient

_JOINER_SYSTEM = """You decide whether an analytics assistant has enough data to answer the user, or whether one more round of tool calls is needed.

You will see:
- the user question
- the plan that was executed (task ids, tools, args)
- the results from each task (value/rows/error)

Return action='finalize' if the assistant can write an honest answer from these results, EVEN IF:
- some metrics returned null/no-data for the period (the answer will say "no data")
- some rows are flagged below_min_sample (the answer will omit or flag them)
- one task errored but the rest are sufficient

Return action='replan' ONLY if the data is genuinely missing for the user's core question (e.g. they asked about pincodes and we got zero rows, or every relevant metric errored). When you replan, set `hint` to one short sentence telling the planner what to fix (e.g. "broaden the date window to last 90 days" or "fetch rto_rate as well").

Prefer finalize. Replanning costs a full extra round-trip.
"""


def _format_results_for_llm(plan: Plan, results: dict[str, dict[str, Any]]) -> str:
    """Render plan + results as compact JSON for the joiner LLM. Strips
    provenance, caps dimensional rows at 20 (full payloads waste tokens; the
    joiner only needs enough signal to decide finalize vs replan).
    """
    out: list[dict[str, Any]] = []
    for t in plan.tasks:
        res = results.get(t.task_id, {})
        if isinstance(res, dict):
            stripped = {k: v for k, v in res.items() if k != "provenance"}
            rows = stripped.get("rows")
            if isinstance(rows, list) and len(rows) > 20:
                stripped["rows"] = rows[:20]
                stripped["total_rows"] = len(rows)
                stripped["truncated"] = True
        else:
            stripped = res
        out.append(
            {
                "task_id": t.task_id,
                "tool": t.tool,
                "args": to_jsonable(t.args),
                "result": to_jsonable(stripped),
            }
        )
    return json.dumps(out, indent=2)


async def run_joiner(
    user_message: str,
    plan: Plan,
    results: dict[str, dict[str, Any]],
    llm: LLMClient,
    budget: Budget,
) -> JoinerDecision:
    budget.check()
    user = (
        f"# User question\n{user_message}\n\n"
        f"# Composition hint from planner\n{plan.composition_hint or '(none)'}\n\n"
        f"# Plan + results\n{_format_results_for_llm(plan, results)}\n"
    )
    resp = await llm.generate_structured(
        system=_JOINER_SYSTEM,
        user=user,
        schema=JoinerDecision,
        model=settings.chat_joiner_model,
    )
    budget.add(resp.usage)
    log.info(
        "joiner_done trace_id=%s action=%s hint=%r",
        current_trace_id(),
        resp.parsed.action,
        (resp.parsed.hint or "")[:120],
    )
    return resp.parsed
