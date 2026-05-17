"""HTTP-level tests for POST /chat and POST /chat/stream.

Uses FakeLLMClient with the new structured + streaming contract — there is
no longer a tool-call ReAct loop; the orchestrator drives planner / joiner /
composer in separate calls.
"""

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from packages.api.chat_routes import get_llm
from packages.api.main import app
from packages.chat.orchestrator.plan import JoinerDecision, Plan, Task
from packages.llm.fake import FakeLLMClient
from packages.warehouse.db import SessionLocal


async def _seed_order(tid: str, total: float = 1234.0) -> None:
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
                'shopify', :sid, 'https://shop.example.com/admin/orders/chat-route-test',
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
    app.dependency_overrides.clear()
    async with SessionLocal() as s:
        await s.execute(
            text(
                'DELETE FROM core."order" '
                "WHERE source_record_url = 'https://shop.example.com/admin/orders/chat-route-test' "
                "AND ingested_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


def _happy_gmv_fake() -> FakeLLMClient:
    return FakeLLMClient(
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


@pytest.mark.asyncio
async def test_chat_route_returns_text_and_footnotes():
    tid = str(uuid.uuid4())
    await _seed_order(tid, 999.50)

    app.dependency_overrides[get_llm] = _happy_gmv_fake

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post("/chat", json={"tenant_id": tid, "message": "what's my GMV?"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "₹999.50" in data["text"]
    assert len(data["footnotes"]) == 1


@pytest.mark.asyncio
async def test_chat_route_rejects_invalid_payload():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post("/chat", json={"message": "missing tenant_id"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_chat_stream_emits_sse_events():
    """End-to-end SSE: plan -> tool events -> compose tokens -> done."""
    tid = str(uuid.uuid4())
    await _seed_order(tid, 999.50)

    app.dependency_overrides[get_llm] = _happy_gmv_fake

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            "/chat/stream",
            json={"tenant_id": tid, "message": "what's my GMV?"},
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers.get("content-type", "")
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
    raw = body.decode("utf-8")

    # SSE frames are "event: name\ndata: {...}\n\n". Parse the event names.
    frames = [f for f in raw.split("\n\n") if f.strip()]
    event_names = [
        line.split(":", 1)[1].strip()
        for f in frames
        for line in f.split("\n")
        if line.startswith("event:")
    ]
    # Required event sequence on the happy path.
    assert "plan" in event_names
    assert "tool_start" in event_names
    assert "tool_result" in event_names
    assert "join_decision" in event_names
    assert "compose_start" in event_names
    assert "token" in event_names
    assert "footnote" in event_names
    assert event_names[-1] == "done"

    # The token payloads should already have the placeholder substituted
    # (renderer substitutes server-side before emitting).
    token_payloads: list[str] = []
    for f in frames:
        is_token = any(line.strip() == "event: token" for line in f.split("\n"))
        if not is_token:
            continue
        for line in f.split("\n"):
            if line.startswith("data:"):
                token_payloads.append(json.loads(line[5:].strip())["text"])
    joined = "".join(token_payloads)
    assert "₹999.50" in joined
