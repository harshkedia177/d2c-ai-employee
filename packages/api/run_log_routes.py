"""GET /runs and GET /tenants — read-only endpoints for the Agent Bench UI."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from packages.semantic_layer.compiler import list_dimensions, list_metrics
from packages.warehouse.db import SessionLocal

router = APIRouter(tags=["runs"])


@router.get("/runs")
async def list_runs(
    tenant_id: str = Query(...),
    agent_id: str | None = None,
    limit: int = Query(20, le=100),
) -> dict[str, Any]:
    sql = """
      SELECT run_id, agent_id, triggered_at, score, band,
             expected_savings_inr, reasoning, proposed_action,
             evidence, trigger, cited_provenance
      FROM core.agent_runs
      WHERE tenant_id = :t
    """
    params: dict[str, Any] = {"t": tenant_id, "lim": limit}
    if agent_id:
        sql += " AND agent_id = :aid"
        params["aid"] = agent_id
    sql += " ORDER BY triggered_at DESC LIMIT :lim"
    async with SessionLocal() as s:
        result = await s.execute(text(sql), params)
        rows = [dict(r) for r in result.mappings()]
    for r in rows:
        if r.get("run_id") is not None:
            r["run_id"] = str(r["run_id"])
        if r.get("triggered_at") is not None:
            r["triggered_at"] = r["triggered_at"].isoformat()
        if r.get("score") is not None:
            r["score"] = float(r["score"])
        if r.get("expected_savings_inr") is not None:
            r["expected_savings_inr"] = float(r["expected_savings_inr"])
    return {"runs": rows}


@router.get("/metrics")
async def list_all_metrics() -> dict[str, Any]:
    return {
        "metrics": list_metrics(),
        "dimensions": list_dimensions(),
    }


@router.get("/tenants")
async def list_tenants() -> dict[str, Any]:
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "SELECT tenant_id, slug, created_at FROM control.tenant "
                "ORDER BY created_at DESC LIMIT 50"
            )
        )
        rows = [dict(r) for r in result.mappings()]
    for r in rows:
        if r.get("tenant_id") is not None:
            r["tenant_id"] = str(r["tenant_id"])
        if r.get("created_at") is not None:
            r["created_at"] = r["created_at"].isoformat()
    return {"tenants": rows}
