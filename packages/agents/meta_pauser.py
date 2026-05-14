"""Meta Campaign Pauser."""

from __future__ import annotations

from dataclasses import dataclass

from packages.agents.base import (
    AgentContext,
    Decision,
    Evidence,
    RunLog,
    TriggerSpec,
    propose_run,
)

PAUSE_ROAS_THRESHOLD = 0.7
PAUSE_MIN_SPEND = 5_000.0
REDUCE_ROAS_THRESHOLD = 1.0
REDUCE_MIN_SPEND = 15_000.0
LEARNING_PHASE_MIN_CONVERSIONS = 50


@dataclass(frozen=True)
class CampaignSnapshot:
    campaign_id: str
    name: str
    spend: float
    attributed_revenue: float
    rto_adjusted_revenue: float
    conversions: int
    learning_phase: bool

    @property
    def post_rto_roas(self) -> float:
        if self.spend <= 0:
            return 0.0
        return self.rto_adjusted_revenue / self.spend


def _decide_for_campaign(c: CampaignSnapshot) -> tuple[str, str, float, float]:
    if c.learning_phase or c.conversions < LEARNING_PHASE_MIN_CONVERSIONS:
        return ("skip_learning_phase", f"in learning phase ({c.conversions} conv) — skip", 0.0, 0.0)
    roas = c.post_rto_roas
    if roas < PAUSE_ROAS_THRESHOLD and c.spend > PAUSE_MIN_SPEND:
        return (
            "pause_campaign",
            f"post-RTO ROAS {roas:.2f} < {PAUSE_ROAS_THRESHOLD} with ₹{c.spend:.0f} spend — pause",
            c.spend,
            max(0.0, 1.0 - roas),
        )
    if roas < REDUCE_ROAS_THRESHOLD and c.spend > REDUCE_MIN_SPEND:
        return (
            "reduce_budget_50",
            f"post-RTO ROAS {roas:.2f} < {REDUCE_ROAS_THRESHOLD}"
            f" with ₹{c.spend:.0f} spend — halve budget",
            c.spend * 0.5,
            max(0.0, 1.0 - roas),
        )
    return ("keep", f"post-RTO ROAS {roas:.2f} acceptable", 0.0, max(0.0, 1.0 - roas))


class MetaPauser:
    agent_id = "meta_pauser"
    schedule = TriggerSpec(kind="cron", cron_expr="0 */6 * * *")

    async def gather(self, ctx: AgentContext) -> Evidence:
        campaigns = ctx.trigger_payload.get("campaigns") or []
        return Evidence(features={"campaigns": campaigns}, citations=[])

    def decide(self, evidence: Evidence) -> Decision:
        campaigns = [CampaignSnapshot(**c) for c in evidence.features.get("campaigns", [])]
        proposals = []
        total_savings = 0.0
        max_score = 0.0
        reasoning_lines = []
        for c in campaigns:
            action, reason, savings, score = _decide_for_campaign(c)
            if action != "keep" and action != "skip_learning_phase":
                proposals.append(
                    {
                        "campaign_id": c.campaign_id,
                        "name": c.name,
                        "action": action,
                        "reason": reason,
                        "expected_savings_inr": savings,
                    }
                )
                total_savings += savings
                max_score = max(max_score, score)
            reasoning_lines.append(f"{c.campaign_id}: {reason}")
        band = (
            "HIGH"
            if any(p["action"] == "pause_campaign" for p in proposals)
            else ("MED" if proposals else "LOW")
        )
        return Decision(
            action_type="batch_meta_actions",
            payload={"proposals": proposals},
            score=max_score,
            band=band,
            reasoning="\n".join(reasoning_lines) or "no campaigns evaluated",
            expected_savings_inr=total_savings,
        )

    async def propose(self, ctx: AgentContext, decision: Decision, evidence: Evidence) -> RunLog:
        return await propose_run(self.agent_id, ctx, decision, evidence)
