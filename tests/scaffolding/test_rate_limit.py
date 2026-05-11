import asyncio
import os
import time
import uuid

import pytest

from packages.scaffolding.rate_limit import DEFAULT_RATES, TokenBucket

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.mark.asyncio
async def test_acquire_blocks_until_tokens_refill():
    """Acquire capacity tokens immediately, then the next acquire must wait."""
    key = f"test:{uuid.uuid4().hex}:shiprocket"
    b = TokenBucket(redis_url=REDIS_URL, key=key, refill_per_sec=1.0, capacity=2)
    await b.reset()
    try:
        await b.acquire()  # 1
        await b.acquire()  # 2
        start = time.time()
        await b.acquire()  # must wait ~1s
        elapsed = time.time() - start
        assert 0.7 < elapsed < 2.0, f"expected ~1s wait, got {elapsed:.2f}s"
    finally:
        await b.reset()
        await b.close()


@pytest.mark.asyncio
async def test_burst_within_capacity_does_not_block():
    """capacity tokens should be acquirable in <100ms total."""
    key = f"test:{uuid.uuid4().hex}:shopify"
    b = TokenBucket(redis_url=REDIS_URL, key=key, refill_per_sec=2.0, capacity=10)
    await b.reset()
    try:
        start = time.time()
        for _ in range(10):
            await b.acquire()
        elapsed = time.time() - start
        assert elapsed < 0.5
    finally:
        await b.reset()
        await b.close()


@pytest.mark.asyncio
async def test_two_buckets_with_different_keys_are_independent():
    """Tenant isolation: tenant A draining bucket doesn't affect tenant B."""
    k1 = f"test:{uuid.uuid4().hex}:t1"
    k2 = f"test:{uuid.uuid4().hex}:t2"
    a = TokenBucket(REDIS_URL, k1, refill_per_sec=1.0, capacity=2)
    b = TokenBucket(REDIS_URL, k2, refill_per_sec=1.0, capacity=2)
    await a.reset()
    await b.reset()
    try:
        await a.acquire()
        await a.acquire()
        # bucket b is fresh — should not block
        start = time.time()
        await b.acquire()
        await b.acquire()
        assert time.time() - start < 0.2
    finally:
        await a.reset()
        await b.reset()
        await a.close()
        await b.close()


@pytest.mark.asyncio
async def test_concurrent_workers_atomic_acquire():
    """4 concurrent coroutines, capacity=4, refill=very slow.
    Exactly 4 should succeed within 100ms; the 5th waits."""
    key = f"test:{uuid.uuid4().hex}:concurrent"
    bucket_factory = lambda: TokenBucket(  # noqa: E731
        REDIS_URL,
        key,
        refill_per_sec=0.1,
        capacity=4,
    )
    b0 = bucket_factory()
    await b0.reset()
    await b0.close()

    async def worker(name: int) -> tuple[int, float]:
        b = bucket_factory()
        try:
            t0 = time.time()
            await b.acquire()
            return (name, time.time() - t0)
        finally:
            await b.close()

    results = await asyncio.gather(*[worker(i) for i in range(5)])
    durations = sorted(d for _, d in results)
    # 4 fast (<100ms), 1 slow (>1s due to refill 0.1/s)
    assert all(d < 0.5 for d in durations[:4]), durations
    assert durations[4] > 1.0, durations

    cleanup = bucket_factory()
    await cleanup.reset()
    await cleanup.close()


@pytest.mark.asyncio
async def test_for_source_uses_default_rates():
    b = await TokenBucket.for_source(REDIS_URL, "t-test", "shiprocket")
    try:
        assert b.refill == DEFAULT_RATES["shiprocket"][0]
        assert b.capacity == DEFAULT_RATES["shiprocket"][1]
        assert b.key == "bucket:t-test:shiprocket"
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_for_source_rejects_unknown_source():
    with pytest.raises(ValueError):
        await TokenBucket.for_source(REDIS_URL, "t-test", "tiktok_ads")


def test_acquire_sync_blocks_until_tokens_refill():
    """3 sync acquires on a capacity=2, refill=1/s bucket: 2 immediate, 3rd waits ~1s."""
    key = f"test-sync:{uuid.uuid4().hex}:shiprocket"
    b = TokenBucket(
        redis_url=REDIS_URL,
        key=key,
        refill_per_sec=1.0,
        capacity=2,
    )
    try:
        b.acquire_sync()
        b.acquire_sync()
        start = time.time()
        b.acquire_sync()
        elapsed = time.time() - start
        assert 0.7 < elapsed < 2.0, f"expected ~1s wait, got {elapsed:.2f}s"
    finally:
        b.r_sync.delete(key)


def test_acquire_sync_times_out_if_max_wait_exceeded():
    key = f"test-sync-timeout:{uuid.uuid4().hex}:shiprocket"
    b = TokenBucket(
        redis_url=REDIS_URL,
        key=key,
        refill_per_sec=0.1,
        capacity=1,
    )
    try:
        b.acquire_sync()
        with pytest.raises(TimeoutError):
            b.acquire_sync(max_wait_s=2.0)
    finally:
        b.r_sync.delete(key)
