"""Production-style cron handlers for the batch agents.

The MetaPauser and PincodeCodBlocker were designed to receive pre-computed
batches in ctx.trigger_payload. This module is the SQL → trigger_payload
adapter — it queries core.* to assemble what each agent expects, then
invokes agent.gather / decide / propose.

In v1 these functions get called by a real scheduler (Celery beat, k8s
CronJob). For now scripts/run_demo.py calls them once after ingest drains.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from packages.agents.base import AgentContext, RunLog
from packages.agents.meta_pauser import MetaPauser
from packages.agents.pincode_cod_blocker import PincodeCodBlocker
from packages.warehouse.db import SessionLocal

log = logging.getLogger(__name__)


async def run_pincode_blocker_for_tenant(
    tenant_id: str,
    window_days: int = 90,
    min_orders: int = 20,
) -> RunLog:
    """Aggregate pincode stats from core, then run PincodeCodBlocker."""
    since = datetime.now(UTC) - timedelta(days=window_days)

    async with SessionLocal() as s:
        result = await s.execute(
            text("""
              SELECT
                o.shipping_pincode AS pincode,
                COUNT(*) AS sample_size,
                SUM(CASE WHEN s.is_rto THEN 1 ELSE 0 END)::numeric
                  / NULLIF(COUNT(*), 0) AS rto_rate,
                AVG(o.total) AS avg_cart_value
              FROM core."order" o
              JOIN core.shipment s
                ON s.tenant_id = o.tenant_id
                AND s.order_canonical_id = o.canonical_id
              WHERE o.tenant_id = :t
                AND o.placed_at >= :since
                AND o.shipping_pincode IS NOT NULL
              GROUP BY o.shipping_pincode
              HAVING COUNT(*) >= :min_orders
            """),
            {"t": tenant_id, "since": since, "min_orders": min_orders},
        )
        rows = list(result.mappings())

    pincode_stats = [
        {
            "pincode": str(r["pincode"]),
            "rto_rate": float(r["rto_rate"] or 0),
            "sample_size": int(r["sample_size"]),
            "avg_cart_value": float(r["avg_cart_value"] or 0),
        }
        for r in rows
    ]

    log.info("pincode_blocker: %d pincodes meet n>=%d gate", len(pincode_stats), min_orders)

    agent = PincodeCodBlocker()
    ctx = AgentContext(
        tenant_id=tenant_id,
        trigger_payload={
            "pincode_stats": pincode_stats,
            "citations": [
                {
                    "source_system": "shiprocket",
                    "source_id": f"pincode-aggregate-{tenant_id[:8]}",
                    "url": "internal://aggregate/pincode_rto",
                    "raw_table": "core.shipment",
                    "raw_row_id": 0,
                }
            ],
        },
    )
    evidence = await agent.gather(ctx)
    decision = agent.decide(evidence)
    return await agent.propose(ctx, decision, evidence)


async def run_meta_pauser_for_tenant(
    tenant_id: str,
    window_days: int = 14,
) -> RunLog:
    """Aggregate campaign performance from core, then run MetaPauser."""
    since_date = (datetime.now(UTC) - timedelta(days=window_days)).date()

    async with SessionLocal() as s:
        result = await s.execute(
            text("""
              WITH spend AS (
                SELECT
                  asd.campaign_canonical_id,
                  SUM(asd.spend) AS spend,
                  SUM(asd.conversions) AS conversions
                FROM core.ad_spend_daily asd
                WHERE asd.tenant_id = :t AND asd.date >= :since
                GROUP BY asd.campaign_canonical_id
              ),
              revenue AS (
                SELECT
                  c.canonical_id AS campaign_canonical_id,
                  SUM(o.total) AS attributed_revenue,
                  SUM(CASE WHEN COALESCE(s.is_rto, false) THEN 0 ELSE o.total END)
                    AS rto_adjusted_revenue
                FROM core.campaign c
                LEFT JOIN core."order" o
                  ON o.tenant_id = c.tenant_id
                  AND o.utm_campaign = c.name
                  AND o.placed_at >= :since
                LEFT JOIN core.shipment s
                  ON s.tenant_id = o.tenant_id
                  AND s.order_canonical_id = o.canonical_id
                WHERE c.tenant_id = :t
                GROUP BY c.canonical_id
              )
              SELECT
                c.canonical_id,
                c.name,
                c.status,
                COALESCE(spend.spend, 0) AS spend,
                COALESCE(spend.conversions, 0) AS conversions,
                COALESCE(revenue.attributed_revenue, 0) AS attributed_revenue,
                COALESCE(revenue.rto_adjusted_revenue, 0) AS rto_adjusted_revenue
              FROM core.campaign c
              LEFT JOIN spend ON spend.campaign_canonical_id = c.canonical_id
              LEFT JOIN revenue ON revenue.campaign_canonical_id = c.canonical_id
              WHERE c.tenant_id = :t
            """),
            {"t": tenant_id, "since": since_date},
        )
        rows = list(result.mappings())

    campaigns = [
        {
            "campaign_id": str(r["canonical_id"]),
            "name": r["name"] or "",
            "spend": float(r["spend"] or 0),
            "attributed_revenue": float(r["attributed_revenue"] or 0),
            "rto_adjusted_revenue": float(r["rto_adjusted_revenue"] or 0),
            "conversions": int(r["conversions"] or 0),
            # Treat any campaign with <50 conversions in the window as still
            # in learning phase (per MetaPauser's LEARNING_PHASE_MIN_CONVERSIONS).
            "learning_phase": int(r["conversions"] or 0) < 50,
        }
        for r in rows
    ]

    log.info("meta_pauser: %d campaigns over %dd window", len(campaigns), window_days)

    agent = MetaPauser()
    ctx = AgentContext(
        tenant_id=tenant_id,
        trigger_payload={"campaigns": campaigns},
    )
    evidence = await agent.gather(ctx)
    decision = agent.decide(evidence)
    return await agent.propose(ctx, decision, evidence)
