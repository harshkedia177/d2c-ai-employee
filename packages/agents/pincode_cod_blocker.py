"""Pincode COD Block Recommender."""

from __future__ import annotations

from dataclasses import dataclass

from packages.agents.base import (
    AgentContext,
    Decision,
    Evidence,
    RunLog,
    TriggerSpec,
    make_run_log,
    write_run_log,
)

MIN_SAMPLE_SIZE = 20
DEFAULT_MARGIN_PCT = 0.30
DEFAULT_FREIGHT_INR = 240.0
TOP_N = 20


@dataclass(frozen=True)
class PincodeStat:
    pincode: str
    rto_rate: float
    sample_size: int
    avg_cart_value: float


def _expected_loss_per_order(p: PincodeStat, margin_pct: float) -> float:
    loss_if_rto = p.avg_cart_value * (1 - margin_pct) + DEFAULT_FREIGHT_INR
    return p.rto_rate * loss_if_rto


def _avg_margin_per_order(p: PincodeStat, margin_pct: float) -> float:
    return p.avg_cart_value * margin_pct


def _should_block(p: PincodeStat, margin_pct: float) -> bool:
    if p.sample_size < MIN_SAMPLE_SIZE:
        return False
    return _expected_loss_per_order(p, margin_pct) > 0.5 * _avg_margin_per_order(p, margin_pct)


class PincodeCodBlocker:
    agent_id = "pincode_cod_blocker"
    schedule = TriggerSpec(kind="cron", cron_expr="0 3 * * *")

    async def gather(self, ctx: AgentContext) -> Evidence:
        pincodes = ctx.trigger_payload.get("pincode_stats") or []
        return Evidence(
            features={"pincode_stats": pincodes},
            citations=ctx.trigger_payload.get("citations", []),
        )

    def decide(self, evidence: Evidence) -> Decision:
        margin_pct = float(evidence.features.get("margin_pct", DEFAULT_MARGIN_PCT))
        stats = [PincodeStat(**s) for s in evidence.features.get("pincode_stats", [])]
        candidates = [s for s in stats if _should_block(s, margin_pct)]
        candidates.sort(
            key=lambda p: _expected_loss_per_order(p, margin_pct),
            reverse=True,
        )
        top = candidates[:TOP_N]
        proposals = [
            {
                "pincode": p.pincode,
                "rto_rate": p.rto_rate,
                "sample_size": p.sample_size,
                "avg_cart_value": p.avg_cart_value,
                "expected_loss_per_order_inr": round(_expected_loss_per_order(p, margin_pct), 2),
                "action": "block_cod_pincode",
            }
            for p in top
        ]
        total_savings = sum(
            p["expected_loss_per_order_inr"] * stats_for_pincode.sample_size
            for p, stats_for_pincode in zip(proposals, top, strict=True)
        )
        band = "HIGH" if proposals else "LOW"
        reasoning = (
            f"Evaluated {len(stats)} pincodes; "
            f"{len(candidates)} above the block threshold; "
            f"top {len(top)} returned. "
            f"Total expected 90d savings: ₹{total_savings:,.0f}."
        )
        return Decision(
            action_type="block_cod_pincodes",
            payload={"proposals": proposals},
            score=top[0].rto_rate if top else 0.0,
            band=band,
            reasoning=reasoning,
            expected_savings_inr=total_savings,
        )

    async def propose(self, ctx: AgentContext, decision: Decision, evidence: Evidence) -> RunLog:
        log_entry = make_run_log(
            agent_id=self.agent_id,
            ctx=ctx,
            evidence=evidence,
            decision=decision,
        )
        await write_run_log(log_entry)
        return log_entry
