import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from packages.agents.scheduler import (
    run_meta_pauser_for_tenant,
    run_pincode_blocker_for_tenant,
)
from packages.warehouse.db import SessionLocal


async def _insert_order(
    tenant_id: str,
    canonical_id: str,
    pincode: str,
    total: float,
    utm_campaign: str | None = None,
    placed_at: datetime | None = None,
) -> None:
    placed_at = placed_at or datetime.now(UTC) - timedelta(days=5)
    async with SessionLocal() as s:
        await s.execute(
            text("""
              INSERT INTO core."order" (
                tenant_id, canonical_id, placed_at, status, gateway,
                total, currency, shipping_pincode, utm_campaign,
                source_system, source_id, source_record_url,
                raw_table, raw_row_id, raw_payload_hash,
                fetched_at, ingested_at, connector_version
              ) VALUES (
                :t, :c, :p, 'paid', 'razorpay',
                :total, 'INR', :pin, :utm,
                'shopify', :sid, 'https://shop/orders/' || :sid,
                'raw.shopify_orders', 1, 'h',
                now(), now(), 'shopify@0.1.0'
              )
            """),
            {
                "t": tenant_id,
                "c": canonical_id,
                "p": placed_at,
                "total": total,
                "pin": pincode,
                "utm": utm_campaign,
                "sid": canonical_id[:8],
            },
        )
        await s.commit()


async def _insert_shipment(
    tenant_id: str,
    order_canonical_id: str,
    is_rto: bool,
) -> None:
    canonical_id = str(uuid.uuid4())
    async with SessionLocal() as s:
        await s.execute(
            text("""
              INSERT INTO core.shipment (
                tenant_id, canonical_id, order_canonical_id, status, is_rto,
                freight_amount, shipped_at,
                source_system, source_id, source_record_url,
                raw_table, raw_row_id, raw_payload_hash,
                fetched_at, ingested_at, connector_version
              ) VALUES (
                :t, :c, :o, 'Delivered', :rto,
                60.0, now() - interval '3 days',
                'shiprocket', :sid, 'https://shiprocket/' || :sid,
                'raw.shiprocket_shipments', 1, 'h',
                now(), now(), 'shiprocket@0.1.0'
              )
            """),
            {
                "t": tenant_id,
                "c": canonical_id,
                "o": order_canonical_id,
                "rto": is_rto,
                "sid": canonical_id[:8],
            },
        )
        await s.commit()


async def _insert_campaign(tenant_id: str, canonical_id: str, name: str) -> None:
    async with SessionLocal() as s:
        await s.execute(
            text("""
              INSERT INTO core.campaign (
                tenant_id, canonical_id, platform, name, status,
                source_system, source_id, source_record_url,
                raw_table, raw_row_id, raw_payload_hash,
                fetched_at, ingested_at, connector_version
              ) VALUES (
                :t, :c, 'meta', :n, 'ACTIVE',
                'meta_ads', :sid, 'https://fb/' || :sid,
                'raw.meta_campaigns', 1, 'h',
                now(), now(), 'meta_ads@0.1.0'
              )
            """),
            {"t": tenant_id, "c": canonical_id, "n": name, "sid": canonical_id[:8]},
        )
        await s.commit()


async def _insert_ad_spend(
    tenant_id: str,
    campaign_canonical_id: str,
    ad_id: str,
    spend: float,
    conversions: int = 100,
) -> None:
    async with SessionLocal() as s:
        await s.execute(
            text("""
              INSERT INTO core.ad_spend_daily (
                tenant_id, date, campaign_canonical_id, ad_id,
                impressions, clicks, spend, currency, conversions,
                source_system, source_id, source_record_url,
                raw_table, raw_row_id, raw_payload_hash,
                fetched_at, ingested_at, connector_version
              ) VALUES (
                :t, CURRENT_DATE - 5, :c, :a,
                10000, 200, :s, 'INR', :conv,
                'meta_ads', :sid, 'https://fb/insight/' || :sid,
                'raw.meta_ad_insights', 1, 'h',
                now(), now(), 'meta_ads@0.1.0'
              )
            """),
            {
                "t": tenant_id,
                "c": campaign_canonical_id,
                "a": ad_id,
                "s": spend,
                "conv": conversions,
                "sid": ad_id,
            },
        )
        await s.commit()


@pytest.fixture
async def _scheduler_cleanup():
    # No-op: each test uses a fresh-UUID tenant_id, so rows don't collide.
    # Avoid deleting here so `make demo` data stays intact across runs.
    created: list[str] = []
    yield created


@pytest.mark.asyncio
async def test_pincode_blocker_flags_high_rto_pincode_and_skips_clean(
    _scheduler_cleanup,
):
    tid = str(uuid.uuid4())
    _scheduler_cleanup.append(tid)

    high_orders = [str(uuid.uuid4()) for _ in range(25)]
    for i, oid in enumerate(high_orders):
        await _insert_order(tid, oid, "110084", 2400.0)
        await _insert_shipment(tid, oid, is_rto=(i % 3 == 0))

    low_orders = [str(uuid.uuid4()) for _ in range(25)]
    for i, oid in enumerate(low_orders):
        await _insert_order(tid, oid, "560001", 2400.0)
        await _insert_shipment(tid, oid, is_rto=(i == 0))

    run = await run_pincode_blocker_for_tenant(tid, window_days=90, min_orders=20)

    proposals = run.proposed_action["payload"]["proposals"]
    pincodes_flagged = {p["pincode"] for p in proposals}
    assert "110084" in pincodes_flagged
    assert "560001" not in pincodes_flagged
    assert run.band == "HIGH"


@pytest.mark.asyncio
async def test_meta_pauser_pauses_low_post_rto_roas_campaign(_scheduler_cleanup):
    tid = str(uuid.uuid4())
    _scheduler_cleanup.append(tid)

    cid_a = str(uuid.uuid4())
    await _insert_campaign(tid, cid_a, "Bad Campaign")
    await _insert_ad_spend(tid, cid_a, "ad-a-1", spend=10_000, conversions=80)
    for _ in range(4):
        oid = str(uuid.uuid4())
        await _insert_order(tid, oid, "560001", 1000.0, utm_campaign="Bad Campaign")
        await _insert_shipment(tid, oid, is_rto=False)

    cid_b = str(uuid.uuid4())
    await _insert_campaign(tid, cid_b, "Good Campaign")
    await _insert_ad_spend(tid, cid_b, "ad-b-1", spend=5_000, conversions=200)
    for _ in range(15):
        oid = str(uuid.uuid4())
        await _insert_order(tid, oid, "560001", 1000.0, utm_campaign="Good Campaign")
        await _insert_shipment(tid, oid, is_rto=False)

    run = await run_meta_pauser_for_tenant(tid, window_days=14)

    proposals = run.proposed_action["payload"]["proposals"]
    actions_by_name = {p["name"]: p["action"] for p in proposals}
    assert actions_by_name.get("Bad Campaign") == "pause_campaign"
    assert "Good Campaign" not in actions_by_name
    assert run.band == "HIGH"
