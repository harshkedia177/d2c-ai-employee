"""Batch pull: runs each connector against mock_saas, writes raw rows, enqueues normalize jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

_ROOT_FOR_IMPORT = Path(__file__).parent.parent
if str(_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORT))

from sqlalchemy import text  # noqa: E402

from packages.config import settings  # noqa: E402
from packages.connectors.base import Checkpoint, Record  # noqa: E402
from packages.connectors.meta_ads.connector import MetaAdsConnector  # noqa: E402
from packages.connectors.shiprocket.connector import ShiprocketConnector  # noqa: E402
from packages.connectors.shopify.connector import ShopifyConnector  # noqa: E402
from packages.scaffolding.queues import enqueue  # noqa: E402
from packages.scaffolding.rate_limit import TokenBucket  # noqa: E402
from packages.warehouse.db import SessionLocal  # noqa: E402

log = logging.getLogger(__name__)

DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEMO_SLUG = "demo"
MERCHANT = "m000"

RAW_TABLES = {
    ("shopify", "orders"): "raw.shopify_orders",
    ("shopify", "line_items"): "raw.shopify_line_items",
    ("shopify", "customers"): "raw.shopify_customers",
    ("shopify", "products"): "raw.shopify_products",
    ("shopify", "refunds"): "raw.shopify_refunds",
    ("shiprocket", "shipments"): "raw.shiprocket_shipments",
    ("meta_ads", "campaigns"): "raw.meta_campaigns",
    ("meta_ads", "ad_insights"): "raw.meta_ad_insights",
}


async def _ensure_tenant(tenant_id: str) -> None:
    async with SessionLocal() as s:
        await s.execute(
            text(
                "INSERT INTO control.tenant (tenant_id, slug) "
                "VALUES (:t, :slug) ON CONFLICT (tenant_id) DO NOTHING"
            ),
            {"t": tenant_id, "slug": DEMO_SLUG},
        )
        await s.commit()


async def _truncate_tenant_data(tenant_id: str) -> None:
    async with SessionLocal() as s:
        for tbl in RAW_TABLES.values():
            await s.execute(text(f"DELETE FROM {tbl} WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(
            text("DELETE FROM raw.shopify_webhook_inbox WHERE tenant_id = :t"), {"t": tenant_id}
        )
        await s.execute(text('DELETE FROM core."order" WHERE tenant_id = :t'), {"t": tenant_id})
        await s.execute(text("DELETE FROM core.order_line WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(text("DELETE FROM core.customer WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(text("DELETE FROM core.product WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(text("DELETE FROM core.refund WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(text("DELETE FROM core.shipment WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(text("DELETE FROM core.campaign WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(
            text("DELETE FROM core.ad_spend_daily WHERE tenant_id = :t"), {"t": tenant_id}
        )
        await s.execute(text("DELETE FROM core.agent_runs WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(
            text("DELETE FROM control.connector_state WHERE tenant_id = :t"), {"t": tenant_id}
        )
        await s.execute(
            text("DELETE FROM control.queue_realtime WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
        await s.commit()


async def _insert_raw(table: str, tenant_id: str, record: Record, connector_version: str) -> int:
    payload_str = json.dumps(record.payload, default=str)
    async with SessionLocal() as s:
        result = await s.execute(
            text(f"""
              INSERT INTO {table} (
                tenant_id, source_id, payload, payload_hash,
                source_record_url, fetched_at, connector_version
              ) VALUES (
                :t, :sid, CAST(:p AS jsonb), :h,
                :u, :ts, :cv
              ) RETURNING row_id
            """),
            {
                "t": tenant_id,
                "sid": record.primary_key,
                "p": payload_str,
                "h": record.payload_hash,
                "u": record.source_record_url,
                "ts": record.fetched_at,
                "cv": connector_version,
            },
        )
        row_id = result.scalar_one()
        await s.commit()
    return row_id


async def _save_state(tenant_id: str, source_system: str, stream: str, cursor: dict) -> None:
    async with SessionLocal() as s:
        await s.execute(
            text("""
              INSERT INTO control.connector_state (tenant_id, source_system, stream, cursor, last_run_at)
              VALUES (:t, :s, :st, CAST(:c AS jsonb), now())
              ON CONFLICT (tenant_id, source_system, stream) DO UPDATE
                SET cursor = EXCLUDED.cursor, last_run_at = EXCLUDED.last_run_at
            """),
            {"t": tenant_id, "s": source_system, "st": stream, "c": json.dumps(cursor)},
        )
        await s.commit()


async def _load_state(tenant_id: str, source_system: str, stream: str) -> dict | None:
    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT cursor FROM control.connector_state "
                "WHERE tenant_id = :t AND source_system = :s AND stream = :st"
            ),
            {"t": tenant_id, "s": source_system, "st": stream},
        )
        row = r.first()
    if row is None or row.cursor is None:
        return None
    return dict(row.cursor)


async def _drain_one_connector(
    tenant_id: str,
    connector: Any,
    stream: str,
    config: dict[str, Any],
) -> tuple[int, int]:
    # connector.read may yield Records of multiple streams (Shopify emits
    # line_items inline with orders), so route each by record.stream.
    state = await _load_state(tenant_id, connector.source_system, stream)

    # Connectors use sync httpx; drain off-loop so concurrent drains overlap.
    items = await asyncio.to_thread(lambda: list(connector.read(stream, config, state)))

    records_written = 0
    checkpoints = 0
    for item in items:
        if isinstance(item, Record):
            key = (connector.source_system, item.stream)
            raw_table = RAW_TABLES.get(key)
            if raw_table is None:
                log.warning("no raw table for %s; skipping record", key)
                continue
            row_id = await _insert_raw(raw_table, tenant_id, item, connector.connector_version)
            await enqueue(
                "realtime",
                tenant_id,
                "connector_record",
                {
                    "source_system": connector.source_system,
                    "stream": item.stream,
                    "raw_table": raw_table,
                    "raw_row_id": row_id,
                },
            )
            records_written += 1
        elif isinstance(item, Checkpoint):
            await _save_state(tenant_id, connector.source_system, item.stream, item.cursor)
            checkpoints += 1
    return records_written, checkpoints


async def main_async(tenant_id: str, reset: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uuid.UUID(tenant_id)

    if reset:
        log.info("--reset: truncating data for tenant %s", tenant_id)
        await _truncate_tenant_data(tenant_id)

    await _ensure_tenant(tenant_id)

    shopify_cfg = {
        "base_url": settings.shopify_base_url,
        "merchant": MERCHANT,
        "shop_domain": f"{MERCHANT}.myshopify.com",
    }
    shiprocket_cfg = {
        "base_url": settings.shiprocket_base_url,
        "merchant": MERCHANT,
        "email": "demo@shoppin.app",
        "password": "demo",
    }
    meta_cfg = {
        "base_url": settings.meta_base_url,
        "ad_account": MERCHANT,
        "access_token": "mock-meta-token",
    }

    shopify_cfg["rate_limiter"] = TokenBucket.for_source_sync(
        redis_url=settings.redis_url,
        tenant_id=tenant_id,
        source="shopify",
    )
    shiprocket_cfg["rate_limiter"] = TokenBucket.for_source_sync(
        redis_url=settings.redis_url,
        tenant_id=tenant_id,
        source="shiprocket",
    )
    meta_cfg["rate_limiter"] = TokenBucket.for_source_sync(
        redis_url=settings.redis_url,
        tenant_id=tenant_id,
        source="meta_ads",
    )

    log.info("pulling shopify / shiprocket / meta in parallel")
    results = await asyncio.gather(
        _drain_one_connector(tenant_id, ShopifyConnector(), "orders", shopify_cfg),
        _drain_one_connector(tenant_id, ShiprocketConnector(), "shipments", shiprocket_cfg),
        _drain_one_connector(tenant_id, MetaAdsConnector(), "campaigns", meta_cfg),
        _drain_one_connector(tenant_id, MetaAdsConnector(), "ad_insights", meta_cfg),
    )
    for name, (r, c) in zip(
        ("shopify", "shiprocket", "meta campaigns", "meta ad_insights"), results, strict=True
    ):
        log.info("  %s: %d records, %d checkpoints", name, r, c)

    log.info("done")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=DEMO_TENANT_ID)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    asyncio.run(main_async(args.tenant, args.reset))


if __name__ == "__main__":
    main()
