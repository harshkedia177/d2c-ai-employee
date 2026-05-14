import uuid

import pytest
from sqlalchemy import text

from packages.agents.base import (
    AgentContext,
    Decision,
    Evidence,
    RunLog,
    TriggerSpec,
    make_run_log,
    write_run_log,
)
from packages.warehouse.db import SessionLocal


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM core.agent_runs WHERE agent_id = 'test_agent'"))
        await s.commit()


def test_trigger_spec_supports_webhook_and_cron():
    a = TriggerSpec(kind="webhook", topic="shopify.orders/create")
    b = TriggerSpec(kind="cron", cron_expr="0 */6 * * *")
    assert a.kind == "webhook"
    assert b.kind == "cron"


def test_make_run_log_carries_all_required_fields():
    ctx = AgentContext(tenant_id=str(uuid.uuid4()), trigger_payload={"order_id": "123"})
    ev = Evidence(
        features={"pincode_rto_rate": 0.34, "cart_value": 2400},
        citations=[{"url": "https://example.com/x", "raw_row_id": 1}],
    )
    d = Decision(
        action_type="downgrade_to_prepaid",
        payload={"order_id": "123", "reason": "high RTO risk"},
        score=0.61,
        band="HIGH",
        reasoning="pincode 34% rto, customer 1/2",
        expected_savings_inr=240.0,
    )
    log = make_run_log("rto_risk_flagger", ctx, ev, d)
    assert isinstance(log, RunLog)
    assert log.agent_id == "rto_risk_flagger"
    assert log.score == 0.61
    assert log.band == "HIGH"
    assert log.proposed_action["dry_run"] is True
    assert log.cited_provenance[0]["raw_row_id"] == 1
    assert log.run_id and log.tenant_id and log.triggered_at


@pytest.mark.asyncio
async def test_write_run_log_persists_to_agent_runs():
    tid = str(uuid.uuid4())
    ctx = AgentContext(tenant_id=tid, trigger_payload={"x": 1})
    ev = Evidence(features={"f": 1.0}, citations=[])
    d = Decision(
        action_type="propose",
        payload={},
        score=0.5,
        band="MED",
        reasoning="r",
        expected_savings_inr=80.0,
    )
    log = make_run_log("test_agent", ctx, ev, d)
    await write_run_log(log)

    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT agent_id, score, band, expected_savings_inr "
                "FROM core.agent_runs WHERE run_id = :rid"
            ),
            {"rid": log.run_id},
        )
        row = r.first()
        assert row is not None
        assert row.agent_id == "test_agent"
        assert float(row.score) == 0.5
        assert row.band == "MED"
        assert float(row.expected_savings_inr) == 80.0


@pytest.mark.asyncio
async def test_run_log_jsonb_columns_round_trip():
    tid = str(uuid.uuid4())
    ctx = AgentContext(
        tenant_id=tid,
        trigger_payload={"shopify_order_id": "shopify-m000-000123", "pincode": "110084"},
    )
    ev = Evidence(
        features={"pincode_rto_rate": 0.34, "cart_value": 2400.0},
        citations=[
            {"url": "https://app.shiprocket.in/orders/sr-1", "raw_row_id": 5},
            {"url": "https://app.shiprocket.in/orders/sr-2", "raw_row_id": 6},
        ],
    )
    d = Decision(
        action_type="downgrade_to_prepaid",
        payload={"order_id": "shopify-m000-000123"},
        score=0.61,
        band="HIGH",
        reasoning="r",
        expected_savings_inr=240.0,
    )
    log = make_run_log("test_agent", ctx, ev, d)
    await write_run_log(log)

    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT trigger, evidence, decision, cited_provenance "
                "FROM core.agent_runs WHERE run_id = :rid"
            ),
            {"rid": log.run_id},
        )
        row = r.first()
        assert row is not None
        assert row.trigger["pincode"] == "110084"
        assert row.evidence["features"]["pincode_rto_rate"] == 0.34
        assert row.decision["action_type"] == "downgrade_to_prepaid"
        assert len(row.cited_provenance) == 2
