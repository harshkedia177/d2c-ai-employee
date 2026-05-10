"""Test the chat route with a FakeLLMClient injected via monkeypatch."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from packages.api.main import app
from packages.llm.client import LLMResponse, ToolCall
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
    async with SessionLocal() as s:
        await s.execute(
            text(
                'DELETE FROM core."order" '
                "WHERE source_record_url = 'https://shop.example.com/admin/orders/chat-route-test' "
                "AND ingested_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_chat_route_returns_text_and_footnotes(monkeypatch):
    tid = str(uuid.uuid4())
    await _seed_order(tid, 999.50)

    fake = FakeLLMClient(
        [
            LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "gmv"})]),
            LLMResponse(text="GMV is {{m:gmv_0}}."),
        ]
    )
    # Inject the fake LLM
    monkeypatch.setattr("packages.api.chat_routes._llm", lambda: fake)

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
    assert r.status_code == 422  # Pydantic validation error


@pytest.mark.asyncio
async def test_chat_route_returns_refusal_when_verifier_exhausted(monkeypatch):
    tid = str(uuid.uuid4())
    await _seed_order(tid, 100)

    fake = FakeLLMClient(
        [
            LLMResponse(tool_calls=[ToolCall("compute_metric", {"metric_id": "gmv"})]),
            LLMResponse(text="GMV {{m:gmv_0}}, about 5 lakh."),
            LLMResponse(text="Roughly 5 lakh."),
            LLMResponse(text="Around 5 lakh."),
        ]
    )
    monkeypatch.setattr("packages.api.chat_routes._llm", lambda: fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post("/chat", json={"tenant_id": tid, "message": "GMV?"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "refused_verifier_exhausted"
    # ensure no literal numerals leaked
    from packages.chat.verifier import find_violations

    assert find_violations(data["text"], frozenset()) == []
