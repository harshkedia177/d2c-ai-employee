"""Planner stage — one LLM call that emits a structured Plan.

Returns a small DAG of tasks (which the executor fans out via asyncio.gather)
plus a composition hint. For forecast / benchmark / estimate questions it
emits intent='refuse' instead of tasks.
"""

from __future__ import annotations

import datetime as _dt

from packages.chat.orchestrator._internals import current_trace_id, log, redact
from packages.chat.orchestrator.budgets import Budget
from packages.chat.orchestrator.plan import Plan, Task
from packages.config import settings
from packages.llm.client import LLMClient
from packages.semantic_layer.compiler import list_metrics


def _metric_catalogue_table() -> str:
    """Compact, LLM-friendly metric table built from metrics.yml.

    Format is plain text, one metric per line, columns separated by ' | '.
    Avoids markdown tables (the planner doesn't need to render them; the
    extra characters waste tokens).
    """
    rows: list[str] = []
    for m in list_metrics():
        time_col = m.get("time_column") or "-"
        filter_examples = m.get("filter_examples") or []
        filter_hint = ", ".join(filter_examples) if filter_examples else "-"
        rows.append(
            f"- {m['id']}: {m['description']} "
            f"(time_column={time_col}; filters={filter_hint})"
        )
    return "\n".join(rows)


def _planner_system_prompt(today: _dt.date) -> str:
    catalogue = _metric_catalogue_table()
    return f"""You are the planning stage of a D2C analytics assistant for an Indian D2C brand. Today is {today.isoformat()}.

Your job: read the user question and emit a small DAG of tool tasks that, when executed in parallel, will gather every number the composer stage needs to answer. You do NOT call tools yourself — you emit a Plan.

# Available metrics
{catalogue}

# Date-filter convention (which column to filter by)
- gmv, aov, contribution_margin_per_order -> placed_at
- rto_rate, pincode_rto_rate_90d, sku_rto_rate_90d -> shipped_at
- cac, post_rto_roas -> date
Use ISO YYYY-MM-DD strings. Today is {today.isoformat()}; "last 30 days" means {(today - _dt.timedelta(days=30)).isoformat()} to {today.isoformat()} inclusive.

# Tools (all args go inside the typed TaskArgs object)
- compute_metric -- args.metric_id (REQUIRED), optional args.dimensions, args.filters, args.grain
- search_examples -- args.question (REQUIRED), optional args.k
- search_rows -- args.entity (REQUIRED), optional args.filters, args.limit

# Filters
Filters are a LIST of {{"field": ..., "op": ..., "value": ...}} entries; op is "gte" | "lte" | "eq".
Always include date filters when the user mentions a time window.

# $task_id reference convention
A filter's `value` may be the string "$other_task_id" — the executor substitutes the upstream result's value (scalar) or dimension list (rows).
Only use refs when a downstream task genuinely depends on upstream output — otherwise leave tasks independent so they run in parallel.

# Refusal contract (non-negotiable)
If the user asks for estimates, forecasts, predictions, projections, or industry benchmarks, set intent='refuse' and put a one-sentence reason in refusal_reason. Do NOT emit tasks in that case.
If the question is ambiguous (no metric, no entity, no timeframe), set intent='clarify' with a one-sentence refusal_reason explaining what's missing.
Otherwise set intent='answer' and emit tasks.

# Composition hint
Always set composition_hint to one sentence telling the composer how to phrase the answer.

# Example
User: "GMV last 30 days vs the prior 30?"
Plan:
{{
  "intent": "answer",
  "tasks": [
    {{"task_id":"t1","tool":"compute_metric","args":{{"metric_id":"gmv","filters":[{{"field":"placed_at","op":"gte","value":"{(today - _dt.timedelta(days=30)).isoformat()}"}},{{"field":"placed_at","op":"lte","value":"{today.isoformat()}"}}]}}}},
    {{"task_id":"t2","tool":"compute_metric","args":{{"metric_id":"gmv","filters":[{{"field":"placed_at","op":"gte","value":"{(today - _dt.timedelta(days=60)).isoformat()}"}},{{"field":"placed_at","op":"lte","value":"{(today - _dt.timedelta(days=30)).isoformat()}"}}]}}}}
  ],
  "composition_hint": "State GMV for the last month and the prior month in one sentence."
}}
"""


async def run_planner(
    user_message: str,
    llm: LLMClient,
    budget: Budget,
    *,
    replan_hint: str | None = None,
) -> Plan:
    """Single planner call. Returns a parsed Plan.

    `replan_hint` is appended to the user message on a second pass after the
    joiner decided more data is needed.
    """
    budget.check()
    today = _dt.datetime.now(_dt.UTC).date()
    system = _planner_system_prompt(today)
    user = user_message
    if replan_hint:
        user = (
            f"{user_message}\n\n[Joiner feedback after first execution pass]\n"
            f"{replan_hint}\n\nRe-emit a Plan that addresses the feedback."
        )
    log.info(
        "planner_start trace_id=%s msg=%r replan=%s",
        current_trace_id(),
        redact(user_message),
        replan_hint is not None,
    )
    resp = await llm.generate_structured(
        system=system,
        user=user,
        schema=Plan,
        model=settings.chat_planner_model,
    )
    budget.add(resp.usage)
    log.info(
        "planner_done trace_id=%s intent=%s n_tasks=%d",
        current_trace_id(),
        resp.parsed.intent,
        len(resp.parsed.tasks),
    )
    return _normalize_plan(resp.parsed)


def _normalize_plan(plan: Plan) -> Plan:
    """Enforce invariants the schema can't express:
    - refuse/clarify plans never carry tasks
    - task_ids are uniquified (planner occasionally emits "t1" twice)
    """
    if plan.intent in ("refuse", "clarify"):
        plan.tasks = []
        return plan
    seen: set[str] = set()
    out: list[Task] = []
    for i, t in enumerate(plan.tasks):
        tid = t.task_id.strip() or f"t{i + 1}"
        base = tid
        n = 1
        while tid in seen:
            n += 1
            tid = f"{base}_{n}"
        seen.add(tid)
        t.task_id = tid
        out.append(t)
    plan.tasks = out
    return plan
