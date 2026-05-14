import re

import pytest

from packages.semantic_layer.compiler import (
    compile_metric,
    list_dimensions,
    list_metrics,
)


def test_compile_gmv_returns_sql_with_tenant_filter_and_citations():
    q = compile_metric("gmv", tenant_id="t1")
    assert "SUM(o.total)" in q.sql
    assert "WHERE o.tenant_id = :tenant_id" in q.sql
    assert q.params["tenant_id"] == "t1"
    assert "citations" in q.sql
    assert "ARRAY_AGG" in q.sql
    assert "source_record_url" in q.sql
    assert "raw_row_id" in q.sql


def test_compile_with_dimension_includes_group_by():
    q = compile_metric("aov", tenant_id="t1", dimensions=["month"])
    assert "DATE_TRUNC('week', o.placed_at)" not in q.sql
    assert "DATE_TRUNC('month', o.placed_at)" in q.sql
    assert "GROUP BY" in q.sql


def test_compile_with_filter_uses_named_param():
    q = compile_metric(
        "gmv",
        tenant_id="t1",
        filters={"placed_at__gte": "2026-04-01"},
    )
    assert "o.placed_at >= :placed_at_gte" in q.sql
    assert q.params["placed_at_gte"] == "2026-04-01"


def test_compile_unknown_metric_raises():
    with pytest.raises(ValueError):
        compile_metric("ghost_metric", tenant_id="t1")


def test_compile_unknown_dimension_raises():
    with pytest.raises(ValueError):
        compile_metric("gmv", tenant_id="t1", dimensions=["nonexistent"])


def test_unknown_filter_operator_raises():
    with pytest.raises(ValueError):
        compile_metric("gmv", tenant_id="t1", filters={"placed_at__like": "%2026%"})


def test_in_filter_with_empty_list_raises():
    with pytest.raises(ValueError):
        compile_metric("gmv", tenant_id="t1", filters={"gateway__in": []})


def test_in_filter_expands_to_named_params():
    q = compile_metric(
        "gmv",
        tenant_id="t1",
        filters={"gateway__in": ["razorpay", "Cash on Delivery"]},
    )
    assert "o.gateway IN" in q.sql
    assert q.params["gateway_in_0"] == "razorpay"
    assert q.params["gateway_in_1"] == "Cash on Delivery"


def test_query_hash_changes_with_filters():
    a = compile_metric("gmv", tenant_id="t1")
    b = compile_metric("gmv", tenant_id="t1", filters={"placed_at__gte": "2026-01-01"})
    assert a.query_hash != b.query_hash


def test_query_hash_stable_for_same_inputs():
    a = compile_metric("gmv", tenant_id="t1", dimensions=["month"])
    b = compile_metric("gmv", tenant_id="t1", dimensions=["month"])
    assert a.query_hash == b.query_hash


def test_min_sample_size_propagated_for_pincode_metric():
    q = compile_metric("pincode_rto_rate_90d", tenant_id="t1", dimensions=["pincode"])
    assert q.min_sample_size == 20


def test_post_rto_roas_includes_three_joins():
    q = compile_metric("post_rto_roas", tenant_id="t1", dimensions=["campaign"])
    assert q.sql.count("LEFT JOIN") >= 3 or q.sql.count("JOIN") >= 3
    assert "asd.spend" in q.sql
    assert "s.is_rto" in q.sql


def test_every_metric_compiles_without_error():
    for m in list_metrics():
        q = compile_metric(m["id"], tenant_id="t1")
        assert "tenant_id" in q.params
        assert re.search(r"\bcitations\b", q.sql), (
            f"metric {m['id']} did not project citations — citation contract broken"
        )
        assert "ARRAY_AGG" in q.sql, f"metric {m['id']} missing ARRAY_AGG in citation projection"


def test_list_metrics_includes_all_eight():
    ids = {m["id"] for m in list_metrics()}
    assert ids == {
        "gmv",
        "aov",
        "rto_rate",
        "cac",
        "post_rto_roas",
        "contribution_margin_per_order",
        "pincode_rto_rate_90d",
        "sku_rto_rate_90d",
    }


def test_list_dimensions_includes_pincode_campaign_sku():
    ids = {d["id"] for d in list_dimensions()}
    assert {"pincode", "campaign", "sku", "month", "week"} <= ids
