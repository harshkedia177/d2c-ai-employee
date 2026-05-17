from __future__ import annotations

from datetime import date

import pytest

from packages.semantic_layer.example_generator import (
    TenantProbe,
    _dedupe,
    generate,
)

TODAY = date(2026, 5, 15)


def _questions(examples: list[dict]) -> list[str]:
    return [e["question"] for e in examples]


def test_generate_with_all_on_produces_diverse_examples():
    out = generate(TenantProbe.all_on(), today=TODAY)
    assert len(out) >= 30, f"expected ≥30 examples, got {len(out)}"
    # Each example has at least one tool call.
    for e in out:
        assert "question" in e and isinstance(e["question"], str) and e["question"]
        assert "plan" in e and isinstance(e["plan"], list) and len(e["plan"]) >= 1
        for step in e["plan"]:
            assert step["tool"] == "compute_metric"
            assert "metric_id" in step["args"]


def test_each_plan_step_uses_a_real_metric():
    from packages.semantic_layer.compiler import _load_config

    valid = set(_load_config()["metrics"].keys())
    out = generate(TenantProbe.all_on(), today=TODAY)
    for e in out:
        for step in e["plan"]:
            assert step["args"]["metric_id"] in valid


def test_each_dimension_is_declared_supported_for_its_metric():
    from packages.semantic_layer.compiler import _load_config

    metrics = _load_config()["metrics"]
    out = generate(TenantProbe.all_on(), today=TODAY)
    for e in out:
        for step in e["plan"]:
            mid = step["args"]["metric_id"]
            for d in step["args"].get("dimensions") or []:
                supported = set(metrics[mid].get("dimensions_supported") or [])
                assert d in supported, (
                    f"metric {mid!r} emitted dimension {d!r} not in dimensions_supported={supported}"
                )


def test_time_filters_use_metrics_declared_time_column():
    from packages.semantic_layer.compiler import _load_config

    metrics = _load_config()["metrics"]
    out = generate(TenantProbe.all_on(), today=TODAY)
    for e in out:
        for step in e["plan"]:
            mid = step["args"]["metric_id"]
            tc = metrics[mid].get("time_column")
            for k in (step["args"].get("filters") or {}).keys():
                field = k.split("__")[0]
                # Must either match the metric's time column or be a non-time filter.
                if "__" in k and tc:
                    assert field == tc, (
                        f"{mid!r} filter {k!r} ≠ declared time_column {tc!r}"
                    )


def test_dedupe_removes_repeated_questions():
    examples = [
        {"question": "What's my GMV?", "plan": []},
        {"question": "what's my gmv?", "plan": []},
        {"question": "What's   my GMV?", "plan": []},
        {"question": "Show me my revenue", "plan": []},
    ]
    out = _dedupe(examples)
    assert len(out) == 2


def test_probe_gates_skip_unsupported_metrics():
    """No campaigns → no cac, post_rto_roas examples; no shipments → no rto."""
    probe = TenantProbe(
        has_orders=True,
        has_shipments_with_rto=False,
        has_campaigns=False,
        has_gateway_diversity=True,
        has_skus=True,
        pincodes_with_signal=0,
    )
    out = generate(probe, today=TODAY)
    seen_metrics = {step["args"]["metric_id"] for e in out for step in e["plan"]}
    assert "cac" not in seen_metrics
    assert "post_rto_roas" not in seen_metrics
    assert "rto_rate" not in seen_metrics
    assert "pincode_rto_rate_90d" not in seen_metrics
    assert "sku_rto_rate_90d" not in seen_metrics
    # gmv / aov / contribution_margin should still show up.
    assert "gmv" in seen_metrics
    assert "aov" in seen_metrics


def test_no_diversity_skips_gateway_breakdowns():
    probe = TenantProbe(has_gateway_diversity=False)
    out = generate(probe, today=TODAY)
    for e in out:
        for step in e["plan"]:
            assert "gateway" not in (step["args"].get("dimensions") or [])


def test_mom_compare_brackets_calendar_months():
    """MoM example must produce TWO calls with non-overlapping month windows."""
    out = generate(TenantProbe.all_on(), today=TODAY)
    mom = [e for e in out if "this month vs last month" in e["question"].lower()]
    assert mom, "no MoM examples generated"
    for e in mom:
        assert len(e["plan"]) == 2
        f1 = e["plan"][0]["args"].get("filters") or {}
        f2 = e["plan"][1]["args"].get("filters") or {}
        # First call covers May (today=2026-05-15), second covers April.
        assert any("2026-05-01" in v for v in f1.values())
        assert any("2026-04-01" in v for v in f2.values())


def test_agent_mirrored_question_for_pincode_block():
    out = generate(TenantProbe.all_on(), today=TODAY)
    qs = _questions(out)
    assert any("block cod" in q.lower() for q in qs), "expected COD-block playbook example"


def test_agent_mirrored_question_for_campaign_pause():
    out = generate(TenantProbe.all_on(), today=TODAY)
    qs = _questions(out)
    assert any("pause" in q.lower() and "campaigns" in q.lower() for q in qs), (
        "expected campaign-pause playbook example"
    )


def test_pulse_question_present():
    out = generate(TenantProbe.all_on(), today=TODAY)
    qs = _questions(out)
    assert any("pulse" in q.lower() for q in qs)


def test_empty_probe_still_produces_nothing_broken():
    out = generate(TenantProbe.none(), today=TODAY)
    # `none` keeps no metrics on; output may be empty or just very small,
    # but it must never raise or emit malformed examples.
    for e in out:
        assert e["question"]
        assert e["plan"]


def test_today_parameter_controls_dates():
    custom = date(2025, 12, 25)
    out = generate(TenantProbe.all_on(), today=custom)
    # Find any example with a time-bounded filter and check the date is in 2025.
    found_date = False
    for e in out:
        for step in e["plan"]:
            for v in (step["args"].get("filters") or {}).values():
                if isinstance(v, str) and v.startswith("2025-"):
                    found_date = True
    assert found_date, "expected at least one 2025-anchored filter when today=2025-12-25"


@pytest.mark.parametrize("probe", [TenantProbe.all_on(), TenantProbe.none()])
def test_generated_examples_are_deduped(probe):
    out = generate(probe, today=TODAY)
    questions = [" ".join(e["question"].lower().split()) for e in out]
    assert len(questions) == len(set(questions))
