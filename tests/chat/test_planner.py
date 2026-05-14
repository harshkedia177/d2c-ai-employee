import uuid

import pytest
from sqlalchemy import text

from packages.chat.planner import chat_turn
from packages.llm.client import LLMResponse, ToolCall
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
                'shopify', :sid, 'https://shop.example.com/admin/orders/planner-test',
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
                "WHERE source_record_url = 'https://shop.example.com/admin/orders/planner-test' "
                "AND ingested_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_planner_runs_compute_metric_and_renders_with_citation():
    tid = str(uuid.uuid4())
    await _seed_order(tid, 999.50)

    fake = FakeLLMClient(
        [
            LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "gmv"})]),
            LLMResponse(text="Your GMV is {{m:gmv_0}}."),
        ]
    )

    out = await chat_turn(tid, "what's my GMV?", fake)
    assert out["status"] == "ok"
    assert "₹999.50" in out["text"]
    assert len(out["footnotes"]) == 1
    fn = out["footnotes"][0]
    assert fn["query_hash"]
    assert fn["citations"][0]["url"] == "https://shop.example.com/admin/orders/planner-test"


@pytest.mark.asyncio
async def test_planner_rejects_literal_numeral_then_retries():
    tid = str(uuid.uuid4())
    await _seed_order(tid, 500.0)

    fake = FakeLLMClient(
        [
            LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "gmv"})]),
            LLMResponse(text="GMV is {{m:gmv_0}}, roughly 5 lakh."),
            LLMResponse(text="GMV is {{m:gmv_0}}."),
        ]
    )

    out = await chat_turn(tid, "GMV?", fake)
    assert out["status"] == "ok"
    assert "₹500" in out["text"]
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_planner_refuses_after_max_verifier_retries():
    tid = str(uuid.uuid4())
    await _seed_order(tid, 100.0)

    fake = FakeLLMClient(
        [
            LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "gmv"})]),
            LLMResponse(text="GMV is {{m:gmv_0}}, about 5 lakh."),
            LLMResponse(text="Sure, around 5 lakh more or less."),
            LLMResponse(text="Roughly 5 lakh."),
        ]
    )

    out = await chat_turn(tid, "GMV?", fake)
    assert out["status"] == "refused_verifier_exhausted"
    from packages.chat.verifier import find_violations

    assert find_violations(out["text"], frozenset()) == []


@pytest.mark.asyncio
async def test_planner_handles_tool_error_gracefully():
    tid = str(uuid.uuid4())

    fake = FakeLLMClient(
        [
            LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "made_up_metric"})]),
            LLMResponse(text="I don't have that metric available."),
        ]
    )

    out = await chat_turn(tid, "made_up?", fake)
    assert out["status"] == "ok"
    assert out["text"] == "I don't have that metric available."


@pytest.mark.asyncio
async def test_planner_pct_format_for_rate_metrics():
    tid = str(uuid.uuid4())
    fake = FakeLLMClient(
        [
            LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "rto_rate"})]),
            LLMResponse(text="RTO rate is {{m:rto_rate_0}}."),
        ]
    )
    out = await chat_turn(tid, "rto rate?", fake)
    assert out["status"] == "ok"
    assert "%" in out["text"] or "—" in out["text"]


@pytest.mark.asyncio
async def test_planner_handles_no_tools_just_text():
    fake = FakeLLMClient(
        [
            LLMResponse(text="Hello! Ask me about GMV, RTO rate, or campaigns."),
        ]
    )
    out = await chat_turn("t", "hello", fake)
    assert out["status"] == "ok"
    assert "Hello" in out["text"]


@pytest.mark.asyncio
async def test_planner_exhausts_turns_if_model_loops():
    tid = str(uuid.uuid4())
    scripted = [
        LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "gmv"})])
        for _ in range(20)
    ]
    fake = FakeLLMClient(scripted)
    out = await chat_turn(tid, "loop", fake)
    assert out["status"] == "exhausted_turns"
