"""Shared abstraction for autonomous agents.

Mirrors the Connector pattern: one Protocol + three impls (RTO Risk
Flagger, Meta Pauser, Pincode COD Blocker). Forcing all 3 agents through
the same shape makes every agent declare trigger / evidence / decision /
proposed_action / reasoning / expected_savings — exactly what the brief
asks for ("trigger, data, decision, action, failure modes all explicit").

Agents share the chat layer's tool surface (compute_metric, search_rows,
propose_write) — so an agent's reasoning is automatically citation-grounded
by the same provenance contract the chat uses. No second grounding system.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import text

from packages.warehouse.db import SessionLocal


@dataclass(frozen=True)
class TriggerSpec:
    """Either webhook or cron — every agent declares one."""

    kind: str  # "webhook" | "cron"
    topic: str | None = None  # for webhooks (e.g. "shopify.orders/create")
    cron_expr: str | None = None  # for cron triggers


@dataclass
class Evidence:
    """Inputs gathered from compute_metric / search_rows.

    `citations` carries the provenance from each underlying tool call so
    the run log shows traceable reasoning.
    """

    features: dict[str, Any]
    citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Decision:
    """Pure function output of decide(). Never persists external state."""

    action_type: str
    payload: dict[str, Any]
    score: float
    band: str
    reasoning: str
    expected_savings_inr: float


@dataclass
class AgentContext:
    """What an agent needs to gather evidence."""

    tenant_id: str
    trigger_payload: dict[str, Any]
    triggered_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class RunLog:
    """One row of core.agent_runs."""

    run_id: str
    tenant_id: str
    agent_id: str
    triggered_at: datetime
    trigger: dict[str, Any]
    evidence: dict[str, Any]
    decision: dict[str, Any]
    proposed_action: dict[str, Any] | None
    reasoning: str
    score: float | None
    band: str | None
    expected_savings_inr: float | None
    cited_provenance: list[dict[str, Any]]


class Agent(Protocol):
    agent_id: str
    schedule: TriggerSpec

    async def gather(self, ctx: AgentContext) -> Evidence: ...

    def decide(self, evidence: Evidence) -> Decision: ...

    async def propose(
        self, ctx: AgentContext, decision: Decision, evidence: Evidence
    ) -> RunLog: ...


# ---------- Persistence ----------


async def write_run_log(log: RunLog) -> None:
    """Append RunLog to core.agent_runs."""
    async with SessionLocal() as s:
        await s.execute(
            text("""
              INSERT INTO core.agent_runs (
                run_id, tenant_id, agent_id, triggered_at,
                trigger, evidence, decision,
                proposed_action, reasoning, score, band,
                expected_savings_inr, cited_provenance
              ) VALUES (
                :run_id, :tenant_id, :agent_id, :triggered_at,
                CAST(:trigger AS jsonb), CAST(:evidence AS jsonb), CAST(:decision AS jsonb),
                CAST(:proposed_action AS jsonb), :reasoning, :score, :band,
                :expected_savings_inr, CAST(:cited_provenance AS jsonb)
              )
            """),
            {
                "run_id": log.run_id,
                "tenant_id": log.tenant_id,
                "agent_id": log.agent_id,
                "triggered_at": log.triggered_at,
                "trigger": json.dumps(log.trigger, default=_json_default),
                "evidence": json.dumps(log.evidence, default=_json_default),
                "decision": json.dumps(log.decision, default=_json_default),
                "proposed_action": (
                    json.dumps(log.proposed_action, default=_json_default)
                    if log.proposed_action is not None
                    else None
                ),
                "reasoning": log.reasoning,
                "score": log.score,
                "band": log.band,
                "expected_savings_inr": log.expected_savings_inr,
                "cited_provenance": json.dumps(log.cited_provenance, default=_json_default),
            },
        )
        await s.commit()


def _json_default(o: Any) -> Any:
    """Coerce dates/decimals for jsonb storage."""
    from decimal import Decimal

    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def make_run_log(
    agent_id: str,
    ctx: AgentContext,
    evidence: Evidence,
    decision: Decision,
) -> RunLog:
    """Helper: build a RunLog from the standard pieces."""
    return RunLog(
        run_id=str(uuid.uuid4()),
        tenant_id=ctx.tenant_id,
        agent_id=agent_id,
        triggered_at=ctx.triggered_at,
        trigger=dict(ctx.trigger_payload),
        evidence={"features": evidence.features},
        decision=asdict(decision),
        proposed_action={
            "action_type": decision.action_type,
            "payload": decision.payload,
            "dry_run": True,
        },
        reasoning=decision.reasoning,
        score=decision.score,
        band=decision.band,
        expected_savings_inr=decision.expected_savings_inr,
        cited_provenance=evidence.citations,
    )
