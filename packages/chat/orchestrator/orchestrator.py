"""Top-level chat-turn orchestrator.

Implements: Plan -> Parallel-Execute -> Join -> [optional Replan -> Execute]
            -> Compose (stream) -> Verify.

The public surface is `chat_turn_stream` (async generator of OrchestratorEvent)
plus a thin `chat_turn` wrapper that drains the stream into the legacy
ChatResponse-shaped dict for backward compatibility.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

from packages.chat.orchestrator._internals import (
    log,
    redact,
    reset_trace_id,
    set_trace_id,
)
from packages.chat.orchestrator.budgets import Budget, BudgetExceededError
from packages.chat.orchestrator.composer import stream_compose
from packages.chat.orchestrator.events import (
    ComposeStartEvent,
    DoneEvent,
    ErrorEvent,
    FootnoteEvent,
    JoinEvent,
    OrchestratorEvent,
    PlanEvent,
    TokenEvent,
    ToolResultEvent,
    ToolStartEvent,
    WarningEvent,
)
from packages.chat.orchestrator.executor import execute_plan, summarize_result
from packages.chat.orchestrator.joiner import run_joiner
from packages.chat.orchestrator.plan import Plan
from packages.chat.orchestrator.planner import run_planner
from packages.config import settings
from packages.llm.client import LLMClient

_REFUSAL_TEXT = (
    "I can only report numbers I compute from your data. I don't have "
    "industry benchmarks or estimates."
)
_CLARIFY_TEXT_DEFAULT = (
    "I'm not sure which metric or window to compute. Could you specify "
    "the metric (GMV, RTO rate, CAC, ...) and a date range?"
)


async def chat_turn_stream(
    tenant_id: str,
    user_message: str,
    llm: LLMClient,
    trace_id: str | None = None,
) -> AsyncIterator[OrchestratorEvent]:
    """Run one chat turn, yielding orchestrator events as the pipeline runs.

    Wrapped in an asyncio.wait_for so total wall-clock never exceeds
    settings.chat_total_timeout_s. Tokens are budgeted via Budget; exceeding
    either limit yields an ErrorEvent then a DoneEvent(status='error').
    """
    trace_id = trace_id or uuid.uuid4().hex[:12]
    token = set_trace_id(trace_id)
    try:
        async for evt in _stream_with_timeout(
            tenant_id, user_message, llm, trace_id
        ):
            yield evt
    finally:
        reset_trace_id(token)


async def _stream_with_timeout(
    tenant_id: str,
    user_message: str,
    llm: LLMClient,
    trace_id: str,
) -> AsyncIterator[OrchestratorEvent]:
    """Apply the total-timeout to the underlying pipeline.

    asyncio.wait_for on an async generator isn't ergonomic (you can't wrap
    `async for`), so we instead drain the inner generator through a queue
    and time-bound the drain task.
    """
    queue: asyncio.Queue[OrchestratorEvent | None] = asyncio.Queue(maxsize=64)

    async def producer() -> None:
        try:
            async for evt in _run_pipeline(
                tenant_id, user_message, llm, trace_id
            ):
                await queue.put(evt)
        except Exception as e:  # noqa: BLE001 — last-resort safety net
            log.exception("orchestrator_unhandled trace_id=%s", trace_id)
            await queue.put(
                ErrorEvent(
                    code="internal_error",
                    message=redact(str(e), 200),
                    trace_id=trace_id,
                )
            )
            await queue.put(
                DoneEvent(status="error", trace_id=trace_id, footnotes=[])
            )
        finally:
            await queue.put(None)  # sentinel

    task = asyncio.create_task(producer())
    deadline = settings.chat_total_timeout_s
    try:
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=deadline)
            except TimeoutError:
                log.warning("orchestrator_timeout trace_id=%s", trace_id)
                yield ErrorEvent(
                    code="timeout",
                    message="I ran out of time answering this. Try a narrower question.",
                    trace_id=trace_id,
                )
                yield DoneEvent(status="timeout", trace_id=trace_id, footnotes=[])
                task.cancel()
                return
            if evt is None:
                return
            yield evt
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def _run_pipeline(
    tenant_id: str,
    user_message: str,
    llm: LLMClient,
    trace_id: str,
) -> AsyncIterator[OrchestratorEvent]:
    log.info(
        "chat_turn_start trace_id=%s tenant=%s msg_len=%d msg=%r",
        trace_id,
        tenant_id,
        len(user_message),
        redact(user_message),
    )
    budget = Budget.from_now(
        token_budget=settings.chat_request_token_budget,
        wall_clock_s=settings.chat_total_timeout_s,
    )
    today_iso = _dt.datetime.now(_dt.UTC).date().isoformat()

    # Stage 1: plan -----------------------------------------------------------
    try:
        plan = await run_planner(user_message, llm, budget)
    except BudgetExceededError as e:
        yield ErrorEvent(
            code="budget_exceeded", message=str(e), trace_id=trace_id
        )
        yield DoneEvent(status="error", trace_id=trace_id, footnotes=[])
        return
    except Exception as e:  # noqa: BLE001
        log.exception("planner_failed trace_id=%s", trace_id)
        yield ErrorEvent(
            code="planner_failed", message=redact(str(e), 200), trace_id=trace_id
        )
        yield DoneEvent(status="error", trace_id=trace_id, footnotes=[])
        return

    yield PlanEvent(
        trace_id=trace_id,
        intent=plan.intent,
        tasks=[t.model_dump() for t in plan.tasks],
        composition_hint=plan.composition_hint,
    )

    if plan.intent in ("refuse", "clarify"):
        async for evt in _emit_short_circuit(plan, budget, trace_id):
            yield evt
        return

    # Stage 2: execute --------------------------------------------------------
    results, exec_events = await _execute_with_events(
        plan, tenant_id, budget, trace_id
    )
    for evt in exec_events:
        yield evt

    # Stage 3: join (ALWAYS ON for safety) -----------------------------------
    # Joiner runs after every execute pass — including after a replan-driven
    # second pass. It can only *request* replan up to chat_max_replans times;
    # beyond that, the verdict is treated as finalize.
    replans_remaining = settings.chat_max_replans
    while True:
        try:
            decision = await run_joiner(user_message, plan, results, llm, budget)
        except BudgetExceededError as e:
            yield ErrorEvent(
                code="budget_exceeded", message=str(e), trace_id=trace_id
            )
            yield DoneEvent(status="error", trace_id=trace_id, footnotes=[])
            return
        except Exception as e:  # noqa: BLE001
            log.exception("joiner_failed trace_id=%s", trace_id)
            # Fall through to compose anyway — we still have results.
            yield WarningEvent(
                code="joiner_failed",
                message=f"joiner errored: {redact(str(e), 120)}; proceeding to compose",
            )
            break

        yield JoinEvent(action=decision.action, hint=decision.hint)
        if decision.action == "finalize" or replans_remaining <= 0:
            break

        # Joiner asked for a replan and we still have budget for it.
        replans_remaining -= 1
        try:
            plan = await run_planner(
                user_message, llm, budget, replan_hint=decision.hint
            )
        except Exception as e:  # noqa: BLE001
            log.exception("replan_failed trace_id=%s", trace_id)
            yield WarningEvent(
                code="replan_failed",
                message=f"replan planner pass errored: {redact(str(e), 120)}; "
                "composing from previous results",
            )
            break

        yield PlanEvent(
            trace_id=trace_id,
            intent=plan.intent,
            tasks=[t.model_dump() for t in plan.tasks],
            composition_hint=plan.composition_hint,
        )
        if plan.intent in ("refuse", "clarify"):
            async for evt in _emit_short_circuit(plan, budget, trace_id):
                yield evt
            return

        results, exec_events = await _execute_with_events(
            plan, tenant_id, budget, trace_id
        )
        for evt in exec_events:
            yield evt

    # Stage 4: compose (streaming) -------------------------------------------
    yield ComposeStartEvent(trace_id=trace_id)
    full_text = ""
    footnotes: list[dict[str, Any]] = []
    compose_usage: dict[str, int] | None = None
    try:
        async for kind, payload in stream_compose(
            user_message, plan, results, llm, budget, today_iso
        ):
            if kind == "text":
                full_text += payload
                yield TokenEvent(text=payload)
            elif kind == "footnote":
                footnotes.append(payload)
                yield FootnoteEvent(footnote=payload)
            elif kind == "warning":
                yield WarningEvent(**payload)
            elif kind == "done":
                compose_usage = payload.usage
                # full_text is already what we accumulated; payload.full_text
                # is the same. footnotes from payload are the canonical list.
                footnotes = payload.footnotes
    except BudgetExceededError as e:
        yield ErrorEvent(
            code="budget_exceeded", message=str(e), trace_id=trace_id
        )
        yield DoneEvent(status="error", trace_id=trace_id, footnotes=footnotes)
        return
    except Exception as e:  # noqa: BLE001
        log.exception("composer_failed trace_id=%s", trace_id)
        yield ErrorEvent(
            code="composer_failed",
            message=redact(str(e), 200),
            trace_id=trace_id,
        )
        yield DoneEvent(status="error", trace_id=trace_id, footnotes=footnotes)
        return

    usage_out: dict[str, int] = {"total_tokens": budget.tokens_used}
    if compose_usage:
        for k, v in compose_usage.items():
            usage_out[k] = int(v or 0)
    yield DoneEvent(
        status="ok",
        text=full_text,
        footnotes=footnotes,
        usage=usage_out if usage_out["total_tokens"] else None,
        trace_id=trace_id,
    )


async def _execute_with_events(
    plan: Plan,
    tenant_id: str,
    budget: Budget,
    trace_id: str,
) -> tuple[dict[str, dict[str, Any]], list[OrchestratorEvent]]:
    """Run the executor; tool_start/tool_result events are buffered because
    callbacks fire inside asyncio.gather and can't yield directly.
    """
    events: list[OrchestratorEvent] = []

    def on_start(task: Any, resolved_args: dict[str, Any]) -> None:
        events.append(
            ToolStartEvent(task_id=task.task_id, tool=task.tool, args=resolved_args)
        )

    def on_result(task: Any, ok: bool, result: dict[str, Any]) -> None:
        events.append(
            ToolResultEvent(
                task_id=task.task_id,
                tool=task.tool,
                ok=ok,
                summary=summarize_result(task.tool, result),
            )
        )

    results = await execute_plan(
        plan,
        tenant_id=tenant_id,
        budget=budget,
        on_task_start=on_start,
        on_task_result=on_result,
    )
    log.info("executor_done trace_id=%s n_results=%d", trace_id, len(results))
    return results, events


def _short_circuit_text(plan: Plan) -> str:
    if plan.intent == "refuse":
        reason = plan.refusal_reason
        return f"{_REFUSAL_TEXT} {reason}" if reason else _REFUSAL_TEXT
    return plan.refusal_reason or _CLARIFY_TEXT_DEFAULT


async def _emit_short_circuit(
    plan: Plan, budget: Budget, trace_id: str
) -> AsyncIterator[OrchestratorEvent]:
    """Refusal / clarify short-circuit: skip executor, joiner, composer."""
    text = _short_circuit_text(plan)
    yield ComposeStartEvent(trace_id=trace_id)
    yield TokenEvent(text=text)
    yield DoneEvent(
        status="refused" if plan.intent == "refuse" else "clarify",
        text=text,
        footnotes=[],
        usage={"total_tokens": budget.tokens_used} if budget.tokens_used else None,
        trace_id=trace_id,
    )


# Backward-compatible JSON entrypoint -----------------------------------------


async def chat_turn(
    tenant_id: str,
    user_message: str,
    llm: LLMClient,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Drain the streaming pipeline into the legacy ChatResponse shape."""
    text_parts: list[str] = []
    footnotes: list[dict[str, Any]] = []
    status = "ok"
    usage: dict[str, int] | None = None
    final_trace_id = trace_id

    async for evt in chat_turn_stream(tenant_id, user_message, llm, trace_id):
        if isinstance(evt, TokenEvent):
            text_parts.append(evt.text)
        elif isinstance(evt, FootnoteEvent):
            footnotes.append(evt.footnote)
        elif isinstance(evt, DoneEvent):
            status = evt.status
            usage = evt.usage
            final_trace_id = evt.trace_id
            # DoneEvent.text/footnotes are the canonical final values; prefer
            # them over the per-token accumulation if provided.
            if evt.text:
                text_parts = [evt.text]
            if evt.footnotes:
                footnotes = evt.footnotes
        elif isinstance(evt, ErrorEvent):
            status = "error"
            if not text_parts:
                text_parts.append(evt.message)
            final_trace_id = evt.trace_id

    return {
        "text": "".join(text_parts) or "(no answer)",
        "footnotes": footnotes,
        "status": status,
        "trace_id": final_trace_id,
        "usage": usage,
    }
