"""Postgres-backed FIFO queues with SELECT FOR UPDATE SKIP LOCKED."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from packages.warehouse.db import SessionLocal

QUEUES: dict[str, str] = {
    "realtime": "control.queue_realtime",
    "backfill": "control.queue_backfill",
}


def _table(queue: str) -> str:
    if queue not in QUEUES:
        raise ValueError(f"unknown queue: {queue}")
    return QUEUES[queue]


async def enqueue(
    queue: str,
    tenant_id: str,
    kind: str,
    payload: dict[str, Any],
) -> int:
    table = _table(queue)
    async with SessionLocal() as s:
        row = await s.execute(
            text(
                f"INSERT INTO {table} (tenant_id, kind, payload) "
                f"VALUES (:t, :k, CAST(:p AS jsonb)) RETURNING id"
            ),
            {"t": tenant_id, "k": kind, "p": json.dumps(payload)},
        )
        await s.commit()
        return row.scalar_one()


async def dequeue(queue: str) -> dict | None:
    table = _table(queue)
    async with SessionLocal() as s:
        row = await s.execute(
            text(
                f"""
                UPDATE {table}
                SET started_at = now(), attempts = attempts + 1
                WHERE id = (
                  SELECT id FROM {table}
                  WHERE started_at IS NULL AND completed_at IS NULL
                  ORDER BY enqueued_at
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
                )
                RETURNING id, tenant_id, kind, payload
                """
            )
        )
        await s.commit()
        r = row.first()
        return dict(r._mapping) if r else None


async def complete(queue: str, job_id: int) -> None:
    table = _table(queue)
    async with SessionLocal() as s:
        await s.execute(
            text(f"UPDATE {table} SET completed_at = now() WHERE id = :i"),
            {"i": job_id},
        )
        await s.commit()


async def fail(queue: str, job_id: int, error: str) -> None:
    table = _table(queue)
    async with SessionLocal() as s:
        await s.execute(
            text(f"UPDATE {table} SET started_at = NULL, last_error = :e WHERE id = :i"),
            {"i": job_id, "e": error},
        )
        await s.commit()
