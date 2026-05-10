import pytest

from packages.chat.verifier import VerifierError, find_violations, verify_no_uncited_numerals


def test_passes_when_all_numerals_were_substituted():
    text = "GMV last week was ₹4,82,310 across 1,247 orders."
    substituted = frozenset({"₹4,82,310", "4,82,310", "1,247"})
    verify_no_uncited_numerals(text, substituted)  # no raise


def test_rejects_literal_numeral_not_from_substitution():
    text = "GMV last week was about 5 lakh."
    with pytest.raises(VerifierError) as excinfo:
        verify_no_uncited_numerals(text, frozenset())
    assert excinfo.value.numeral == "5"


def test_rejects_estimate_phrasing_red_team():
    text = "Revenue was approximately ₹100,000 last month."
    with pytest.raises(VerifierError) as excinfo:
        verify_no_uncited_numerals(text, frozenset())
    # at least one numeral caught
    assert excinfo.value.violations


def test_passes_with_zero_numerals():
    text = "Your GMV is high but I cannot quote a number without running compute_metric."
    verify_no_uncited_numerals(text, frozenset())


def test_decimal_numerals_caught():
    text = "Revenue was 12.34"
    with pytest.raises(VerifierError):
        verify_no_uncited_numerals(text, frozenset())


def test_indian_comma_numerals_caught():
    text = "Revenue was 4,82,310"
    with pytest.raises(VerifierError):
        verify_no_uncited_numerals(text, frozenset())


def test_substituted_value_with_currency_symbol_matches_in_text():
    """Renderer outputs ₹4,82,310. The text contains it. The numeral inside
    (4,82,310) is reachable by the regex; that bare form must also be in
    substituted_values. The renderer adds it; verifier catches its absence."""
    text = "₹4,82,310"
    # only the formatted form is in the set; the bare numeral is NOT
    incomplete = frozenset({"₹4,82,310"})
    with pytest.raises(VerifierError):
        verify_no_uncited_numerals(text, incomplete)

    # full set (renderer's actual behavior)
    complete = frozenset({"₹4,82,310", "4,82,310"})
    verify_no_uncited_numerals(text, complete)


def test_find_violations_does_not_raise():
    text = "5 lakh and ₹2,000."
    out = find_violations(text, frozenset({"₹2,000", "2,000"}))
    assert len(out) == 1
    assert out[0]["numeral"] == "5"


def test_violation_includes_context():
    text = "GMV last week was 5 lakh."
    with pytest.raises(VerifierError) as excinfo:
        verify_no_uncited_numerals(text, frozenset())
    assert "5" in excinfo.value.context


def test_red_team_dataset_minimal():
    """Quick red-team: prompts that try to elicit estimates.
    These rendered outputs MUST be rejected."""
    bad_outputs = [
        "Approximately 30% RTO rate this month.",
        "About ₹50,000 lost to RTO.",
        "Roughly 1,200 orders.",
        "I'd estimate 2.5x ROAS.",
        "Around 100 campaigns active.",
    ]
    for out in bad_outputs:
        with pytest.raises(VerifierError):
            verify_no_uncited_numerals(out, frozenset())


def test_combined_render_and_verify_round_trip():
    """End-to-end: a draft is rendered, the rendered text passes verify."""
    from packages.chat.renderer import render

    draft = "GMV: {{m:gmv}}. Orders: {{m:cnt}}."
    metric_results = {
        "gmv": {
            "value": 482310,
            "provenance": {"query_hash": "h1", "metric_id": "gmv", "citations": []},
        },
        "cnt": {
            "value": 1247,
            "provenance": {"query_hash": "h2", "metric_id": "order_count", "citations": []},
        },
    }
    res = render(draft, metric_results, formats={"gmv": "inr"})
    verify_no_uncited_numerals(res.text, res.substituted_values)


def test_combined_round_trip_catches_llm_typed_estimate():
    """If the LLM ignores the rule and types a literal numeral alongside
    placeholders, the verifier MUST still catch it."""
    from packages.chat.renderer import render

    draft = "GMV is {{m:gmv}}, roughly 5 lakh."  # LLM cheated with literal '5'
    metric_results = {
        "gmv": {
            "value": 482310,
            "provenance": {"query_hash": "h", "metric_id": "gmv", "citations": []},
        },
    }
    res = render(draft, metric_results, formats={"gmv": "inr"})
    with pytest.raises(VerifierError) as excinfo:
        verify_no_uncited_numerals(res.text, res.substituted_values)
    assert excinfo.value.numeral == "5"
