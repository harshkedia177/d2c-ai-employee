"""End-to-end tests for the realtime queue worker.

These tests insert real raw rows, enqueue real jobs, and verify the worker
populates core.* and core.agent_runs correctly. No mocks — the whole point
is to prove the production code path works.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from packages.ingestion.worker import process_one
from packages.scaffolding.queues import enqueue
from packages.warehouse.db import SessionLocal

# ---------- helpers ----------


async def _insert_raw_shopify_order(tenant_id: str, payload: dict) -> int:
    """Insert a row into raw.shopify_orders, return the row_id."""
    p_str = json.dumps(payload)
    p_hash = hashlib.sha256(p_str.encode()).hexdigest()
    async with SessionLocal() as s:
        result = await s.execute(
            text("""
              INSERT INTO raw.shopify_orders (
                tenant_id, source_id, payload, payload_hash,
                source_record_url, fetched_at, connector_version
              ) VALUES (
                :t, :sid, CAST(:p AS jsonb), :h,
                :u, :ts, :cv
              ) RETURNING row_id
            """),
            {
                "t": tenant_id,
                "sid": str(payload["id"]),
                "p": p_str,
                "h": p_hash,
                "u": f"https://m000.myshopify.com/admin/orders/{payload['id']}",
                "ts": datetime.now(UTC),
                "cv": "shopify@0.1.0",
            },
        )
        row_id = result.scalar_one()
        await s.commit()
    return row_id


async def _insert_raw_webhook(tenant_id: str, payload: dict) -> int:
    p_str = json.dumps(payload)
    p_hash = hashlib.sha256(p_str.encode()).hexdigest()
    async with SessionLocal() as s:
        result = await s.execute(
            text("""
              INSERT INTO raw.shopify_webhook_inbox (
                tenant_id, source_id, payload, payload_hash,
                source_record_url, fetched_at, connector_version
              ) VALUES (
                :t, :sid, CAST(:p AS jsonb), :h, :u, :ts, :cv
              ) RETURNING row_id
            """),
            {
                "t": tenant_id,
                "sid": str(payload["id"]),
                "p": p_str,
                "h": p_hash,
                "u": f"webhook://shopify/{tenant_id}/orders/create/{payload['id']}",
                "ts": datetime.now(UTC),
                "cv": "shopify@webhook@0.1.0",
            },
        )
        row_id = result.scalar_one()
        await s.commit()
    return row_id


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    # tests use unique tenant_ids per run, but clean up any old test rows
    async with SessionLocal() as s:
        await s.execute(
            text(
                'DELETE FROM core."order" '
                "WHERE source_record_url LIKE 'https://m000.myshopify.com/admin/orders/%' "
                "AND ingested_at < now() - interval '5 minutes'"
            )
        )
        await s.execute(
            text("DELETE FROM raw.shopify_orders WHERE ingested_at < now() - interval '5 minutes'")
        )
        await s.execute(
            text(
                "DELETE FROM raw.shopify_webhook_inbox "
                "WHERE ingested_at < now() - interval '5 minutes'"
            )
        )
        await s.execute(
            text(
                "DELETE FROM core.agent_runs "
                "WHERE agent_id = 'rto_risk_flagger' "
                "AND triggered_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


# ---------- tests ----------


@pytest.mark.asyncio
async def test_connector_record_job_writes_to_core_order():
    """The worker normalizes a Shopify order Record from raw and writes
    a row to core.order with the canonical_id from xref."""
    tid = str(uuid.uuid4())
    order = {
        "id": 12345001,
        "name": "#9001",
        "created_at": "2026-05-01T00:00:00Z",
        "updated_at": "2026-05-01T00:00:00Z",
        "financial_status": "paid",
        "total_price": "1234.50",
        "subtotal_price": "1100",
        "total_tax": "100",
        "total_discounts": "0",
        "total_shipping_price_set": {"shop_money": {"amount": "34.50"}},
        "currency": "INR",
        "gateway": "razorpay",
        "shipping_address": {"zip": "560001"},
        "customer": {"id": 7001},
        "line_items": [],
        "note_attributes": [{"name": "utm_campaign", "value": "camp-1"}],
    }
    row_id = await _insert_raw_shopify_order(tid, order)

    await enqueue(
        "realtime",
        tid,
        "connector_record",
        {
            "source_system": "shopify",
            "stream": "orders",
            "raw_table": "raw.shopify_orders",
            "raw_row_id": row_id,
        },
    )

    # Drain — may need to process other tests' jobs too; loop until ours runs.
    # Identify ours by checking core.order.
    row = None
    for _ in range(50):
        await process_one()
        async with SessionLocal() as s:
            r = await s.execute(
                text(
                    "SELECT total, gateway, shipping_pincode, utm_campaign, "
                    "       source_system, raw_table, raw_row_id "
                    'FROM core."order" '
                    "WHERE tenant_id = :t AND source_id = :sid"
                ),
                {"t": tid, "sid": str(order["id"])},
            )
            row = r.first()
        if row is not None:
            break
    assert row is not None, "worker did not write the order to core.order"
    assert float(row.total) == 1234.50
    assert row.gateway == "razorpay"
    assert row.shipping_pincode == "560001"
    assert row.utm_campaign == "camp-1"
    assert row.source_system == "shopify"
    assert row.raw_table == "raw.shopify_orders"
    assert row.raw_row_id == row_id


@pytest.mark.asyncio
async def test_shopify_webhook_job_runs_rto_flagger_for_cod_order():
    """Worker handles a shopify_webhook job: loads from inbox, fires RTO Flagger."""
    tid = str(uuid.uuid4())
    order = {
        "id": 999001,
        "gateway": "Cash on Delivery",
        "total_price": "2400",
        "shipping_address": {"zip": "110084", "address1": "PG hostel block"},
        "customer": {"id": 7},
        "line_items": [{"sku": "SKU-1"}],
        "created_at": "2026-05-01T23:00:00Z",
    }
    row_id = await _insert_raw_webhook(tid, order)
    await enqueue(
        "realtime",
        tid,
        "shopify_webhook",
        {
            "topic": "orders/create",
            "raw_row_id": row_id,
            "source_id": str(order["id"]),
        },
    )

    # Drain until our agent_run shows up.
    row = None
    for _ in range(50):
        await process_one()
        async with SessionLocal() as s:
            r = await s.execute(
                text(
                    "SELECT band, score, proposed_action "
                    "FROM core.agent_runs "
                    "WHERE tenant_id = :t AND agent_id = 'rto_risk_flagger' "
                    "ORDER BY triggered_at DESC LIMIT 1"
                ),
                {"t": tid},
            )
            row = r.first()
        if row is not None:
            break
    assert row is not None, "worker did not fire RTO Flagger"
    assert row.band in ("LOW", "MED", "HIGH")
    assert row.score is not None
    assert row.proposed_action["dry_run"] is True


@pytest.mark.asyncio
async def test_shopify_webhook_job_skips_non_cod_orders():
    """Non-COD orders must be silently complete with no agent_runs row."""
    tid = str(uuid.uuid4())
    order = {
        "id": 999002,
        "gateway": "razorpay",  # NOT COD
        "total_price": "2400",
        "shipping_address": {"zip": "110084"},
        "customer": {"id": 8},
        "line_items": [],
        "created_at": "2026-05-01T14:00:00Z",
    }
    row_id = await _insert_raw_webhook(tid, order)
    await enqueue(
        "realtime",
        tid,
        "shopify_webhook",
        {
            "topic": "orders/create",
            "raw_row_id": row_id,
            "source_id": str(order["id"]),
        },
    )
    # Drain until the queue empties for this tenant.
    for _ in range(50):
        progressed = await process_one()
        if not progressed:
            break

    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT COUNT(*) FROM core.agent_runs "
                "WHERE tenant_id = :t AND agent_id = 'rto_risk_flagger'"
            ),
            {"t": tid},
        )
        count = r.scalar_one()
    assert count == 0
