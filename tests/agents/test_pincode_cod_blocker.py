import uuid

import pytest
from sqlalchemy import text

from packages.agents.base import AgentContext, Evidence
from packages.agents.pincode_cod_blocker import (
    DEFAULT_MARGIN_PCT,
    MIN_SAMPLE_SIZE,
    TOP_N,
    PincodeCodBlocker,
    PincodeStat,
    _should_block,
)
from packages.warehouse.db import SessionLocal


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with SessionLocal() as s:
        await s.execute(
            text(
                "DELETE FROM core.agent_runs "
                "WHERE agent_id = 'pincode_cod_blocker' "
                "AND triggered_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


def test_should_not_block_below_min_sample():
    p = PincodeStat(pincode="110084", rto_rate=0.50, sample_size=15, avg_cart_value=2000)
    assert _should_block(p, DEFAULT_MARGIN_PCT) is False


def test_should_block_when_expected_loss_exceeds_half_margin():
    # rto 33% × (2000×0.7 + 240) = 0.33 × 1640 = 541; half-margin = 0.5×600 = 300; 541 > 300 → block
    p = PincodeStat(pincode="110084", rto_rate=0.33, sample_size=87, avg_cart_value=2000)
    assert _should_block(p, DEFAULT_MARGIN_PCT) is True


def test_should_not_block_when_low_rto():
    p = PincodeStat(pincode="560001", rto_rate=0.05, sample_size=200, avg_cart_value=2000)
    assert _should_block(p, DEFAULT_MARGIN_PCT) is False


def test_decide_returns_top_n_ranked_by_expected_loss():
    blocker = PincodeCodBlocker()
    pincodes = [
        # bad ones
        dict(pincode=f"BAD-{i}", rto_rate=0.4 + i * 0.01, sample_size=50, avg_cart_value=2000)
        for i in range(25)
    ] + [
        # good ones
        dict(pincode=f"GOOD-{i}", rto_rate=0.05, sample_size=100, avg_cart_value=2000)
        for i in range(5)
    ]
    ev = Evidence(features={"pincode_stats": pincodes}, citations=[])
    d = blocker.decide(ev)
    assert d.band == "HIGH"
    proposals = d.payload["proposals"]
    assert len(proposals) == TOP_N  # capped at 20
    # ranked by expected_loss desc → highest rto first
    rates = [p["rto_rate"] for p in proposals]
    assert rates == sorted(rates, reverse=True)


def test_decide_low_band_when_no_candidates_above_threshold():
    blocker = PincodeCodBlocker()
    pincodes = [
        dict(pincode="GOOD", rto_rate=0.05, sample_size=200, avg_cart_value=2000),
    ]
    d = blocker.decide(Evidence(features={"pincode_stats": pincodes}, citations=[]))
    assert d.band == "LOW"
    assert d.payload["proposals"] == []


def test_decide_skips_undersample_pincodes():
    blocker = PincodeCodBlocker()
    pincodes = [
        dict(
            pincode="UNDER", rto_rate=0.99, sample_size=MIN_SAMPLE_SIZE - 1, avg_cart_value=2000
        ),  # extreme but undersampled — must skip
    ]
    d = blocker.decide(Evidence(features={"pincode_stats": pincodes}, citations=[]))
    assert d.payload["proposals"] == []
    assert d.band == "LOW"


@pytest.mark.asyncio
async def test_propose_persists_run_log():
    tid = str(uuid.uuid4())
    blocker = PincodeCodBlocker()
    ctx = AgentContext(
        tenant_id=tid,
        trigger_payload={
            "pincode_stats": [
                dict(pincode="110084", rto_rate=0.33, sample_size=87, avg_cart_value=2000),
            ],
        },
    )
    ev = await blocker.gather(ctx)
    d = blocker.decide(ev)
    log = await blocker.propose(ctx, d, ev)

    async with SessionLocal() as s:
        r = await s.execute(
            text("SELECT band, proposed_action FROM core.agent_runs WHERE run_id = :rid"),
            {"rid": log.run_id},
        )
        row = r.first()
    assert row is not None
    assert row.band == "HIGH"
    assert row.proposed_action["dry_run"] is True
    assert row.proposed_action["payload"]["proposals"][0]["pincode"] == "110084"
