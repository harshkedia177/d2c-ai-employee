"""SSE edge-case probes: abort mid-stream, concurrent streams, trace_id propagation."""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

BASE = "http://localhost:8000"
TENANT = "00000000-0000-0000-0000-000000000001"


def mark(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'}  {name:48s} {detail}")
    return ok


async def test_trace_id_propagation() -> bool:
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"{BASE}/chat/stream",
            json={"tenant_id": TENANT, "message": "GMV last 7 days?"},
            timeout=60.0,
        ) as r:
            trace_ids: set[str] = set()
            buf = ""
            async for chunk in r.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    for line in frame.split("\n"):
                        if line.startswith("data:"):
                            try:
                                d = json.loads(line[len("data:"):].strip())
                                if "trace_id" in d:
                                    trace_ids.add(d["trace_id"])
                            except Exception:
                                pass
    return mark(
        "trace_id same across all events that carry one",
        len(trace_ids) == 1 and len(next(iter(trace_ids))) == 12,
        f"distinct={len(trace_ids)} sample={next(iter(trace_ids), None)}",
    )


async def test_abort_mid_stream() -> bool:
    """Open stream, drop the connection after the first event. The server
    should NOT crash the api process — and a follow-up health check must
    still return 200. We don't directly observe server-side cleanup but
    health-after-abort is a strong signal."""
    cancelled_before_done = False
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST",
                f"{BASE}/chat/stream",
                json={"tenant_id": TENANT, "message": "Top pincodes by RTO rate?"},
                timeout=30.0,
            ) as r:
                async for _chunk in r.aiter_text():
                    cancelled_before_done = True
                    break  # abort after first chunk
        except Exception:
            pass

    # Health check still 200?
    async with httpx.AsyncClient() as client:
        h = await client.get(f"{BASE}/health", timeout=5.0)
    return mark(
        "abort mid-stream: api still healthy",
        cancelled_before_done and h.status_code == 200,
        f"aborted={cancelled_before_done} health={h.status_code}",
    )


async def test_concurrent_streams() -> bool:
    """Fire 5 streams in parallel; all should complete with a 'done' event."""
    async def one(prompt: str) -> bool:
        async with httpx.AsyncClient() as client:
            done_seen = False
            async with client.stream(
                "POST",
                f"{BASE}/chat/stream",
                json={"tenant_id": TENANT, "message": prompt},
                timeout=120.0,
            ) as r:
                buf = ""
                async for chunk in r.aiter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        frame, buf = buf.split("\n\n", 1)
                        for line in frame.split("\n"):
                            if line.strip() == "event: done":
                                done_seen = True
            return done_seen

    prompts = [
        "What's my GMV last 7 days?",
        "What's my RTO rate this month?",
        "What's my CAC last 14 days?",
        "Top 5 pincodes by RTO rate?",
        "Show me AOV by gateway last 30 days?",
    ]
    t0 = time.monotonic()
    results = await asyncio.gather(*[one(p) for p in prompts])
    elapsed = time.monotonic() - t0
    return mark(
        "5 concurrent streams all reach 'done'",
        all(results),
        f"n={sum(results)}/5 in {elapsed:.1f}s",
    )


async def test_invalid_tenant() -> bool:
    """Tenant that's a valid UUID but doesn't exist — should still answer
    (no rows, just 'no data') without crashing."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE}/chat",
            json={
                "tenant_id": "00000000-0000-0000-0000-deadbeef0000",
                "message": "What's my GMV?",
            },
            timeout=60.0,
        )
    j = r.json() if r.status_code == 200 else {}
    return mark(
        "non-existent tenant returns 200 (no data path)",
        r.status_code == 200 and j.get("status") in ("ok", "clarify", "refused"),
        f"status={r.status_code} app_status={j.get('status')}",
    )


async def test_streaming_content_type() -> bool:
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"{BASE}/chat/stream",
            json={"tenant_id": TENANT, "message": "GMV?"},
            timeout=60.0,
        ) as r:
            ct = r.headers.get("content-type", "")
            cc = r.headers.get("cache-control", "")
            xab = r.headers.get("x-accel-buffering", "") or r.headers.get("X-Accel-Buffering", "")
            # drain
            async for _ in r.aiter_text():
                pass
    return mark(
        "stream content-type=text/event-stream",
        "text/event-stream" in ct,
        f"ct={ct!r} cc={cc!r} xab={xab!r}",
    )


async def main() -> int:
    failed = 0
    for fn in [
        test_streaming_content_type,
        test_trace_id_propagation,
        test_abort_mid_stream,
        test_invalid_tenant,
        test_concurrent_streams,
    ]:
        try:
            if not await fn():
                failed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            mark(fn.__name__, False, f"EXC {type(e).__name__}: {e}")
    print(f"\n{5 - failed}/5 passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
