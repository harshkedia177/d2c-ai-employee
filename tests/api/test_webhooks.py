import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from packages.api.main import app
from packages.warehouse.db import SessionLocal


@pytest.mark.asyncio
async def test_shopify_webhook_returns_200_quickly():
    tid = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        t0 = time.perf_counter()
        r = await ac.post(
            f"/webhooks/shopify/{tid}/orders/create",
            json={"id": 9999, "total_price": "499", "gateway": "Cash on Delivery"},
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["raw_row_id"]
    assert elapsed_ms < 500, f"webhook handler too slow: {elapsed_ms:.1f}ms"


@pytest.mark.asyncio
async def test_webhook_writes_to_inbox_and_enqueues_realtime_job():
    tid = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            f"/webhooks/shopify/{tid}/orders/create",
            json={"id": 12345, "total_price": "999", "gateway": "razorpay"},
        )
    assert r.status_code == 200
    raw_row_id = r.json()["raw_row_id"]

    # inbox row exists with our tenant + source_id
    async with SessionLocal() as s:
        inbox = await s.execute(
            text(
                "SELECT source_id, source_record_url FROM raw.shopify_webhook_inbox "
                "WHERE tenant_id = :t AND row_id = :r"
            ),
            {"t": tid, "r": raw_row_id},
        )
        row = inbox.first()
        assert row is not None
        assert row.source_id == "12345"
        assert row.source_record_url == f"webhook://shopify/{tid}/orders/create/12345"

        # realtime queue job was enqueued
        q = await s.execute(
            text(
                "SELECT kind, payload FROM control.queue_realtime "
                "WHERE tenant_id = :t AND completed_at IS NULL ORDER BY id DESC LIMIT 1"
            ),
            {"t": tid},
        )
        qrow = q.first()
        assert qrow is not None
        assert qrow.kind == "shopify_webhook"
        assert qrow.payload["raw_row_id"] == raw_row_id
        assert qrow.payload["topic"] == "orders/create"
        assert qrow.payload["source_id"] == "12345"


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
