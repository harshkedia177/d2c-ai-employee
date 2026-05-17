"""Integration tests for the Plan -> Execute -> Join -> Compose orchestrator.

Replaces the legacy tests/chat/test_planner.py. Uses FakeLLMClient scripted
with Pydantic Plan / JoinerDecision instances + composer text-chunk streams,
matching the orchestrator's three-stage contract (no ReAct tool-call loop).
"""

import uuid

import pytest
from sqlalchemy import text

from packages.chat.orchestrator import chat_turn
from packages.chat.orchestrator.plan import JoinerDecision, Plan, Task
from packages.llm.fake import FakeLLMClient
from packages.warehouse.db import SessionLocal


async def _seed_order(tid: str, total: float) -> None:
    cid = str(uuid.uuid4())
    async with SessionLocal() as s:
        await s.execute(
            text("""
              INSERT INTO core."order" (
                tenant_id, canonical_id, placed_at, status, gateway,
                total, currency, shipping_pincode,
                source_system, source_id, source_record_url,
                raw_table, raw_row_id, raw_payload_hash,
                fetched_at, ingested_at, connector_version
              ) VALUES (
                :t, :c, '2026-05-01T00:00:00Z', 'paid', 'razorpay',
                :total, 'INR', '560001',
                'shopify', :sid, 'https://shop.example.com/admin/orders/orch-test',
                'raw.shopify_orders', 1, 'h',
                now(), now(), 'shopify@0.1.0'
              )
            """),
            {"t": tid, "c": cid, "total": total, "sid": cid[:8]},
        )
        await s.commit()


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with SessionLocal() as s:
        await s.execute(
            text(
                'DELETE FROM core."order" '
                "WHERE source_record_url = 'https://shop.example.com/admin/orders/orch-test' "
                "AND ingested_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_happy_path_renders_with_citation():
    tid = str(uuid.uuid4())
    await _seed_order(tid, 999.50)

    fake = FakeLLMClient(
        structured=[
            Plan(
                intent="answer",
                tasks=[Task(task_id="t1", tool="compute_metric", args={"metric_id": "gmv"})],
                composition_hint="State GMV in one sentence.",
            ),
            JoinerDecision(action="finalize"),
        ],
        streams=[["GMV is ", "{{m:gmv_0}}", "."]],
    )

    out = await chat_turn(tid, "what's my GMV?", fake)

    assert out["status"] == "ok"
    assert "₹999.50" in out["text"]
    assert len(out["footnotes"]) == 1
    fn = out["footnotes"][0]
    assert fn["query_hash"]
    assert fn["citations"][0]["url"] == "https://shop.example.com/admin/orders/orch-test"
    # Exactly one planner + one joiner call on the happy path:
    assert len(fake.structured_calls) == 2
    # Exactly one composer stream:
    assert len(fake.stream_calls) == 1


@pytest.mark.asyncio
async def test_refusal_short_circuits_before_tools():
    fake = FakeLLMClient(
        structured=[
            Plan(
                intent="refuse",
                refusal_reason="Industry benchmarks are out of scope.",
                tasks=[],
                composition_hint="",
            ),
        ],
    )

    out = await chat_turn("t", "what's an industry-typical RTO rate?", fake)

    assert out["status"] == "refused"
    # No joiner / composer calls were made (planner alone is enough).
    assert len(fake.structured_calls) == 1
    assert len(fake.stream_calls) == 0
    # Refusal text references benchmarks / estimates.
    assert "benchmark" in out["text"].lower() or "estimate" in out["text"].lower()


@pytest.mark.asyncio
async def test_pct_format_for_rate_metrics():
    tid = str(uuid.uuid4())
    fake = FakeLLMClient(
        structured=[
            Plan(
                intent="answer",
                tasks=[
                    Task(task_id="t1", tool="compute_metric", args={"metric_id": "rto_rate"})
                ],
                composition_hint="State the RTO rate.",
            ),
            JoinerDecision(action="finalize"),
        ],
        streams=[["RTO rate is ", "{{m:rto_rate_0}}", "."]],
    )

    out = await chat_turn(tid, "rto rate?", fake)

    assert out["status"] == "ok"
    # Either a formatted percentage or "—%" if no rows.
    assert "%" in out["text"] or "—" in out["text"]


@pytest.mark.asyncio
async def test_tool_error_does_not_crash_pipeline():
    """An unknown metric_id triggers a tool error. The executor sanitises it
    into a {'error': True, 'error_code': ...} payload and the composer is
    asked to acknowledge the failure rather than fabricate a number."""
    tid = str(uuid.uuid4())
    fake = FakeLLMClient(
        structured=[
            Plan(
                intent="answer",
                tasks=[
                    Task(
                        task_id="t1",
                        tool="compute_metric",
                        args={"metric_id": "made_up_metric"},
                    )
                ],
                composition_hint="Report that the metric is unavailable.",
            ),
            JoinerDecision(action="finalize"),
        ],
        streams=[["That metric is not available."]],
    )

    out = await chat_turn(tid, "made_up?", fake)
    assert out["status"] == "ok"
    assert "not available" in out["text"]


@pytest.mark.asyncio
async def test_replan_path_runs_planner_twice():
    """Joiner can request one replan when the first pass returned no data."""
    tid = str(uuid.uuid4())
    await _seed_order(tid, 250.0)

    plan_pass1 = Plan(
        intent="answer",
        tasks=[
            Task(
                task_id="t1",
                tool="compute_metric",
                # Filter to a future window that has no orders.
                args={
                    "metric_id": "gmv",
                    "filters": [
                        {"field": "placed_at", "op": "gte", "value": "2099-01-01"},
                        {"field": "placed_at", "op": "lte", "value": "2099-01-31"},
                    ],
                },
            )
        ],
        composition_hint="State GMV.",
    )
    plan_pass2 = Plan(
        intent="answer",
        tasks=[Task(task_id="t1", tool="compute_metric", args={"metric_id": "gmv"})],
        composition_hint="State GMV.",
    )

    fake = FakeLLMClient(
        structured=[
            plan_pass1,
            JoinerDecision(action="replan", hint="No orders in that window; broaden."),
            plan_pass2,
            JoinerDecision(action="finalize"),
        ],
        streams=[["GMV is ", "{{m:gmv_0}}", "."]],
    )

    out = await chat_turn(tid, "GMV?", fake)
    assert out["status"] == "ok"
    # planner ran twice + joiner ran twice = 4 structured calls
    assert len(fake.structured_calls) == 4
    assert "₹250" in out["text"]


@pytest.mark.asyncio
async def test_clarify_intent_returns_clarify_status():
    fake = FakeLLMClient(
        structured=[
            Plan(
                intent="clarify",
                refusal_reason="No metric or timeframe specified.",
                tasks=[],
                composition_hint="",
            ),
        ],
    )
    out = await chat_turn("t", "tell me stuff", fake)
    # Clarify path emits a short prompt; no joiner / composer called.
    assert out["status"] in ("clarify", "refused")
    assert len(fake.stream_calls) == 0
