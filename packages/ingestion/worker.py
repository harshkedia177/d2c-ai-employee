"""Realtime queue worker.

Drains `control.queue_realtime` and dispatches jobs by `kind`:

  - shopify_webhook       (produced by packages/api/webhook_routes.py)
                          Loads payload from raw.shopify_webhook_inbox,
                          runs RTORiskFlagger if gateway == "Cash on Delivery".

  - connector_record      (produced by scripts/pull_demo_data.py)
                          Loads payload from the source raw table, runs the
                          matching normalizer, inserts the result into the
                          matching core.* table with ON CONFLICT DO NOTHING.

No new abstraction layer — two literal dispatch dicts + one handler per kind.
Reuses every existing utility unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import datetime
from typing import Any

from sqlalchemy import text

from packages.agents.base import AgentContext
from packages.agents.rto_risk_flagger import RTORiskFlagger
from packages.connectors.base import Record
from packages.scaffolding.queues import complete, dequeue, fail
from packages.udm.normalize.meta_to_udm import (
    ad_spend_daily_from_meta,
    campaign_from_meta,
)
from packages.udm.normalize.shiprocket_to_udm import shipment_from_shiprocket
from packages.udm.normalize.shopify_to_udm import (
    order_from_shopify,
    order_line_from_shopify,
)
from packages.warehouse.db import SessionLocal

log = logging.getLogger(__name__)

# Dispatch table: (source_system, stream) → (normalizer_fn, target_core_table_short_name)
NORMALIZER_DISPATCH: dict[tuple[str, str], tuple[Any, str]] = {
    ("shopify", "orders"): (order_from_shopify, "order"),
    ("shopify", "line_items"): (order_line_from_shopify, "order_line"),
    ("shiprocket", "shipments"): (shipment_from_shiprocket, "shipment"),
    ("meta_ads", "campaigns"): (campaign_from_meta, "campaign"),
    ("meta_ads", "ad_insights"): (ad_spend_daily_from_meta, "ad_spend_daily"),
}

# INSERT SQL per target entity. Each idempotent via ON CONFLICT DO NOTHING.
# Column lists MUST match the migration in packages/warehouse/migrations/versions/0001_init.py.
_CORE_INSERTS: dict[str, str] = {
    "order": """
      INSERT INTO core."order" (
        tenant_id, canonical_id, customer_canonical_id, placed_at, status, gateway,
        subtotal, tax, shipping_amount, discount, total, currency,
        shipping_pincode, utm_campaign, utm_source,
        source_system, source_id, source_record_url,
        raw_table, raw_row_id, raw_payload_hash,
        fetched_at, ingested_at, connector_version
      ) VALUES (
        :tenant_id, :canonical_id, :customer_canonical_id, :placed_at, :status, :gateway,
        :subtotal, :tax, :shipping_amount, :discount, :total, :currency,
        :shipping_pincode, :utm_campaign, :utm_source,
        :source_system, :source_id, :source_record_url,
        :raw_table, :raw_row_id, :raw_payload_hash,
        :fetched_at, :ingested_at, :connector_version
      ) ON CONFLICT (tenant_id, canonical_id) DO NOTHING
    """,
    "order_line": """
      INSERT INTO core.order_line (
        tenant_id, order_canonical_id, line_id, product_canonical_id, sku,
        qty, unit_price, line_total, discount,
        source_system, source_id, source_record_url,
        raw_table, raw_row_id, raw_payload_hash,
        fetched_at, ingested_at, connector_version
      ) VALUES (
        :tenant_id, :order_canonical_id, :line_id, :product_canonical_id, :sku,
        :qty, :unit_price, :line_total, :discount,
        :source_system, :source_id, :source_record_url,
        :raw_table, :raw_row_id, :raw_payload_hash,
        :fetched_at, :ingested_at, :connector_version
      ) ON CONFLICT (tenant_id, order_canonical_id, line_id) DO NOTHING
    """,
    "shipment": """
      INSERT INTO core.shipment (
        tenant_id, canonical_id, order_canonical_id, carrier, tracking_number,
        status, is_rto, freight_amount, shipped_at, delivered_at, rto_at,
        source_system, source_id, source_record_url,
        raw_table, raw_row_id, raw_payload_hash,
        fetched_at, ingested_at, connector_version
      ) VALUES (
        :tenant_id, :canonical_id, :order_canonical_id, :carrier, :tracking_number,
        :status, :is_rto, :freight_amount, :shipped_at, :delivered_at, :rto_at,
        :source_system, :source_id, :source_record_url,
        :raw_table, :raw_row_id, :raw_payload_hash,
        :fetched_at, :ingested_at, :connector_version
      ) ON CONFLICT (tenant_id, canonical_id) DO NOTHING
    """,
    "campaign": """
      INSERT INTO core.campaign (
        tenant_id, canonical_id, platform, name, objective, status,
        source_system, source_id, source_record_url,
        raw_table, raw_row_id, raw_payload_hash,
        fetched_at, ingested_at, connector_version
      ) VALUES (
        :tenant_id, :canonical_id, :platform, :name, :objective, :status,
        :source_system, :source_id, :source_record_url,
        :raw_table, :raw_row_id, :raw_payload_hash,
        :fetched_at, :ingested_at, :connector_version
      ) ON CONFLICT (tenant_id, canonical_id) DO NOTHING
    """,
    "ad_spend_daily": """
      INSERT INTO core.ad_spend_daily (
        tenant_id, date, campaign_canonical_id, ad_set_id, ad_id,
        impressions, clicks, spend, currency, conversions, revenue_attributed,
        source_system, source_id, source_record_url,
        raw_table, raw_row_id, raw_payload_hash,
        fetched_at, ingested_at, connector_version
      ) VALUES (
        :tenant_id, :date, :campaign_canonical_id, :ad_set_id, :ad_id,
        :impressions, :clicks, :spend, :currency, :conversions, :revenue_attributed,
        :source_system, :source_id, :source_record_url,
        :raw_table, :raw_row_id, :raw_payload_hash,
        :fetched_at, :ingested_at, :connector_version
      ) ON CONFLICT (tenant_id, date, campaign_canonical_id, ad_id) DO NOTHING
    """,
}


# Per-entity timestamp string fields that asyncpg requires as datetime.
# (Provenance columns fetched_at/ingested_at come from the normalizer as
# real datetimes already; we only coerce stringy payload-derived ones.)
_TIMESTAMP_FIELDS: dict[str, tuple[str, ...]] = {
    "order": ("placed_at",),
    "order_line": (),
    "shipment": ("shipped_at", "delivered_at", "rto_at"),
    "campaign": (),
    "ad_spend_daily": (),
}


def _parse_ts(value: Any) -> Any:
    """Coerce an ISO-8601 string (possibly with trailing 'Z') to datetime.
    Pass through None and existing datetimes unchanged."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


# ---------- handlers ----------


async def _handle_connector_record(job: dict[str, Any]) -> None:
    """Job kind: connector_record. Payload contains references to a raw row
    that was already INSERTed by the pull-script. We reconstruct a Record,
    run the matching normalizer, write to core."""
    p = job["payload"]
    source_system = p["source_system"]
    stream = p["stream"]
    raw_table = p["raw_table"]
    raw_row_id = int(p["raw_row_id"])
    tenant_id = str(job["tenant_id"])

    dispatch = NORMALIZER_DISPATCH.get((source_system, stream))
    if dispatch is None:
        log.warning("no normalizer for (%s, %s); skipping", source_system, stream)
        return
    normalizer_fn, entity = dispatch

    # Fetch the raw payload + provenance from the raw table.
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                f"SELECT source_id, payload, source_record_url, fetched_at, "
                f"payload_hash, connector_version "
                f"FROM {raw_table} WHERE row_id = :r AND tenant_id = :t"
            ),
            {"r": raw_row_id, "t": tenant_id},
        )
        row = result.first()
        if row is None:
            raise RuntimeError(
                f"raw row not found: {raw_table} row_id={raw_row_id} tenant={tenant_id}"
            )

    record = Record(
        stream=stream,
        primary_key=str(row.source_id),
        payload=dict(row.payload),
        source_record_url=row.source_record_url,
        fetched_at=row.fetched_at,
    )

    core_row = normalizer_fn(record, tenant_id, raw_row_id)
    insert_sql = _CORE_INSERTS[entity]

    # Coerce ISO-8601 string timestamps to datetime for asyncpg.
    for field in _TIMESTAMP_FIELDS.get(entity, ()):
        if field in core_row:
            core_row[field] = _parse_ts(core_row[field])

    # Coerce ad_spend_daily.date string to datetime.date for asyncpg.
    if entity == "ad_spend_daily" and isinstance(core_row.get("date"), str):
        from datetime import date as _date

        core_row["date"] = _date.fromisoformat(core_row["date"][:10])

    async with SessionLocal() as s:
        await s.execute(text(insert_sql), core_row)
        await s.commit()


async def _handle_shopify_webhook(job: dict[str, Any]) -> None:
    """Job kind: shopify_webhook. Webhook route already wrote the body to
    raw.shopify_webhook_inbox. Load it, run RTORiskFlagger if COD."""
    p = job["payload"]
    raw_row_id = int(p["raw_row_id"])
    tenant_id = str(job["tenant_id"])

    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "SELECT payload FROM raw.shopify_webhook_inbox WHERE row_id = :r AND tenant_id = :t"
            ),
            {"r": raw_row_id, "t": tenant_id},
        )
        row = result.first()
        if row is None:
            raise RuntimeError(
                f"webhook inbox row not found: row_id={raw_row_id} tenant={tenant_id}"
            )
        order_payload = dict(row.payload)

    if order_payload.get("gateway") != "Cash on Delivery":
        return  # RTO Flagger only cares about COD orders

    flagger = RTORiskFlagger()
    ctx = AgentContext(tenant_id=tenant_id, trigger_payload=order_payload)
    evidence = await flagger.gather(ctx)
    decision = flagger.decide(evidence)
    await flagger.propose(ctx, decision, evidence)


# ---------- top-level dispatch ----------

JOB_HANDLERS: dict[str, Any] = {
    "shopify_webhook": _handle_shopify_webhook,
    "connector_record": _handle_connector_record,
}


async def process_one() -> bool:
    """Pop one job and process it. Returns True if a job was processed,
    False if the queue was empty. Test entrypoint."""
    job = await dequeue("realtime")
    if job is None:
        return False
    handler = JOB_HANDLERS.get(job["kind"])
    if handler is None:
        log.warning("unknown job kind %s; marking complete", job["kind"])
        await complete("realtime", job["id"])
        return True
    try:
        await handler(job)
    except Exception as e:
        log.exception("job %s failed: %s", job["id"], e)
        await fail("realtime", job["id"], str(e))
        return True
    await complete("realtime", job["id"])
    return True


async def worker_loop(poll_interval_s: float = 1.0) -> None:
    """Run forever. Signal-handled (SIGINT / SIGTERM) for graceful shutdown."""
    stop_event = asyncio.Event()

    def _signal_handler(*_: Any) -> None:
        log.info("worker received shutdown signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows doesn't support add_signal_handler; skip if so.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    log.info("worker started; polling control.queue_realtime every %.1fs", poll_interval_s)
    while not stop_event.is_set():
        processed = await process_one()
        if not processed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
    log.info("worker stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(worker_loop())
