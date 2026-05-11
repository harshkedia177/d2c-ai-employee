"""Per-tenant per-source Redis token bucket.

Why Redis Lua: rate-limit decisions must be atomic across worker processes.
A read-then-write implementation lets two workers each acquire a token when
only one was available. Lua scripts execute atomically inside Redis, so
the check-and-decrement is a single critical section without external locks.

At 10k merchants the bucket key is `bucket:{tenant_id}:{source}`, so the
key cardinality is bounded at 10k × 5 sources = 50k. A single Redis node
handles this comfortably.
"""

from __future__ import annotations

import asyncio
import time
from typing import ClassVar

import redis as _redis_sync  # synchronous client; separate from redis.asyncio
import redis.asyncio as redis

# Atomic acquire script.
# Returns 0 if a token was acquired (call may proceed),
# or the number of seconds to sleep before the next attempt.
LUA = """
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens') or ARGV[1])
local last = tonumber(redis.call('HGET', KEYS[1], 'ts') or ARGV[3])
local now = tonumber(ARGV[3])
local refill = tonumber(ARGV[2])
local capacity = tonumber(ARGV[1])
tokens = math.min(capacity, tokens + (now - last) * refill)
if tokens >= 1 then
  tokens = tokens - 1
  redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
  return 0
else
  local wait = (1 - tokens) / refill
  return tostring(wait)
end
"""

# Per-source default rates. Add more as needed.
DEFAULT_RATES: dict[str, tuple[float, int]] = {
    # source: (refill_per_sec, capacity)
    "shopify": (2.0, 40),
    "shiprocket": (1.0, 2),  # very tight — Shiprocket undocumented limit
    "meta_ads": (10.0, 200),
}


class TokenBucket:
    _scripts: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        redis_url: str,
        key: str,
        refill_per_sec: float,
        capacity: int,
    ):
        self.r = redis.from_url(redis_url, decode_responses=True)
        # Sync client for code paths that aren't inside an event loop
        # (e.g. connectors making httpx.get calls). Both clients hit the
        # same Redis hash, so sync and async callers correctly compete for
        # tokens in the same bucket.
        self.r_sync = _redis_sync.from_url(redis_url, decode_responses=True)
        self.key = key
        self.refill = refill_per_sec
        self.capacity = capacity

    @classmethod
    async def for_source(
        cls,
        redis_url: str,
        tenant_id: str,
        source: str,
    ) -> TokenBucket:
        if source not in DEFAULT_RATES:
            raise ValueError(f"no default rate for source={source}")
        refill, capacity = DEFAULT_RATES[source]
        return cls(
            redis_url=redis_url,
            key=f"bucket:{tenant_id}:{source}",
            refill_per_sec=refill,
            capacity=capacity,
        )

    @classmethod
    def for_source_sync(
        cls,
        redis_url: str,
        tenant_id: str,
        source: str,
    ) -> TokenBucket:
        """Same as ``for_source`` but does not need to be awaited.

        Use this from sync code paths (e.g. connectors making httpx calls).
        """
        if source not in DEFAULT_RATES:
            raise ValueError(f"no default rate for source={source}")
        refill, capacity = DEFAULT_RATES[source]
        return cls(
            redis_url=redis_url,
            key=f"bucket:{tenant_id}:{source}",
            refill_per_sec=refill,
            capacity=capacity,
        )

    async def _ensure_script(self) -> str:
        sha = self._scripts.get(LUA)
        if sha is None:
            sha = await self.r.script_load(LUA)
            self._scripts[LUA] = sha
        return sha

    async def acquire(self) -> None:
        """Block until a token is acquired."""
        sha = await self._ensure_script()
        while True:
            wait_raw = await self.r.evalsha(
                sha,
                1,
                self.key,
                str(self.capacity),
                str(self.refill),
                str(time.time()),
            )
            wait = float(wait_raw) if isinstance(wait_raw, str) else float(wait_raw)
            if wait == 0:
                return
            await asyncio.sleep(min(wait, 5.0))

    def acquire_sync(self, max_wait_s: float = 30.0) -> None:
        """Synchronous acquire. Blocks the calling thread until a token is
        available or ``max_wait_s`` elapses.

        Used by sync code paths (connectors' httpx.get calls). The async
        ``acquire()`` above is for async callers. Both paths share the Lua
        script via Redis' SHA1 cache; tokens come from the same Redis hash
        so sync and async callers correctly compete for the same bucket.
        """
        sha = self.r_sync.script_load(LUA)  # idempotent; Redis dedupes by SHA1
        deadline = time.time() + max_wait_s
        while True:
            wait_raw = self.r_sync.evalsha(
                sha,
                1,
                self.key,
                str(self.capacity),
                str(self.refill),
                str(time.time()),
            )
            wait = float(wait_raw) if isinstance(wait_raw, str) else float(wait_raw)
            if wait == 0:
                return
            if time.time() + wait > deadline:
                raise TimeoutError(f"acquire_sync on {self.key} timed out after {max_wait_s:.1f}s")
            time.sleep(min(wait, 5.0))

    async def reset(self) -> None:
        """Drop the bucket state (test helper)."""
        await self.r.delete(self.key)

    async def close(self) -> None:
        await self.r.aclose()
