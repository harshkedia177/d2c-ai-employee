"""Executor stage — the Task Fetching Unit.

Walks the plan DAG in topological waves. Each wave's tasks dispatch
concurrently via asyncio.gather. $task_id references in task args are
resolved at dispatch time using upstream results.

Reference resolution contract:
- "$tid" where the referenced result is a scalar metric ({"value": ...})
  -> substituted with the scalar value.
- "$tid" where the referenced result is dimensional ({"rows": [...]})
  -> substituted with the list of dimension keys (the non-"value" /
  non-"citations" / non-"sample_size" fields from each row).
- "$tid" anywhere else (search_rows / search_examples / error result)
  -> substituted with the raw result dict; the downstream tool is
  responsible for accepting whatever shape it gets.

Errors from a single task never crash the wave; they become a sanitized
error payload that the joiner can reason about.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from packages.chat.orchestrator._internals import (
    current_trace_id,
    log,
    safe_tool_error,
)
from packages.chat.orchestrator.budgets import Budget
from packages.chat.orchestrator.plan import FilterEntry, Plan, Task, TaskArgs
from packages.chat.tools import TOOL_REGISTRY
from packages.config import settings

# Whitelist of tools the planner is allowed to dispatch. The full TOOL_REGISTRY
# also exposes get_provenance / run_sql / propose_write — those must NOT be
# planner-driven; they're either backchannel (provenance) or write-path.
_PLANNER_TOOLS = frozenset({"compute_metric", "search_examples", "search_rows"})

_REF_RE = re.compile(r"^\$([A-Za-z0-9_]+)$")


def _ref_target(value: Any) -> str | None:
    """Return the referenced task_id if `value` is a "$task_id" string, else None."""
    if isinstance(value, str):
        m = _REF_RE.match(value)
        if m:
            return m.group(1)
    return None


def _collect_refs(args: TaskArgs) -> set[str]:
    """Recursively collect all $task_id targets referenced inside args."""
    refs: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, FilterEntry):
            walk(node.value)
        else:
            t = _ref_target(node)
            if t is not None:
                refs.add(t)

    walk(args.model_dump())
    return refs


def _substitute_ref(target: str, results: dict[str, dict[str, Any]]) -> Any:
    """Map a referenced result into the value used at dispatch.

    Scalar metric -> .value
    Dimensional metric -> list of dimension-key values from each row
    Anything else -> the raw result dict (downstream tool decides)
    """
    res = results[target]
    if "value" in res:
        return res.get("value")
    if "rows" in res and isinstance(res["rows"], list):
        # Extract dimension keys (anything that isn't value / citations / sample_size /
        # below_min_sample). For typical dimensional metrics this yields e.g. a list
        # of pincodes or SKUs.
        skip = {"value", "citations", "sample_size", "below_min_sample"}
        dims: list[Any] = []
        for row in res["rows"]:
            if not isinstance(row, dict):
                continue
            keys = [k for k in row if k not in skip]
            if len(keys) == 1:
                dims.append(row[keys[0]])
            elif keys:
                dims.append({k: row[k] for k in keys})
        return dims
    return res


def _resolve_scalar(value: Any, results: dict[str, dict[str, Any]]) -> Any:
    """Replace a "$task_id" scalar with the resolved upstream value."""
    target = _ref_target(value)
    if target is not None:
        return _substitute_ref(target, results)
    return value


def _filters_to_kwargs(
    filters: list[FilterEntry] | None,
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Convert [FilterEntry(field, op, value), ...] -> {field__op: value}.

    This is the shape the semantic-layer compiler accepts.
    """
    if not filters:
        return {}
    out: dict[str, Any] = {}
    for f in filters:
        out[f"{f.field}__{f.op}"] = _resolve_scalar(f.value, results)
    return out


def _compile_args(
    tool: str,
    args: TaskArgs,
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compile typed TaskArgs into the kwargs the tool function expects.

    Per-tool whitelist of fields; everything else is dropped silently so a
    careless planner can't smuggle unused params into the tool call.
    """
    if tool == "compute_metric":
        out: dict[str, Any] = {}
        if args.metric_id:
            out["metric_id"] = args.metric_id
        if args.dimensions:
            out["dimensions"] = args.dimensions
        compiled_filters = _filters_to_kwargs(args.filters, results)
        if compiled_filters:
            out["filters"] = compiled_filters
        if args.grain:
            out["grain"] = args.grain
        return out
    if tool == "search_examples":
        out = {}
        if args.question:
            out["question"] = args.question
        if args.k is not None:
            out["k"] = args.k
        return out
    if tool == "search_rows":
        out = {}
        if args.entity:
            out["entity"] = args.entity
        compiled_filters = _filters_to_kwargs(args.filters, results)
        if compiled_filters:
            out["filter"] = compiled_filters  # search_rows uses singular `filter`
        if args.limit is not None:
            out["limit"] = args.limit
        return out
    return {}


def _refs_resolved(task: Task, results: dict[str, dict[str, Any]]) -> bool:
    return _collect_refs(task.args).issubset(results.keys())


def summarize_result(tool: str, result: dict[str, Any]) -> str:
    """Short human-readable summary for the SSE tool_result event."""
    if result.get("error"):
        return f"error: {result.get('error_code', 'unknown')}"
    if tool == "compute_metric":
        if "value" in result:
            v = result.get("value")
            prov = result.get("provenance") or {}
            return f"value={v}, sample_size={prov.get('sample_size')}"
        if "rows" in result:
            rows = result.get("rows") or []
            return f"rows={len(rows)}"
    if tool == "search_examples":
        n = len(result.get("examples") or [])
        return f"examples={n} ({result.get('retrieval', '?')})"
    if tool == "search_rows":
        n = len(result.get("rows") or [])
        return f"rows={n}"
    return "ok"


async def execute_plan(
    plan: Plan,
    tenant_id: str,
    budget: Budget,
    *,
    on_task_start: Callable[[Task, dict[str, Any]], None] | None = None,
    on_task_result: Callable[[Task, bool, dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Execute the plan's DAG. Returns {task_id: result_dict}.

    Tasks dispatch in topological waves; each wave runs concurrently via
    asyncio.gather. Per-task wall-clock timeout from settings.
    """
    results: dict[str, dict[str, Any]] = {}
    pending: dict[str, Task] = {t.task_id: t for t in plan.tasks}

    while pending:
        budget.check()
        ready = [t for t in pending.values() if _refs_resolved(t, results)]
        if not ready:
            unresolved = {
                tid: sorted(_collect_refs(t.args) - results.keys())
                for tid, t in pending.items()
            }
            raise ValueError(
                f"dependency cycle or missing upstream in plan: {unresolved}"
            )

        async def _run(task: Task) -> tuple[str, dict[str, Any], bool]:
            resolved_args = _compile_args(task.tool, task.args, results)
            if on_task_start is not None:
                on_task_start(task, resolved_args)
            tool_fn = TOOL_REGISTRY.get(task.tool)
            if tool_fn is None or task.tool not in _PLANNER_TOOLS:
                err = safe_tool_error(task.tool, ValueError(f"unknown tool: {task.tool}"))
                return task.task_id, err, False
            try:
                log.info(
                    "task_dispatch trace_id=%s task_id=%s tool=%s args=%s",
                    current_trace_id(),
                    task.task_id,
                    task.tool,
                    resolved_args,
                )
                result = await asyncio.wait_for(
                    tool_fn(tenant_id=tenant_id, **resolved_args),
                    timeout=settings.chat_per_task_timeout_s,
                )
                return task.task_id, result, True
            except Exception as e:  # noqa: BLE001 — sanitized via safe_tool_error
                return task.task_id, safe_tool_error(task.tool, e), False

        log.info(
            "executor_wave trace_id=%s n_ready=%d task_ids=%s",
            current_trace_id(),
            len(ready),
            [t.task_id for t in ready],
        )
        gathered = await asyncio.gather(
            *[_run(t) for t in ready], return_exceptions=False
        )
        for tid, result, ok in gathered:
            results[tid] = result
            task = pending.pop(tid)
            if on_task_result is not None:
                on_task_result(task, ok, result)

    return results
