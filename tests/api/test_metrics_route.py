"""Tests for GET /metrics — semantic-layer catalogue exposed for the UI."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from packages.api.main import app


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_all_eight_metrics():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    metric_ids = {m["id"] for m in body["metrics"]}
    assert {
        "gmv",
        "aov",
        "rto_rate",
        "cac",
        "post_rto_roas",
        "contribution_margin_per_order",
        "pincode_rto_rate_90d",
        "sku_rto_rate_90d",
    } == metric_ids
    dim_ids = {d["id"] for d in body["dimensions"]}
    assert "campaign" in dim_ids and "pincode" in dim_ids


@pytest.mark.asyncio
async def test_metrics_endpoint_shape():
    """Each metric row exposes id, description, grain; each dimension row id+sql."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/metrics")
    body = r.json()
    for m in body["metrics"]:
        assert "id" in m and "description" in m and "grain" in m
    for d in body["dimensions"]:
        assert "id" in d and "sql" in d
