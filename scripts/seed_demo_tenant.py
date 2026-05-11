"""Seed a demo tenant + a handful of agent_runs for the UI to render.

Idempotent — re-running won't duplicate rows (ON CONFLICT DO NOTHING / fixed
run_ids).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from packages.warehouse.db import SessionLocal

DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEMO_SLUG = "demo"


# Fixed run UUIDs so re-seeding is idempotent.
RUN_RTO_HIGH_1 = "11111111-1111-1111-1111-000000000001"
RUN_RTO_HIGH_2 = "11111111-1111-1111-1111-000000000002"
RUN_RTO_MED_1 = "11111111-1111-1111-1111-000000000003"
RUN_META_PAUSER = "11111111-1111-1111-1111-000000000004"
RUN_PINCODE_BLOCKER = "11111111-1111-1111-1111-000000000005"


def _cite(src_id: str, system: str = "shiprocket", table: str = "shipments") -> dict:
    return {
        "source_system": system,
        "source_id": src_id,
        "url": f"http://localhost:9000/{system}/{table}/{src_id}",
        "raw_table": f"raw.{system}_{table}",
        "raw_row_id": f"row_{src_id}",
    }


async def seed() -> None:
    now = datetime.now(UTC)

    async with SessionLocal() as s:
        await s.execute(
            text(
                "INSERT INTO control.tenant (tenant_id, slug) "
                "VALUES (:t, :slug) ON CONFLICT (tenant_id) DO NOTHING"
            ),
            {"t": DEMO_TENANT_ID, "slug": DEMO_SLUG},
        )

        runs = [
            {
                "run_id": RUN_RTO_HIGH_1,
                "agent_id": "rto_risk_flagger",
                "triggered_at": now - timedelta(hours=2),
                "trigger": {
                    "event": "order.created",
                    "order_id": "shopify-m000-001247",
                    "gateway": "COD",
                    "amount_inr": 2400,
                    "pincode": "110084",
                },
                "evidence": {
                    "features": {
                        "pincode_rto_rate": 0.34,
                        "customer_prior_rto": 0.50,
                        "sku_basket_rto": 0.18,
                        "time_of_day_score": 0.12,
                        "amount_band_score": 0.08,
                        "address_completeness": 0.05,
                        "first_time_buyer": 0.03,
                        "cart_value_band": 0.02,
                    },
                    "pincode_sample_n": 87,
                    "customer_sample_n": 2,
                },
                "decision": {"band": "HIGH", "score": 0.61},
                "proposed_action": {
                    "type": "downgrade_to_prepaid",
                    "offer_pct": 5,
                    "channel": "checkout_banner",
                    "dry_run": True,
                },
                "reasoning": (
                    "Pincode RTO 34% over 87 orders. Customer prior RTO 1 of 2. "
                    "Late-night order. Score 0.61 → HIGH."
                ),
                "score": 0.61,
                "band": "HIGH",
                "expected_savings_inr": 240,
                "cited_provenance": [
                    _cite("sr-shp-004521"),
                    _cite("sr-shp-007720"),
                    _cite("sr-shp-002103"),
                    _cite("sr-shp-008814"),
                    _cite("sr-shp-009127"),
                ],
            },
            {
                "run_id": RUN_RTO_HIGH_2,
                "agent_id": "rto_risk_flagger",
                "triggered_at": now - timedelta(hours=4),
                "trigger": {
                    "event": "order.created",
                    "order_id": "shopify-m000-001249",
                    "gateway": "COD",
                    "amount_inr": 4150,
                    "pincode": "744301",
                },
                "evidence": {
                    "features": {
                        "pincode_rto_rate": 0.42,
                        "customer_prior_rto": 0.0,
                        "sku_basket_rto": 0.22,
                        "time_of_day_score": 0.04,
                        "amount_band_score": 0.18,
                        "address_completeness": 0.10,
                        "first_time_buyer": 0.10,
                        "cart_value_band": 0.05,
                    },
                    "pincode_sample_n": 53,
                    "customer_sample_n": 0,
                },
                "decision": {"band": "HIGH", "score": 0.58},
                "proposed_action": {
                    "type": "require_otp_confirmation",
                    "channel": "sms",
                    "dry_run": True,
                },
                "reasoning": (
                    "Remote pincode 744301 — RTO 42% over 53 orders. First-time buyer, "
                    "high cart value. Score 0.58 → HIGH."
                ),
                "score": 0.58,
                "band": "HIGH",
                "expected_savings_inr": 415,
                "cited_provenance": [
                    _cite("sr-shp-004102"),
                    _cite("sr-shp-004221"),
                    _cite("sr-shp-005550"),
                ],
            },
            {
                "run_id": RUN_RTO_MED_1,
                "agent_id": "rto_risk_flagger",
                "triggered_at": now - timedelta(hours=8),
                "trigger": {
                    "event": "order.created",
                    "order_id": "shopify-m000-001251",
                    "gateway": "COD",
                    "amount_inr": 1190,
                    "pincode": "560078",
                },
                "evidence": {
                    "features": {
                        "pincode_rto_rate": 0.18,
                        "customer_prior_rto": 0.0,
                        "sku_basket_rto": 0.12,
                        "time_of_day_score": 0.02,
                        "amount_band_score": 0.03,
                        "address_completeness": 0.01,
                        "first_time_buyer": 0.05,
                        "cart_value_band": 0.02,
                    },
                    "pincode_sample_n": 312,
                    "customer_sample_n": 4,
                },
                "decision": {"band": "MED", "score": 0.34},
                "proposed_action": {
                    "type": "soft_nudge_prepaid",
                    "offer_pct": 3,
                    "dry_run": True,
                },
                "reasoning": (
                    "Pincode 560078 baseline RTO 18% over 312 orders. Returning customer, "
                    "no prior RTOs. Score 0.34 → MED."
                ),
                "score": 0.34,
                "band": "MED",
                "expected_savings_inr": 119,
                "cited_provenance": [
                    _cite("sr-shp-001102"),
                    _cite("sr-shp-001440"),
                ],
            },
            {
                "run_id": RUN_META_PAUSER,
                "agent_id": "meta_campaign_pauser",
                "triggered_at": now - timedelta(hours=6),
                "trigger": {
                    "event": "cron.6h",
                    "window_days": 14,
                    "min_spend_inr": 5000,
                },
                "evidence": {
                    "candidates": [
                        {
                            "campaign_id": "meta-cmp-aa01",
                            "name": "Hero Tee — Lookalike 1%",
                            "spend_inr": 18400,
                            "raw_roas": 1.6,
                            "post_rto_roas": 0.72,
                            "rto_rate": 0.31,
                            "learning_phase": False,
                        },
                        {
                            "campaign_id": "meta-cmp-aa02",
                            "name": "Winter Bundle — Broad",
                            "spend_inr": 12100,
                            "raw_roas": 1.4,
                            "post_rto_roas": 0.81,
                            "rto_rate": 0.28,
                            "learning_phase": False,
                        },
                    ]
                },
                "decision": {"band": "HIGH", "proposals": 2},
                "proposed_action": {
                    "type": "pause_campaigns",
                    "campaign_ids": ["meta-cmp-aa01", "meta-cmp-aa02"],
                    "dry_run": True,
                },
                "reasoning": (
                    "2 campaigns flagged with post-RTO ROAS below 1.0 over the 14-day "
                    "window. Combined wasted spend approximately ₹20,000."
                ),
                "score": None,
                "band": "HIGH",
                "expected_savings_inr": 20000,
                "cited_provenance": [
                    _cite("meta-cmp-aa01", system="meta", table="insights"),
                    _cite("meta-cmp-aa02", system="meta", table="insights"),
                    _cite("sr-shp-007001"),
                    _cite("sr-shp-007002"),
                ],
            },
            {
                "run_id": RUN_PINCODE_BLOCKER,
                "agent_id": "pincode_cod_blocker",
                "triggered_at": now - timedelta(hours=12),
                "trigger": {
                    "event": "cron.daily",
                    "window_days": 90,
                    "min_orders": 30,
                },
                "evidence": {
                    "candidates": [
                        {
                            "pincode": "110084",
                            "rto_rate": 0.34,
                            "orders": 87,
                            "expected_loss_inr": 11800,
                        },
                        {
                            "pincode": "744301",
                            "rto_rate": 0.42,
                            "orders": 53,
                            "expected_loss_inr": 8200,
                        },
                        {
                            "pincode": "190001",
                            "rto_rate": 0.39,
                            "orders": 41,
                            "expected_loss_inr": 6450,
                        },
                    ]
                },
                "decision": {"band": "HIGH", "proposals": 3},
                "proposed_action": {
                    "type": "block_cod_pincodes",
                    "pincodes": ["110084", "744301", "190001"],
                    "dry_run": True,
                },
                "reasoning": (
                    "Three pincodes in the top-20 by expected COD loss this quarter. "
                    "Proposing prepaid-only checkout for these."
                ),
                "score": None,
                "band": "HIGH",
                "expected_savings_inr": 26450,
                "cited_provenance": [
                    _cite("sr-shp-004102"),
                    _cite("sr-shp-004221"),
                    _cite("sr-shp-002103"),
                    _cite("sr-shp-009127"),
                ],
            },
        ]

        for r in runs:
            await s.execute(
                text(
                    """
                    INSERT INTO core.agent_runs (
                      run_id, tenant_id, agent_id, triggered_at,
                      trigger, evidence, decision, proposed_action,
                      reasoning, score, band, expected_savings_inr,
                      cited_provenance
                    ) VALUES (
                      :run_id, :tenant_id, :agent_id, :triggered_at,
                      CAST(:trigger AS jsonb), CAST(:evidence AS jsonb),
                      CAST(:decision AS jsonb), CAST(:proposed_action AS jsonb),
                      :reasoning, :score, :band, :expected_savings_inr,
                      CAST(:cited_provenance AS jsonb)
                    )
                    ON CONFLICT (run_id, tenant_id) DO NOTHING
                    """
                ),
                {
                    "run_id": r["run_id"],
                    "tenant_id": DEMO_TENANT_ID,
                    "agent_id": r["agent_id"],
                    "triggered_at": r["triggered_at"],
                    "trigger": json.dumps(r["trigger"]),
                    "evidence": json.dumps(r["evidence"]),
                    "decision": json.dumps(r["decision"]),
                    "proposed_action": json.dumps(r["proposed_action"]),
                    "reasoning": r["reasoning"],
                    "score": r["score"],
                    "band": r["band"],
                    "expected_savings_inr": r["expected_savings_inr"],
                    "cited_provenance": json.dumps(r["cited_provenance"]),
                },
            )

        await s.commit()
        print(f"Seeded tenant {DEMO_SLUG} ({DEMO_TENANT_ID}) with {len(runs)} agent_runs.")


if __name__ == "__main__":
    asyncio.run(seed())
