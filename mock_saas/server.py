from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query

DATA = Path(__file__).parent / "seed" / "data"


def load(merchant: str, name: str) -> list[dict]:
    p = DATA / f"{merchant}_{name}.json"
    if not p.exists():
        raise HTTPException(404, f"no seed for {merchant}/{name}")
    return json.loads(p.read_text())


app = FastAPI(title="mock-saas")


@app.get("/shopify/{merchant}/admin/api/2026-01/orders.json")
def shopify_orders(
    merchant: str,
    updated_at_min: str | None = None,
    limit: int = Query(50, le=250),
):
    rows = load(merchant, "shopify_orders")
    if updated_at_min:
        rows = [r for r in rows if r["updated_at"] > updated_at_min]
    return {"orders": rows[:limit]}


@app.get("/meta/v19.0/act_{ad_account}/insights")
def meta_insights(
    ad_account: str,
    time_range: str | None = None,
    fields: str | None = None,
    limit: int = 1000,
    after: str | None = None,
):
    rows = load(ad_account, "meta_insights")
    start = int(after) if after and after.isdigit() else 0
    page = rows[start : start + limit]
    next_start = start + limit
    has_more = next_start < len(rows)
    # Real Graph API shape: {data, paging: {cursors: {before, after}}}.
    # https://developers.facebook.com/docs/marketing-api/insights/
    paging: dict = {"cursors": {"before": str(start), "after": str(next_start) if has_more else ""}}
    return {"data": page, "paging": paging}


@app.get("/meta/v19.0/act_{ad_account}/campaigns")
def meta_campaigns(ad_account: str):
    return {"data": load(ad_account, "meta_campaigns")}


@app.post("/shiprocket/v1/external/auth/login")
def sr_login():
    return {"token": "mock-shiprocket-token", "expires_in": 240 * 3600}


@app.get("/shiprocket/v1/external/orders")
def sr_orders(
    merchant: str = Query(...),
    page: int = 1,
    per_page: int = 50,
    authorization: str = Header(...),
):
    if "mock-shiprocket-token" not in authorization:
        raise HTTPException(401, "bad token")
    rows = load(merchant, "shiprocket_shipments")
    start = (page - 1) * per_page
    return {"data": rows[start : start + per_page], "meta": {"total": len(rows)}}
