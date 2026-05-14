"""RTO Risk Flagger."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from packages.agents.base import (
    AgentContext,
    Decision,
    Evidence,
    RunLog,
    TriggerSpec,
    propose_run,
)
from packages.chat.tools import compute_metric
from packages.udm.xref import canonical_id
from packages.warehouse.db import SessionLocal

log = logging.getLogger(__name__)


WEIGHTS = {
    "pincode_rto_rate": 0.35,
    "customer_prior_rto_rate": 0.25,
    "sku_basket_rto_rate": 0.15,
    "cart_value_zscore": 0.10,
    "address_quality_score": 0.10,
    "time_of_day_risk": 0.05,
}

COLD_START_PRIOR = 0.15
COLD_START_PINCODE_THRESHOLD = 20

LOW_THRESHOLD = 0.25
MED_THRESHOLD = 0.45

SAVINGS_HIGH = 240.0
SAVINGS_MED = 80.0


@dataclass(frozen=True)
class RTOFeatures:
    pincode_rto_rate: float
    customer_prior_rto_rate: float
    sku_basket_rto_rate: float
    cart_value_zscore: float
    address_quality_score: float
    time_of_day_risk: float
    pincode_sample_size: int
    customer_orders_seen: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "pincode_rto_rate": self.pincode_rto_rate,
            "customer_prior_rto_rate": self.customer_prior_rto_rate,
            "sku_basket_rto_rate": self.sku_basket_rto_rate,
            "cart_value_zscore": self.cart_value_zscore,
            "address_quality_score": self.address_quality_score,
            "time_of_day_risk": self.time_of_day_risk,
            "pincode_sample_size": self.pincode_sample_size,
            "customer_orders_seen": self.customer_orders_seen,
        }


def _score(features: RTOFeatures) -> float:
    raw_inputs = (
        features.pincode_rto_rate,
        features.customer_prior_rto_rate,
        features.sku_basket_rto_rate,
        features.cart_value_zscore,
        features.address_quality_score,
        features.time_of_day_risk,
    )
    if any(v > 1.0 for v in raw_inputs):
        return 1.0

    customer_signal = (
        features.customer_prior_rto_rate if features.customer_orders_seen > 0 else COLD_START_PRIOR
    )
    s = (
        WEIGHTS["pincode_rto_rate"] * features.pincode_rto_rate
        + WEIGHTS["customer_prior_rto_rate"] * customer_signal
        + WEIGHTS["sku_basket_rto_rate"] * features.sku_basket_rto_rate
        + WEIGHTS["cart_value_zscore"] * features.cart_value_zscore
        + WEIGHTS["address_quality_score"] * features.address_quality_score
        + WEIGHTS["time_of_day_risk"] * features.time_of_day_risk
    )
    return max(0.0, min(1.0, s))


def _band(score: float) -> str:
    if score < LOW_THRESHOLD:
        return "LOW"
    if score < MED_THRESHOLD:
        return "MED"
    return "HIGH"


def _address_quality_score(address1: str | None, city: str | None) -> float:
    if not address1:
        return 1.0
    addr = address1.lower()
    if any(kw in addr for kw in ("pg ", "hostel", "resort", "lodge", "hotel ")):
        return 0.8
    if len(addr) < 15:
        return 0.6
    if not city:
        return 0.4
    return 0.1


def _time_of_day_risk(placed_at_iso: str | None) -> float:
    if not placed_at_iso:
        return 0.0
    try:
        ts = datetime.fromisoformat(placed_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    hour = ts.hour
    if hour >= 22 or hour < 4:
        return 1.0
    return 0.0


def _cart_value_zscore(cart_value: float, pincode_avg: float = 1500.0) -> float:
    if pincode_avg <= 0:
        return 0.0
    ratio = cart_value / pincode_avg
    return max(0.0, min(1.0, (ratio - 0.5) / 2.5))


class RTORiskFlagger:
    agent_id = "rto_risk_flagger"
    schedule = TriggerSpec(kind="webhook", topic="shopify.orders/create")

    async def gather(self, ctx: AgentContext) -> Evidence:
        order = ctx.trigger_payload
        pincode = (order.get("shipping_address") or {}).get("zip")
        cart_value = float(order.get("total_price") or 0)
        sku_list = [li.get("sku") for li in (order.get("line_items") or [])]

        (
            (pincode_rate, pincode_n, pincode_citations),
            (cust_rate, cust_n, cust_citations),
            (sku_rate, sku_citations),
        ) = await asyncio.gather(
            self._pincode_rto_rate(ctx.tenant_id, pincode),
            self._customer_prior_rto(ctx.tenant_id, order.get("customer", {}).get("id")),
            self._sku_rto_rate(ctx.tenant_id, sku_list),
        )
        cart_z = _cart_value_zscore(cart_value)
        addr_quality = _address_quality_score(
            (order.get("shipping_address") or {}).get("address1"),
            (order.get("shipping_address") or {}).get("city"),
        )
        tod_risk = _time_of_day_risk(order.get("created_at"))

        features = RTOFeatures(
            pincode_rto_rate=pincode_rate,
            customer_prior_rto_rate=cust_rate,
            sku_basket_rto_rate=sku_rate,
            cart_value_zscore=cart_z,
            address_quality_score=addr_quality,
            time_of_day_risk=tod_risk,
            pincode_sample_size=pincode_n,
            customer_orders_seen=cust_n,
        )
        return Evidence(
            features=features.as_dict(),
            citations=pincode_citations + cust_citations + sku_citations,
        )

    def decide(self, evidence: Evidence) -> Decision:
        f = RTOFeatures(
            pincode_rto_rate=evidence.features.get("pincode_rto_rate", 0.0),
            customer_prior_rto_rate=evidence.features.get("customer_prior_rto_rate", 0.0),
            sku_basket_rto_rate=evidence.features.get("sku_basket_rto_rate", 0.0),
            cart_value_zscore=evidence.features.get("cart_value_zscore", 0.0),
            address_quality_score=evidence.features.get("address_quality_score", 0.0),
            time_of_day_risk=evidence.features.get("time_of_day_risk", 0.0),
            pincode_sample_size=int(evidence.features.get("pincode_sample_size", 0)),
            customer_orders_seen=int(evidence.features.get("customer_orders_seen", 0)),
        )
        score = _score(f)
        band = _band(score)
        if band == "HIGH":
            action = "downgrade_to_prepaid"
            savings = SAVINGS_HIGH
        elif band == "MED":
            action = "send_whatsapp_confirm"
            savings = SAVINGS_MED
        else:
            action = "ship_as_is"
            savings = 0.0

        reasoning = (
            f"pincode RTO {f.pincode_rto_rate:.0%} (n={f.pincode_sample_size}); "
            f"customer prior RTO {f.customer_prior_rto_rate:.0%} "
            f"(n={f.customer_orders_seen}); "
            f"sku basket RTO {f.sku_basket_rto_rate:.0%}; "
            f"cart-z {f.cart_value_zscore:.2f}; "
            f"address {f.address_quality_score:.2f}; "
            f"tod {f.time_of_day_risk:.0f}; "
            f"score {score:.2f} → {band}"
        )
        confidence = "low" if f.pincode_sample_size < COLD_START_PINCODE_THRESHOLD else "high"

        return Decision(
            action_type=action,
            payload={
                "score": round(score, 4),
                "band": band,
                "confidence": confidence,
                "features": evidence.features,
            },
            score=score,
            band=band,
            reasoning=reasoning,
            expected_savings_inr=savings,
        )

    async def propose(self, ctx: AgentContext, decision: Decision, evidence: Evidence) -> RunLog:
        return await propose_run(self.agent_id, ctx, decision, evidence)

    async def _pincode_rto_rate(
        self, tenant_id: str, pincode: str | None
    ) -> tuple[float, int, list[dict]]:
        if not pincode:
            return 0.0, 0, []
        try:
            res = await compute_metric(
                tenant_id=tenant_id,
                metric_id="pincode_rto_rate_90d",
                dimensions=["pincode"],
                filters={"shipping_pincode": pincode},
            )
        except Exception as e:
            log.warning("pincode metric failed: %s", e)
            return 0.0, 0, []
        rows = res.get("rows") or []
        match = next((r for r in rows if str(r.get("pincode")) == pincode), None)
        if not match:
            return 0.0, 0, []
        rate = float(match.get("value") or 0)
        n = int(match.get("sample_size") or 0)
        cites = match.get("citations") or []
        return rate, n, cites[:3]

    async def _customer_prior_rto(
        self, tenant_id: str, customer_id: Any
    ) -> tuple[float, int, list[dict]]:
        if not customer_id:
            return 0.0, 0, []
        customer_canonical = canonical_id(tenant_id, "customer", "shopify", str(customer_id))
        async with SessionLocal() as s:
            result = await s.execute(
                text("""
                  SELECT
                    COUNT(*) AS n,
                    SUM(CASE WHEN s.is_rto THEN 1 ELSE 0 END)::numeric
                      / NULLIF(COUNT(*), 0) AS rate,
                    ARRAY_AGG(jsonb_build_object(
                      'source_system', s.source_system,
                      'source_id', s.source_id,
                      'url', s.source_record_url,
                      'raw_table', s.raw_table,
                      'raw_row_id', s.raw_row_id
                    )) AS citations
                  FROM core.shipment s
                  JOIN core."order" o
                    ON o.tenant_id = s.tenant_id
                    AND o.canonical_id = s.order_canonical_id
                  WHERE o.tenant_id = :t
                    AND o.customer_canonical_id = :cc
                """),
                {"t": tenant_id, "cc": customer_canonical},
            )
            row = result.first()
        if row is None or row.n == 0:
            return 0.0, 0, []
        rate = float(row.rate or 0)
        n = int(row.n)
        citations = list(row.citations or [])[:3]
        return rate, n, citations

    async def _sku_rto_rate(
        self, tenant_id: str, sku_list: list[str | None]
    ) -> tuple[float, list[dict]]:
        skus = [s for s in (sku_list or []) if s]
        if not skus:
            return 0.0, []
        async with SessionLocal() as s:
            result = await s.execute(
                text("""
                  SELECT
                    ol.sku,
                    COUNT(*) AS n,
                    SUM(CASE WHEN sh.is_rto THEN 1 ELSE 0 END)::numeric
                      / NULLIF(COUNT(*), 0) AS rate,
                    (ARRAY_AGG(jsonb_build_object(
                      'source_system', sh.source_system,
                      'source_id', sh.source_id,
                      'url', sh.source_record_url,
                      'raw_table', sh.raw_table,
                      'raw_row_id', sh.raw_row_id
                    )))[1:2] AS citations
                  FROM core.order_line ol
                  JOIN core."order" o
                    ON o.tenant_id = ol.tenant_id
                    AND o.canonical_id = ol.order_canonical_id
                  JOIN core.shipment sh
                    ON sh.tenant_id = o.tenant_id
                    AND sh.order_canonical_id = o.canonical_id
                  WHERE ol.tenant_id = :t
                    AND ol.sku = ANY(:skus)
                  GROUP BY ol.sku
                """),
                {"t": tenant_id, "skus": skus},
            )
            rows = list(result.mappings())
        if not rows:
            return 0.0, []
        valid = [r for r in rows if r["n"] and r["n"] > 0]
        if not valid:
            return 0.0, []
        mean_rate = sum(float(r["rate"] or 0) for r in valid) / len(valid)
        citations: list[dict] = []
        for r in valid[:3]:
            citations.extend(list(r["citations"] or []))
        return mean_rate, citations[:3]
