import uuid

import pytest
from sqlalchemy import text

from packages.agents.base import AgentContext, Evidence
from packages.agents.rto_risk_flagger import (
    LOW_THRESHOLD,
    MED_THRESHOLD,
    SAVINGS_HIGH,
    SAVINGS_MED,
    RTOFeatures,
    RTORiskFlagger,
    _address_quality_score,
    _band,
    _cart_value_zscore,
    _score,
    _time_of_day_risk,
)
from packages.warehouse.db import SessionLocal


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with SessionLocal() as s:
        await s.execute(
            text(
                "DELETE FROM core.agent_runs "
                "WHERE agent_id = 'rto_risk_flagger' "
                "AND triggered_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


def _features(**overrides) -> RTOFeatures:
    base = dict(
        pincode_rto_rate=0.0,
        customer_prior_rto_rate=0.0,
        sku_basket_rto_rate=0.0,
        cart_value_zscore=0.0,
        address_quality_score=0.0,
        time_of_day_risk=0.0,
        pincode_sample_size=0,
        customer_orders_seen=0,
    )
    base.update(overrides)
    return RTOFeatures(**base)


def test_score_clamps_to_unit_interval():
    f = _features(pincode_rto_rate=2.0, customer_prior_rto_rate=2.0)
    assert _score(f) == 1.0
    g = _features()
    assert _score(g) == _score(g)  # deterministic


def test_high_band_when_pincode_high_rto_and_high_address_risk():
    f = _features(
        pincode_rto_rate=0.34,
        pincode_sample_size=87,
        customer_prior_rto_rate=0.5,
        customer_orders_seen=2,
        sku_basket_rto_rate=0.18,
        cart_value_zscore=1.0,
        address_quality_score=0.4,
        time_of_day_risk=1.0,
    )
    s = _score(f)
    assert _band(s) == "HIGH"


def test_low_band_when_all_features_low():
    f = _features(pincode_sample_size=200, customer_orders_seen=10)
    assert _band(_score(f)) == "LOW"


def test_medium_band_threshold_inclusive_lower():
    f = _features(
        pincode_rto_rate=LOW_THRESHOLD / 0.35 + 0.01,
        pincode_sample_size=100,
        customer_orders_seen=5,
    )
    s = _score(f)
    assert s >= LOW_THRESHOLD


def test_decide_high_proposes_downgrade_to_prepaid_with_240rs_savings():
    flagger = RTORiskFlagger()
    ev = Evidence(
        features={
            "pincode_rto_rate": 0.34,
            "pincode_sample_size": 87,
            "customer_prior_rto_rate": 0.5,
            "customer_orders_seen": 2,
            "sku_basket_rto_rate": 0.18,
            "cart_value_zscore": 1.0,
            "address_quality_score": 0.4,
            "time_of_day_risk": 1.0,
        },
        citations=[],
    )
    d = flagger.decide(ev)
    assert d.band == "HIGH"
    assert d.action_type == "downgrade_to_prepaid"
    assert d.expected_savings_inr == SAVINGS_HIGH


def test_decide_low_proposes_ship_as_is_no_savings():
    flagger = RTORiskFlagger()
    ev = Evidence(
        features={
            "pincode_rto_rate": 0.05,
            "pincode_sample_size": 200,
            "customer_prior_rto_rate": 0.0,
            "customer_orders_seen": 5,
            "sku_basket_rto_rate": 0.0,
            "cart_value_zscore": 0.1,
            "address_quality_score": 0.1,
            "time_of_day_risk": 0.0,
        },
        citations=[],
    )
    d = flagger.decide(ev)
    assert d.band == "LOW"
    assert d.action_type == "ship_as_is"
    assert d.expected_savings_inr == 0.0


def test_decide_med_proposes_whatsapp_confirm_with_partial_savings():
    flagger = RTORiskFlagger()
    # Construct features that score in MED band (~0.30-0.45)
    ev = Evidence(
        features={
            "pincode_rto_rate": 0.5,
            "pincode_sample_size": 50,
            "customer_prior_rto_rate": 0.3,
            "customer_orders_seen": 2,
            "sku_basket_rto_rate": 0.2,
            "cart_value_zscore": 0.5,
            "address_quality_score": 0.3,
            "time_of_day_risk": 0.0,
        },
        citations=[],
    )
    d = flagger.decide(ev)
    assert d.band == "MED"
    assert d.action_type == "send_whatsapp_confirm"
    assert d.expected_savings_inr == SAVINGS_MED


def test_cold_start_pincode_marks_low_confidence():
    flagger = RTORiskFlagger()
    ev = Evidence(
        features={
            "pincode_rto_rate": 0.0,
            "pincode_sample_size": 5,  # below threshold (20)
            "customer_prior_rto_rate": 0.0,
            "customer_orders_seen": 0,
            "sku_basket_rto_rate": 0.0,
            "cart_value_zscore": 0.0,
            "address_quality_score": 0.0,
            "time_of_day_risk": 0.0,
        },
        citations=[],
    )
    d = flagger.decide(ev)
    assert d.payload["confidence"] == "low"


def test_address_quality_penalises_keywords():
    assert _address_quality_score("PG block, near hostel", "Bangalore") > 0.5
    assert _address_quality_score("123 Main St, Apartment 4B", "Bangalore") < 0.3


def test_address_quality_handles_missing_address():
    assert _address_quality_score(None, None) == 1.0
    assert _address_quality_score("123 Main Street, Bangalore", None) > 0.3


def test_time_of_day_late_night_flagged():
    assert _time_of_day_risk("2026-05-01T23:30:00Z") == 1.0
    assert _time_of_day_risk("2026-05-01T03:00:00Z") == 1.0
    assert _time_of_day_risk("2026-05-01T14:00:00Z") == 0.0
    assert _time_of_day_risk(None) == 0.0


def test_cart_value_zscore_clamped():
    assert _cart_value_zscore(0) == 0.0
    assert _cart_value_zscore(50000) == 1.0  # extreme high
    assert 0 < _cart_value_zscore(2000) < 0.7


@pytest.mark.asyncio
async def test_propose_persists_run_log_to_agent_runs():
    tid = str(uuid.uuid4())
    flagger = RTORiskFlagger()
    ctx = AgentContext(
        tenant_id=tid,
        trigger_payload={
            "id": "shopify-test-1",
            "total_price": "2400",
            "gateway": "Cash on Delivery",
            "shipping_address": {"zip": "110084", "address1": "PG hostel block"},
            "customer": {"id": "cust-7"},
            "line_items": [{"sku": "SKU-1"}],
            "created_at": "2026-05-01T23:00:00Z",
        },
    )
    ev = Evidence(
        features={
            "pincode_rto_rate": 0.34,
            "pincode_sample_size": 87,
            "customer_prior_rto_rate": 0.5,
            "customer_orders_seen": 2,
            "sku_basket_rto_rate": 0.18,
            "cart_value_zscore": 0.6,
            "address_quality_score": 0.8,
            "time_of_day_risk": 1.0,
        },
        citations=[
            {"url": "https://example.com/x", "raw_row_id": 1},
        ],
    )
    d = flagger.decide(ev)
    log = await flagger.propose(ctx, d, ev)

    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT band, score, reasoning, proposed_action "
                "FROM core.agent_runs WHERE run_id = :rid"
            ),
            {"rid": log.run_id},
        )
        row = r.first()
    assert row is not None
    assert row.band == "HIGH"
    assert float(row.score) >= MED_THRESHOLD
    assert "pincode RTO 34%" in row.reasoning
    # proposed_action.dry_run = True (never executes)
    assert row.proposed_action["dry_run"] is True


@pytest.mark.asyncio
async def test_gather_with_no_pincode_returns_zero_rates():
    tid = str(uuid.uuid4())
    flagger = RTORiskFlagger()
    ctx = AgentContext(
        tenant_id=tid,
        trigger_payload={
            "id": "shopify-no-zip",
            "total_price": "1000",
            "gateway": "Cash on Delivery",
            "shipping_address": {},
            "customer": {"id": "cust-x"},
            "line_items": [],
            "created_at": "2026-05-01T14:00:00Z",
        },
    )
    ev = await flagger.gather(ctx)
    assert ev.features["pincode_rto_rate"] == 0.0
    assert ev.features["pincode_sample_size"] == 0


def test_agent_implements_protocol():
    """Compile-time check: RTORiskFlagger satisfies Agent."""
    flagger = RTORiskFlagger()
    # duck-typing — Agent is a Protocol, not runtime_checkable
    assert hasattr(flagger, "agent_id")
    assert hasattr(flagger, "schedule")
    assert callable(flagger.gather)
    assert callable(flagger.decide)
    assert callable(flagger.propose)
