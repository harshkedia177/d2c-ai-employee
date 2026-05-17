import asyncio
import uuid

import pytest
from sqlalchemy import text

from packages.scaffolding.queues import complete, dequeue, enqueue, fail
from packages.warehouse.db import SessionLocal


@pytest.fixture(autouse=True)
async def _clean_test_jobs():
    """Truncate both queue tables before and after every test for isolation —
    oldest-first dequeue ordering makes shared queue state cause flakes.
    """
    async with SessionLocal() as s:
        await s.execute(text("TRUNCATE control.queue_realtime, control.queue_backfill"))
        await s.commit()
    yield
    async with SessionLocal() as s:
        await s.execute(text("TRUNCATE control.queue_realtime, control.queue_backfill"))
        await s.commit()


@pytest.mark.asyncio
async def test_enqueue_then_dequeue_returns_payload():
    tid = str(uuid.uuid4())
    job_id = await enqueue("realtime", tid, "test_kind", {"x": 1})
    assert job_id > 0
    job = await dequeue("realtime")
    while job and job["tenant_id"] != uuid.UUID(tid):
        await complete("realtime", job["id"])
        job = await dequeue("realtime")
    assert job is not None
    assert job["kind"] == "test_kind"
    assert job["payload"]["x"] == 1
    await complete("realtime", job["id"])


@pytest.mark.asyncio
async def test_two_workers_skip_locked_get_different_jobs():
    tid = str(uuid.uuid4())
    a = await enqueue("realtime", tid, "k", {"n": 1})
    b = await enqueue("realtime", tid, "k", {"n": 2})

    j1, j2 = await asyncio.gather(dequeue("realtime"), dequeue("realtime"))
    ours = [j for j in (j1, j2) if j and j["tenant_id"] == uuid.UUID(tid)]
    if len(ours) == 2:
        assert ours[0]["id"] != ours[1]["id"]
        assert {ours[0]["id"], ours[1]["id"]} == {a, b}
    for j in ours:
        await complete("realtime", j["id"])


@pytest.mark.asyncio
async def test_realtime_and_backfill_are_isolated():
    tid = str(uuid.uuid4())
    rid = await enqueue("realtime", tid, "rt", {})
    bid = await enqueue("backfill", tid, "bf", {})

    seen_bids: set[int] = set()
    while True:
        j = await dequeue("backfill")
        if j is None:
            break
        seen_bids.add(j["id"])
        await complete("backfill", j["id"])
        if bid in seen_bids:
            break
    assert bid in seen_bids
    assert rid not in seen_bids

    while True:
        j = await dequeue("realtime")
        if j is None:
            break
        if j["id"] == rid:
            await complete("realtime", j["id"])
            break
        await complete("realtime", j["id"])


@pytest.mark.asyncio
async def test_complete_then_dequeue_does_not_return_same_job():
    tid = str(uuid.uuid4())
    job_id = await enqueue("realtime", tid, "once", {})
    seen: set[int] = set()
    j = await dequeue("realtime")
    while j and j["id"] != job_id:
        seen.add(j["id"])
        await complete("realtime", j["id"])
        j = await dequeue("realtime")
    assert j is not None and j["id"] == job_id
    await complete("realtime", job_id)

    next_j = await dequeue("realtime")
    while next_j is not None:
        assert next_j["id"] != job_id, "completed job was redelivered"
        await complete("realtime", next_j["id"])
        next_j = await dequeue("realtime")


@pytest.mark.asyncio
async def test_fail_releases_job_back_to_pool():
    tid = str(uuid.uuid4())
    job_id = await enqueue("realtime", tid, "retry", {})
    j = await dequeue("realtime")
    while j and j["id"] != job_id:
        await complete("realtime", j["id"])
        j = await dequeue("realtime")
    assert j is not None
    await fail("realtime", job_id, "boom")

    seen_again = False
    j2 = await dequeue("realtime")
    while j2 is not None:
        if j2["id"] == job_id:
            seen_again = True
            await complete("realtime", j2["id"])
            break
        await complete("realtime", j2["id"])
        j2 = await dequeue("realtime")
    assert seen_again
    async with SessionLocal() as s:
        r = await s.execute(
            text("SELECT last_error, attempts FROM control.queue_realtime WHERE id = :i"),
            {"i": job_id},
        )
        row = r.first()
        assert row is not None
        assert row.last_error == "boom"
        assert row.attempts >= 2


@pytest.mark.asyncio
async def test_unknown_queue_raises():
    with pytest.raises(ValueError):
        await enqueue("hazmat", "tid", "k", {})
