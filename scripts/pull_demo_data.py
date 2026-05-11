"""One-shot batch pull: runs each connector against mock_saas, writes Records
to the appropriate raw.* table, and enqueues a `connector_record` job for the
worker to normalize and write to core.

Same write-then-enqueue pattern as the production webhook route — no new
abstraction. The worker handles normalize → core write.

Idempotency: raw is append-only (re-runs duplicate raw rows but core has
ON CONFLICT DO NOTHING). Use --reset to TRUNCATE the tenant's raw data first.

Usage:
  uv run python scripts/pull_demo_data.py [--tenant=UUID] [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

# Ensure the repo root is on sys.path so `import packages.*` works when run as a script.
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

# (source_system, stream) → raw table name
RAW_TABLES = {
    ("shopify", "orders"): "raw.shopify_orders",
    ("shopify", "line_items"): "raw.shopify_line_items",
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
    """Drop all raw + core + agent_runs rows for this tenant. Used by --reset."""
    async with SessionLocal() as s:
        for tbl in RAW_TABLES.values():
            await s.execute(text(f"DELETE FROM {tbl} WHERE tenant_id = :t"), {"t": tenant_id})
        await s.execute(
            text("DELETE FROM raw.shopify_webhook_inbox WHERE tenant_id = :t"), {"t": tenant_id}
        )
        await s.execute(text('DELETE FROM core."order" WHERE tenant_id = :t'), {"t": tenant_id})
        await s.execute(text("DELETE FROM core.order_line WHERE tenant_id = :t"), {"t": tenant_id})
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
    """Insert one Record into the raw table. Returns the new row_id."""
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
    """Run connector.read for one stream; for every yielded item, write to raw
    and enqueue a connector_record job. Returns (records_written, checkpoints_saved).

    Note: connector.read may yield Records of MULTIPLE streams (Shopify emits
    line_items inline with orders). Route each by record.stream, not by the
    stream argument passed to read()."""
    bucket = await TokenBucket.for_source(
        redis_url=settings.redis_url,
        tenant_id=tenant_id,
        source=connector.source_system,
    )
    state = await _load_state(tenant_id, connector.source_system, stream)

    records_written = 0
    checkpoints = 0
    for item in connector.read(stream, config, state):
        await bucket.acquire()
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
    uuid.UUID(tenant_id)  # validate

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

    # Pull in dependency order so cross-source joins resolve cleanly.
    # 1) Shopify orders (emits orders + line_items records)
    log.info("pulling shopify orders + line_items")
    r, c = await _drain_one_connector(tenant_id, ShopifyConnector(), "orders", shopify_cfg)
    log.info("  shopify: %d records, %d checkpoints", r, c)

    # 2) Shiprocket shipments (refers to shopify order ids)
    log.info("pulling shiprocket shipments")
    r, c = await _drain_one_connector(tenant_id, ShiprocketConnector(), "shipments", shiprocket_cfg)
    log.info("  shiprocket: %d records, %d checkpoints", r, c)

    # 3) Meta campaigns
    log.info("pulling meta campaigns")
    r, c = await _drain_one_connector(tenant_id, MetaAdsConnector(), "campaigns", meta_cfg)
    log.info("  meta campaigns: %d records, %d checkpoints", r, c)

    # 4) Meta ad_insights
    log.info("pulling meta ad_insights")
    r, c = await _drain_one_connector(tenant_id, MetaAdsConnector(), "ad_insights", meta_cfg)
    log.info("  meta ad_insights: %d records, %d checkpoints", r, c)

    log.info("done — worker will drain queue and write to core.*")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=DEMO_TENANT_ID)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    asyncio.run(main_async(args.tenant, args.reset))


if __name__ == "__main__":
    main()
