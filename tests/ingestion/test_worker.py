import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from packages.ingestion.worker import process_one
from packages.scaffolding.queues import enqueue
from packages.udm.xref import canonical_id
from packages.warehouse.db import SessionLocal


async def _insert_raw_shopify_order(tenant_id: str, payload: dict) -> int:
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
                "u": f"https://test.example.com/admin/orders/{payload['id']}",
                "ts": datetime.now(UTC),
                "cv": "shopify@0.1.0",
            },
        )
        row_id = result.scalar_one()
        await s.commit()
    return row_id


async def _insert_raw_generic(table: str, tenant_id: str, source_id: str, payload: dict) -> int:
    p_str = json.dumps(payload)
    p_hash = hashlib.sha256(p_str.encode()).hexdigest()
    async with SessionLocal() as s:
        result = await s.execute(
            text(f"""
              INSERT INTO {table} (
                tenant_id, source_id, payload, payload_hash,
                source_record_url, fetched_at, connector_version
              ) VALUES (
                :t, :sid, CAST(:p AS jsonb), :h, :u, :ts, :cv
              ) RETURNING row_id
            """),
            {
                "t": tenant_id,
                "sid": source_id,
                "p": p_str,
                "h": p_hash,
                "u": f"https://test.example.com/{table}/{source_id}",
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
                "u": f"webhook://test.example.com/{tenant_id}/orders/create/{payload['id']}",
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
    # Scope cleanup strictly to test-only URL prefixes so we don't wipe
    # demo data created by `make demo`. Each test uses a fresh-UUID tenant_id.
    async with SessionLocal() as s:
        for tbl in [
            'core."order"',
            "core.customer",
            "core.product",
            "core.refund",
        ]:
            await s.execute(
                text(f"DELETE FROM {tbl} WHERE source_record_url LIKE 'https://test.example.com/%'")
            )
        for tbl in [
            "raw.shopify_orders",
            "raw.shopify_customers",
            "raw.shopify_products",
            "raw.shopify_refunds",
        ]:
            await s.execute(
                text(f"DELETE FROM {tbl} WHERE source_record_url LIKE 'https://test.example.com/%'")
            )
        await s.execute(
            text(
                "DELETE FROM raw.shopify_webhook_inbox "
                "WHERE source_record_url LIKE 'webhook://test.example.com/%'"
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_connector_record_job_writes_to_core_order():
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
    tid = str(uuid.uuid4())
    order = {
        "id": 999002,
        "gateway": "razorpay",
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


@pytest.mark.asyncio
async def test_connector_record_job_writes_to_core_customer():
    tid = str(uuid.uuid4())
    cust = {
        "id": 90001,
        "email": "x90001@example.com",
        "phone": "+919000000001",
        "default_address": {"country_code": "IN"},
        "created_at": "2026-04-01T00:00:00Z",
    }
    row_id = await _insert_raw_generic("raw.shopify_customers", tid, str(cust["id"]), cust)

    await enqueue(
        "realtime",
        tid,
        "connector_record",
        {
            "source_system": "shopify",
            "stream": "customers",
            "raw_table": "raw.shopify_customers",
            "raw_row_id": row_id,
        },
    )
    row = None
    for _ in range(50):
        await process_one()
        async with SessionLocal() as s:
            r = await s.execute(
                text(
                    "SELECT email_hash, country, raw_table, raw_row_id "
                    "FROM core.customer WHERE tenant_id = :t AND source_id = :sid"
                ),
                {"t": tid, "sid": str(cust["id"])},
            )
            row = r.first()
        if row is not None:
            break
    assert row is not None, "worker did not write the customer to core.customer"
    assert row.country == "IN"
    assert row.raw_table == "raw.shopify_customers"
    assert row.raw_row_id == row_id
    assert row.email_hash != "x90001@example.com"
    assert len(row.email_hash) == 64


@pytest.mark.asyncio
async def test_connector_record_job_writes_to_core_product():
    tid = str(uuid.uuid4())
    sku = f"SKU-TEST-{uuid.uuid4().hex[:8]}"
    product = {
        "sku": sku,
        "title": "Test Tee",
        "price": "499.00",
        "currency": "INR",
    }
    row_id = await _insert_raw_generic("raw.shopify_products", tid, sku, product)

    await enqueue(
        "realtime",
        tid,
        "connector_record",
        {
            "source_system": "shopify",
            "stream": "products",
            "raw_table": "raw.shopify_products",
            "raw_row_id": row_id,
        },
    )
    row = None
    for _ in range(50):
        await process_one()
        async with SessionLocal() as s:
            r = await s.execute(
                text(
                    "SELECT sku, title, price, raw_table, raw_row_id "
                    "FROM core.product WHERE tenant_id = :t AND source_id = :sid"
                ),
                {"t": tid, "sid": sku},
            )
            row = r.first()
        if row is not None:
            break
    assert row is not None, "worker did not write the product to core.product"
    assert row.sku == sku
    assert row.title == "Test Tee"
    assert float(row.price) == 499.00
    assert row.raw_table == "raw.shopify_products"
    assert row.raw_row_id == row_id


@pytest.mark.asyncio
async def test_connector_record_job_writes_to_core_refund():
    tid = str(uuid.uuid4())
    refund_id = f"refund-test-{uuid.uuid4().hex[:8]}"
    refund = {
        "id": refund_id,
        "amount": "250.00",
        "reason": "damaged",
        "created_at": "2026-05-03T10:00:00Z",
        "_order_id": "12345678",
    }
    row_id = await _insert_raw_generic("raw.shopify_refunds", tid, refund_id, refund)

    await enqueue(
        "realtime",
        tid,
        "connector_record",
        {
            "source_system": "shopify",
            "stream": "refunds",
            "raw_table": "raw.shopify_refunds",
            "raw_row_id": row_id,
        },
    )
    row = None
    for _ in range(50):
        await process_one()
        async with SessionLocal() as s:
            r = await s.execute(
                text(
                    "SELECT amount, reason, order_canonical_id, raw_table, raw_row_id "
                    "FROM core.refund WHERE tenant_id = :t AND source_id = :sid"
                ),
                {"t": tid, "sid": refund_id},
            )
            row = r.first()
        if row is not None:
            break
    assert row is not None, "worker did not write the refund to core.refund"
    assert float(row.amount) == 250.00
    assert row.reason == "damaged"
    assert row.raw_table == "raw.shopify_refunds"
    assert row.raw_row_id == row_id
    assert str(row.order_canonical_id) == canonical_id(tid, "order", "shopify", "12345678")
