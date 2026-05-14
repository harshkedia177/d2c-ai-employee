"""Realtime queue worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import date as _date
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
    customer_from_shopify,
    order_from_shopify,
    order_line_from_shopify,
    product_from_shopify,
    refund_from_shopify,
)
from packages.warehouse.db import SessionLocal

log = logging.getLogger(__name__)

NORMALIZER_DISPATCH: dict[tuple[str, str], tuple[Any, str]] = {
    ("shopify", "orders"): (order_from_shopify, "order"),
    ("shopify", "line_items"): (order_line_from_shopify, "order_line"),
    ("shopify", "customers"): (customer_from_shopify, "customer"),
    ("shopify", "products"): (product_from_shopify, "product"),
    ("shopify", "refunds"): (refund_from_shopify, "refund"),
    ("shiprocket", "shipments"): (shipment_from_shiprocket, "shipment"),
    ("meta_ads", "campaigns"): (campaign_from_meta, "campaign"),
    ("meta_ads", "ad_insights"): (ad_spend_daily_from_meta, "ad_spend_daily"),
}

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
    "customer": """
      INSERT INTO core.customer (
        tenant_id, canonical_id, email_hash, phone_hash, country, created_at,
        source_system, source_id, source_record_url,
        raw_table, raw_row_id, raw_payload_hash,
        fetched_at, ingested_at, connector_version
      ) VALUES (
        :tenant_id, :canonical_id, :email_hash, :phone_hash, :country, :created_at,
        :source_system, :source_id, :source_record_url,
        :raw_table, :raw_row_id, :raw_payload_hash,
        :fetched_at, :ingested_at, :connector_version
      ) ON CONFLICT (tenant_id, canonical_id, source_system) DO NOTHING
    """,
    "product": """
      INSERT INTO core.product (
        tenant_id, canonical_id, sku, title, price, currency, cost_per_item, vendor,
        source_system, source_id, source_record_url,
        raw_table, raw_row_id, raw_payload_hash,
        fetched_at, ingested_at, connector_version
      ) VALUES (
        :tenant_id, :canonical_id, :sku, :title, :price, :currency, :cost_per_item, :vendor,
        :source_system, :source_id, :source_record_url,
        :raw_table, :raw_row_id, :raw_payload_hash,
        :fetched_at, :ingested_at, :connector_version
      ) ON CONFLICT (tenant_id, canonical_id, source_system) DO NOTHING
    """,
    "refund": """
      INSERT INTO core.refund (
        tenant_id, canonical_id, order_canonical_id, amount, reason, refunded_at,
        source_system, source_id, source_record_url,
        raw_table, raw_row_id, raw_payload_hash,
        fetched_at, ingested_at, connector_version
      ) VALUES (
        :tenant_id, :canonical_id, :order_canonical_id, :amount, :reason, :refunded_at,
        :source_system, :source_id, :source_record_url,
        :raw_table, :raw_row_id, :raw_payload_hash,
        :fetched_at, :ingested_at, :connector_version
      ) ON CONFLICT (tenant_id, canonical_id) DO NOTHING
    """,
}


_TIMESTAMP_FIELDS: dict[str, tuple[str, ...]] = {
    "order": ("placed_at",),
    "order_line": (),
    "shipment": ("shipped_at", "delivered_at", "rto_at"),
    "campaign": (),
    "ad_spend_daily": (),
    "customer": ("created_at",),
    "product": (),
    "refund": ("refunded_at",),
}


def _parse_ts(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


async def _handle_connector_record(job: dict[str, Any]) -> None:
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

    for field in _TIMESTAMP_FIELDS.get(entity, ()):
        if field in core_row:
            core_row[field] = _parse_ts(core_row[field])

    if entity == "ad_spend_daily" and isinstance(core_row.get("date"), str):
        core_row["date"] = _date.fromisoformat(core_row["date"][:10])

    async with SessionLocal() as s:
        await s.execute(text(insert_sql), core_row)
        await s.commit()


async def _handle_shopify_webhook(job: dict[str, Any]) -> None:
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
        return

    flagger = RTORiskFlagger()
    ctx = AgentContext(tenant_id=tenant_id, trigger_payload=order_payload)
    evidence = await flagger.gather(ctx)
    decision = flagger.decide(evidence)
    await flagger.propose(ctx, decision, evidence)


JOB_HANDLERS: dict[str, Any] = {
    "shopify_webhook": _handle_shopify_webhook,
    "connector_record": _handle_connector_record,
}


async def process_one() -> bool:
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
    stop_event = asyncio.Event()

    def _signal_handler(*_: Any) -> None:
        log.info("worker received shutdown signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
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
