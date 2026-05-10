import uuid

import pytest
from sqlalchemy import text

from packages.agents.base import AgentContext, Evidence
from packages.agents.meta_pauser import (
    CampaignSnapshot,
    MetaPauser,
    _decide_for_campaign,
)
from packages.warehouse.db import SessionLocal


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with SessionLocal() as s:
        await s.execute(
            text(
                "DELETE FROM core.agent_runs "
                "WHERE agent_id = 'meta_pauser' "
                "AND triggered_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


def _camp(**kw) -> CampaignSnapshot:
    base = dict(
        campaign_id="c1",
        name="C1",
        spend=10_000,
        attributed_revenue=10_000,
        rto_adjusted_revenue=10_000,
        conversions=100,
        learning_phase=False,
    )
    base.update(kw)
    return CampaignSnapshot(**base)


def test_post_rto_roas_computation():
    c = _camp(spend=10_000, rto_adjusted_revenue=20_000)
    assert c.post_rto_roas == 2.0


def test_skip_when_in_learning_phase():
    c = _camp(learning_phase=True, conversions=10)
    action, *_ = _decide_for_campaign(c)
    assert action == "skip_learning_phase"


def test_skip_when_conversions_below_threshold():
    c = _camp(conversions=49)
    action, *_ = _decide_for_campaign(c)
    assert action == "skip_learning_phase"


def test_pause_when_low_roas_high_spend():
    c = _camp(spend=10_000, rto_adjusted_revenue=4_000, conversions=80)
    action, _, savings, _ = _decide_for_campaign(c)
    assert action == "pause_campaign"
    assert savings == 10_000


def test_no_pause_when_low_roas_low_spend():
    c = _camp(spend=2_000, rto_adjusted_revenue=500, conversions=80)
    action, *_ = _decide_for_campaign(c)
    assert action == "keep"


def test_reduce_when_marginal_roas_high_spend():
    c = _camp(spend=20_000, rto_adjusted_revenue=18_000, conversions=200)
    action, _, savings, _ = _decide_for_campaign(c)
    assert action == "reduce_budget_50"
    assert savings == 10_000


def test_keep_when_healthy_roas():
    c = _camp(spend=10_000, rto_adjusted_revenue=30_000, conversions=200)
    action, *_ = _decide_for_campaign(c)
    assert action == "keep"


def test_decide_aggregates_proposals_and_total_savings():
    pauser = MetaPauser()
    ev = Evidence(
        features={
            "campaigns": [
                dict(
                    campaign_id="c1",
                    name="bad",
                    spend=10_000,
                    attributed_revenue=4_000,
                    rto_adjusted_revenue=4_000,
                    conversions=80,
                    learning_phase=False,
                ),  # PAUSE → save 10k
                dict(
                    campaign_id="c2",
                    name="meh",
                    spend=20_000,
                    attributed_revenue=18_000,
                    rto_adjusted_revenue=18_000,
                    conversions=200,
                    learning_phase=False,
                ),  # REDUCE → save 10k
                dict(
                    campaign_id="c3",
                    name="learning",
                    spend=1_000,
                    attributed_revenue=2_000,
                    rto_adjusted_revenue=2_000,
                    conversions=10,
                    learning_phase=True,
                ),  # SKIP
            ],
        },
        citations=[],
    )
    d = pauser.decide(ev)
    assert d.band == "HIGH"
    assert d.expected_savings_inr == 20_000
    assert len(d.payload["proposals"]) == 2


def test_decide_low_band_when_all_healthy():
    pauser = MetaPauser()
    ev = Evidence(
        features={
            "campaigns": [
                dict(
                    campaign_id="c1",
                    name="ok",
                    spend=10_000,
                    attributed_revenue=30_000,
                    rto_adjusted_revenue=30_000,
                    conversions=200,
                    learning_phase=False,
                ),
            ],
        },
        citations=[],
    )
    d = pauser.decide(ev)
    assert d.band == "LOW"
    assert d.expected_savings_inr == 0.0


@pytest.mark.asyncio
async def test_propose_persists_run_log():
    tid = str(uuid.uuid4())
    pauser = MetaPauser()
    ctx = AgentContext(
        tenant_id=tid,
        trigger_payload={
            "campaigns": [
                dict(
                    campaign_id="c1",
                    name="bad",
                    spend=10_000,
                    attributed_revenue=4_000,
                    rto_adjusted_revenue=4_000,
                    conversions=80,
                    learning_phase=False,
                ),
            ],
        },
    )
    ev = await pauser.gather(ctx)
    d = pauser.decide(ev)
    log = await pauser.propose(ctx, d, ev)

    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT band, expected_savings_inr, proposed_action "
                "FROM core.agent_runs WHERE run_id = :rid"
            ),
            {"rid": log.run_id},
        )
        row = r.first()
    assert row is not None
    assert row.band == "HIGH"
    assert float(row.expected_savings_inr) == 10_000
    assert row.proposed_action["dry_run"] is True
    proposals = row.proposed_action["payload"]["proposals"]
    assert proposals[0]["action"] == "pause_campaign"
