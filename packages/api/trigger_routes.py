"""Mock-data triggers: synth a fresh webhook or run a cron handler on demand."""

from __future__ import annotations

import json
import logging
import random
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from starlette.datastructures import Headers

from packages.agents.scheduler import (
    run_meta_pauser_for_tenant,
    run_pincode_blocker_for_tenant,
)
from packages.api.webhook_routes import shopify_webhook
from packages.warehouse.db import SessionLocal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/triggers", tags=["triggers"])

# Pincode pool — mix of high/medium/low historical RTO so each click can land
# the synth order anywhere on the risk spectrum.
PINCODE_POOL = [
    "201001", "110084", "302013", "560100", "700091",
    "400001", "411001", "560001", "600028", "110001",
    "380001", "208001", "143001", "682001", "751001",
]
SKU_POOL = ["SKU-X1", "SKU-X2", "SKU-A100", "SKU-K7", "SKU-D55", "SKU-FK0"]
NAMES = ["Ravi", "Aanya", "Vihaan", "Priya", "Aarav", "Sneha", "Karan", "Meera"]


class WebhookTriggerResponse(BaseModel):
    ok: bool
    raw_row_id: int
    summary: dict[str, Any]


class CronTriggerResponse(BaseModel):
    ok: bool
    run_id: str
    agent_id: str
    band: str | None
    expected_savings_inr: float | None
    reasoning: str


async def _next_synth_id() -> int:
    """Monotonic counter so each click produces a unique source_id even though
    the body is otherwise random. Lives in raw.shopify_webhook_inbox as the
    largest synth- prefixed source_id seen so far."""
    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT COALESCE(MAX("
                "CAST(substring(source_id FROM 'synth-(\\d+)') AS bigint)"
                "), 0) + 1 "
                "FROM raw.shopify_webhook_inbox WHERE source_id LIKE 'synth-%'"
            )
        )
        return int(r.scalar_one())


@router.post("/webhook", response_model=WebhookTriggerResponse)
async def trigger_webhook(tenant_id: str) -> WebhookTriggerResponse:
    """Generate a randomized Shopify order webhook and POST it through the
    real webhook path so the worker + RTO Flagger fire end-to-end."""
    pincode = random.choice(PINCODE_POOL)
    cart = random.randint(800, 22_000)
    sku = random.choice(SKU_POOL)
    cust_id = random.randint(7_000_000, 9_999_999)
    seq = await _next_synth_id()
    source_id = f"synth-{seq}"
    name = random.choice(NAMES)
    addr_pool = [".", "12", "Block A Apt 7, MG Road", "404 building"]

    payload = {
        "id": source_id,
        "name": f"#SYNTH-{seq}",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "total_price": f"{cart}.00",
        "subtotal_price": f"{cart - 200}.00",
        "total_tax": "100.00",
        "currency": "INR",
        "financial_status": "pending",
        "gateway": "Cash on Delivery",
        "customer": {"id": cust_id, "email": f"{name.lower()}{cust_id}@example.com", "first_name": name},
        "shipping_address": {
            "zip": pincode,
            "city": "X",
            "address1": random.choice(addr_pool),
            "country": "India",
        },
        "line_items": [
            {"id": 1, "sku": sku, "title": f"Item {sku}", "quantity": 1, "price": f"{cart}.00"}
        ],
    }

    # Re-enter the real webhook handler — same code path as Shopify hitting us live.
    result = await shopify_webhook(
        tenant_id=tenant_id,
        topic_a="orders",
        topic_b="create",
        request=_StubRequest(json.dumps(payload).encode()),  # type: ignore[arg-type]
    )
    return WebhookTriggerResponse(
        ok=True,
        raw_row_id=int(result["raw_row_id"]),
        summary={
            "source_id": source_id,
            "pincode": pincode,
            "cart_value_inr": cart,
            "sku": sku,
            "customer_id": cust_id,
        },
    )


class _StubRequest:
    """Minimal duck for FastAPI's Request — only .body() and .headers are used
    by `shopify_webhook`. Lets us re-enter the real handler without HTTP."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers = Headers({"content-type": "application/json"})

    async def body(self) -> bytes:
        return self._body


_CRON_HANDLERS = {
    "pincode_cod_blocker": run_pincode_blocker_for_tenant,
    "meta_pauser": run_meta_pauser_for_tenant,
}


@router.post("/cron/{agent_id}", response_model=CronTriggerResponse)
async def trigger_cron(agent_id: str, tenant_id: str) -> CronTriggerResponse:
    handler = _CRON_HANDLERS.get(agent_id)
    if handler is None:
        raise HTTPException(
            status_code=400,
            detail=f"no cron handler for agent_id={agent_id!r}. "
            f"available: {sorted(_CRON_HANDLERS)}",
        )
    log_entry = await handler(tenant_id)
    return CronTriggerResponse(
        ok=True,
        run_id=log_entry.run_id,
        agent_id=log_entry.agent_id,
        band=log_entry.band,
        expected_savings_inr=log_entry.expected_savings_inr,
        reasoning=log_entry.reasoning,
    )
