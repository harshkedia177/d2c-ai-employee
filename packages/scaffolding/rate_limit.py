"""Per-tenant per-source Redis token bucket."""

from __future__ import annotations

import asyncio
import time
from typing import ClassVar, cast

import redis as _redis_pkg
import redis.asyncio as _redis_asyncio
from redis import Redis as _SyncRedis
from redis.asyncio import Redis as _AsyncRedis

# Atomic acquire: returns 0 if a token was acquired, else seconds to wait.
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
        # Both clients hit the same Redis hash so sync and async callers
        # compete for tokens in the same bucket.
        self.r: _AsyncRedis = _redis_asyncio.from_url(redis_url, decode_responses=True)
        self.r_sync: _SyncRedis = _redis_pkg.from_url(redis_url, decode_responses=True)
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
        """Sync version of ``for_source`` for non-async callers."""
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
            loaded = await self.r.script_load(LUA)
            sha = cast("str", loaded)
            self._scripts[LUA] = sha
        return sha

    async def acquire(self) -> None:
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
            wait = float(cast("str", wait_raw))
            if wait == 0:
                return
            await asyncio.sleep(min(wait, 5.0))

    def acquire_sync(self, max_wait_s: float = 30.0) -> None:
        """Blocking acquire for sync callers; raises TimeoutError after max_wait_s."""
        sha = cast("str", self.r_sync.script_load(LUA))
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
            wait = float(cast("str", wait_raw))
            if wait == 0:
                return
            if time.time() + wait > deadline:
                raise TimeoutError(f"acquire_sync on {self.key} timed out after {max_wait_s:.1f}s")
            time.sleep(min(wait, 5.0))

    async def reset(self) -> None:
        await self.r.delete(self.key)

    async def close(self) -> None:
        await self.r.aclose()
