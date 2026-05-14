import base64
import hashlib
import hmac
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from packages.api.main import app
from packages.config import settings
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


@pytest.mark.asyncio
async def test_shopify_webhook_hmac_pass(monkeypatch):
    monkeypatch.setattr(settings, "shopify_webhook_secret", "test_secret")
    tid = str(uuid.uuid4())
    body = b'{"id":42,"total_price":"100"}'
    sig = base64.b64encode(hmac.new(b"test_secret", body, hashlib.sha256).digest()).decode("ascii")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            f"/webhooks/shopify/{tid}/orders/create",
            content=body,
            headers={"X-Shopify-Hmac-Sha256": sig, "Content-Type": "application/json"},
        )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_shopify_webhook_hmac_reject(monkeypatch):
    monkeypatch.setattr(settings, "shopify_webhook_secret", "test_secret")
    tid = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        bad = await ac.post(
            f"/webhooks/shopify/{tid}/orders/create",
            content=b'{"id":42}',
            headers={"X-Shopify-Hmac-Sha256": "obviously-wrong"},
        )
        missing = await ac.post(
            f"/webhooks/shopify/{tid}/orders/create",
            content=b'{"id":42}',
        )
    assert bad.status_code == 401
    assert missing.status_code == 401
