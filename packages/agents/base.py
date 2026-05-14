"""Shared abstraction for autonomous agents."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import text

from packages.warehouse.db import SessionLocal


@dataclass(frozen=True)
class TriggerSpec:
    kind: str
    topic: str | None = None
    cron_expr: str | None = None


@dataclass
class Evidence:
    features: dict[str, Any]
    citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Decision:
    action_type: str
    payload: dict[str, Any]
    score: float
    band: str
    reasoning: str
    expected_savings_inr: float


@dataclass
class AgentContext:
    tenant_id: str
    trigger_payload: dict[str, Any]
    triggered_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class RunLog:
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


async def write_run_log(log: RunLog) -> None:
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
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


async def propose_run(
    agent_id: str,
    ctx: AgentContext,
    decision: Decision,
    evidence: Evidence,
) -> RunLog:
    log_entry = make_run_log(agent_id=agent_id, ctx=ctx, evidence=evidence, decision=decision)
    await write_run_log(log_entry)
    return log_entry


def make_run_log(
    agent_id: str,
    ctx: AgentContext,
    evidence: Evidence,
    decision: Decision,
) -> RunLog:
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
