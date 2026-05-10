"""RTO Risk Flagger — the hero agent.

Trigger: Shopify orders/create webhook for COD orders.
Decision: weighted rule-stack score → 3 bands (LOW/MED/HIGH).
Action: proposes (never executes) a band-appropriate friction step.

Why a transparent rule-stack instead of an ML model: a Mumbai D2C founder
needs to argue with the score ("pincode 110084 has 34% RTO over 87 orders,
customer has 1/2 prior RTOs") before they trust it. A black-box XGBoost
output is unactionable at v0. Marginal accuracy < explainability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from packages.agents.base import (
    AgentContext,
    Decision,
    Evidence,
    RunLog,
    TriggerSpec,
    make_run_log,
    write_run_log,
)
from packages.chat.tools import compute_metric

log = logging.getLogger(__name__)


# Feature weights (sum to 1.0). Plain numbers so a founder can edit them.
WEIGHTS = {
    "pincode_rto_rate": 0.35,
    "customer_prior_rto_rate": 0.25,
    "sku_basket_rto_rate": 0.15,
    "cart_value_zscore": 0.10,
    "address_quality_score": 0.10,
    "time_of_day_risk": 0.05,
}

COLD_START_PRIOR = 0.15  # used when customer has no prior orders
COLD_START_PINCODE_THRESHOLD = 20  # below this, fall back to district rate

LOW_THRESHOLD = 0.25
MED_THRESHOLD = 0.45

# Expected savings per band (₹ per order avoided RTO)
SAVINGS_HIGH = 240.0
SAVINGS_MED = 80.0


@dataclass(frozen=True)
class RTOFeatures:
    """Snapshot of the 6 features used by the agent."""

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
    """Linear weighted sum, clamped to [0, 1].

    Defensive saturation: if any input feature exceeds the unit interval
    (e.g., a buggy upstream returns 2.0 for an RTO rate), clamp the whole
    score to 1.0 so a downstream consumer never silently underweights a
    blown-out signal.
    """
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
    """Crude address-quality heuristic. 0.0 = clean, 1.0 = suspicious.

    Catches PG/hostel/resort hits, very short addresses, and missing city.
    """
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
    """Late-night impulse window (10pm–4am IST) gets 1.0; otherwise 0.0."""
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
    """Toy z-score: how much above/below the pincode mean (₹1,500 default).

    Returns clamped [0, 1] — risky-high carts (>3× mean) score 1.0;
    typical carts score ~0.3.
    """
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

        # 1. Pincode RTO rate
        pincode_rate, pincode_n, pincode_citations = await self._pincode_rto_rate(
            ctx.tenant_id, pincode
        )
        # 2. Customer prior RTO rate
        cust_rate, cust_n, cust_citations = await self._customer_prior_rto(
            ctx.tenant_id, order.get("customer", {}).get("id")
        )
        # 3. SKU RTO rate
        sku_rate, sku_citations = await self._sku_rto_rate(ctx.tenant_id, sku_list)
        # 4-6: cheap heuristics, no DB calls
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

    async def propose(
        self,
        ctx: AgentContext,
        decision: Decision,
        evidence: Evidence,
    ) -> RunLog:
        log_entry = make_run_log(
            agent_id=self.agent_id,
            ctx=ctx,
            evidence=evidence,
            decision=decision,
        )
        await write_run_log(log_entry)
        return log_entry

    # ---------- Helpers ----------

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
        # v0: simple — count prior orders this customer has, no RTO yet
        # because mock_saas seed doesn't link customer_id → shipments cleanly.
        # Use placeholder: 0 prior RTO, 0 orders seen → cold start.
        if not customer_id:
            return 0.0, 0, []
        return 0.0, 0, []

    async def _sku_rto_rate(
        self, tenant_id: str, sku_list: list[str | None]
    ) -> tuple[float, list[dict]]:
        # v0: stub — 0 rate, 0 citations. The signal lives in pincode_rto_rate.
        return 0.0, []
