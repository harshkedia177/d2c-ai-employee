import pytest

from packages.chat.renderer import (
    RenderResult,
    UnresolvedPlaceholder,
    format_inr,
    format_pct,
    render,
)


def test_format_inr_uses_indian_grouping():
    assert format_inr(482310) == "₹4,82,310"
    assert format_inr(1000) == "₹1,000"
    assert format_inr(100) == "₹100"
    assert format_inr(99999999) == "₹9,99,99,999"
    assert format_inr(1234.50) == "₹1,234.50"
    assert format_inr(None) == "₹—"


def test_format_pct():
    assert format_pct(0.34) == "34.0%"
    assert format_pct(0.0) == "0.0%"
    assert format_pct(1.0) == "100.0%"


def test_render_substitutes_placeholders():
    draft = "GMV last week was {{m:gmv_w19}} across {{m:order_count}} orders."
    metric_results = {
        "gmv_w19": {
            "value": 482310,
            "provenance": {"query_hash": "h1", "metric_id": "gmv", "citations": []},
        },
        "order_count": {
            "value": 1247,
            "provenance": {"query_hash": "h2", "metric_id": "order_count", "citations": []},
        },
    }
    res = render(draft, metric_results, formats={"gmv_w19": "inr"})
    assert "₹4,82,310" in res.text
    assert "1247" in res.text
    assert "₹4,82,310" in res.substituted_values
    assert "1247" in res.substituted_values


def test_render_attaches_footnotes_per_placeholder():
    draft = "GMV: {{m:gmv}}."
    res = render(
        draft,
        {
            "gmv": {
                "value": 1000,
                "provenance": {
                    "query_hash": "abc",
                    "metric_id": "gmv",
                    "citations": [{"url": "https://shop.example.com/orders/1"}] * 100,
                    "sample_size": 100,
                },
            }
        },
    )
    assert len(res.footnotes) == 1
    fn = res.footnotes[0]
    assert fn["query_hash"] == "abc"
    assert fn["total_sources"] == 100
    assert len(fn["citations"]) == 5


def test_render_raises_on_unresolved_placeholder():
    draft = "Value is {{m:never_provided}}"
    with pytest.raises(UnresolvedPlaceholder):
        render(draft, {})


def test_render_handles_repeated_same_placeholder():
    draft = "{{m:gmv}} = {{m:gmv}}, twice"
    res = render(
        draft,
        {
            "gmv": {
                "value": 100,
                "provenance": {"query_hash": "h", "metric_id": "gmv", "citations": []},
            }
        },
    )
    assert res.text.count("100") == 2


def test_render_pct_formatter():
    res = render(
        "RTO rate: {{m:rto}}",
        {
            "rto": {
                "value": 0.34,
                "provenance": {"query_hash": "h", "metric_id": "rto_rate", "citations": []},
            }
        },
        formats={"rto": "pct"},
    )
    assert "34.0%" in res.text
    assert "34.0%" in res.substituted_values


def test_render_emits_render_result_dataclass():
    res = render("hi", {})
    assert isinstance(res, RenderResult)
    assert res.text == "hi"
    assert res.substituted_values == frozenset()
