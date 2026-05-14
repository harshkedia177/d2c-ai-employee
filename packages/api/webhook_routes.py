"""Non-blocking webhook ingress."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from packages.scaffolding.queues import enqueue
from packages.warehouse.db import SessionLocal

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

WEBHOOK_INBOX_TABLE = "raw.shopify_webhook_inbox"


@router.post("/shopify/{tenant_id}/{topic_a}/{topic_b}")
async def shopify_webhook(
    tenant_id: str,
    topic_a: str,
    topic_b: str,
    request: Request,
) -> dict[str, Any]:
    # Shopify topics like 'orders/create' arrive as two path segments; rejoin them.
    topic = f"{topic_a}/{topic_b}"
    body = await request.body()
    payload = json.loads(body) if body else {}
    source_id = str(payload.get("id") or payload.get("admin_graphql_api_id") or "unknown")

    async with SessionLocal() as s:
        row = await s.execute(
            text(
                f"INSERT INTO {WEBHOOK_INBOX_TABLE} "
                f"(tenant_id, source_id, payload, payload_hash, "
                f" source_record_url, fetched_at, connector_version) "
                f"VALUES (:t, :sid, CAST(:p AS jsonb), :h, :u, :ts, :cv) "
                f"RETURNING row_id"
            ),
            {
                "t": tenant_id,
                "sid": source_id,
                "p": body.decode() if body else "{}",
                "h": _payload_hash(body),
                "u": f"webhook://shopify/{tenant_id}/{topic}/{source_id}",
                "ts": datetime.now(UTC),
                "cv": "shopify@webhook@0.1.0",
            },
        )
        await s.commit()
        raw_row_id = row.scalar_one()

    await enqueue(
        "realtime",
        tenant_id,
        "shopify_webhook",
        {"topic": topic, "raw_row_id": raw_row_id, "source_id": source_id},
    )
    return {"ok": True, "raw_row_id": raw_row_id}


def _payload_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
