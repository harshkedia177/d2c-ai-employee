"""Rigorous endpoint smoke. One row per check, color-coded pass/fail.

Hits every advertised endpoint with at least one happy and one error case.
Stops on first hard infra failure (api down); records bugs as a list at the end.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

import httpx

BASE = "http://localhost:8000"
TENANT = "00000000-0000-0000-0000-000000000001"


@dataclass
class Check:
    name: str
    status: str  # "PASS" / "FAIL" / "WARN"
    detail: str = ""


@dataclass
class Result:
    checks: list[Check] = field(default_factory=list)


def add(r: Result, name: str, ok: bool, detail: str = "") -> None:
    r.checks.append(Check(name, "PASS" if ok else "FAIL", detail))


async def test_health(client: httpx.AsyncClient, r: Result) -> None:
    resp = await client.get(f"{BASE}/health")
    add(r, "GET /health -> 200 {'ok':true}", resp.status_code == 200 and resp.json().get("ok") is True, f"status={resp.status_code}")


async def test_tenants(client: httpx.AsyncClient, r: Result) -> None:
    resp = await client.get(f"{BASE}/tenants")
    ok = resp.status_code == 200 and len(resp.json().get("tenants", [])) >= 1
    add(r, "GET /tenants -> list with >=1 entry", ok, str(resp.json())[:80])


async def test_metrics(client: httpx.AsyncClient, r: Result) -> None:
    resp = await client.get(f"{BASE}/metrics")
    data = resp.json() if resp.status_code == 200 else {}
    n_metrics = len(data.get("metrics", []))
    n_dims = len(data.get("dimensions", []))
    add(r, "GET /metrics has metrics + dimensions", n_metrics >= 8 and n_dims >= 5, f"metrics={n_metrics} dims={n_dims}")


async def test_runs(client: httpx.AsyncClient, r: Result) -> None:
    resp = await client.get(f"{BASE}/runs?tenant_id={TENANT}&limit=5")
    add(r, "GET /runs -> 200 list", resp.status_code == 200 and "runs" in resp.json(), f"status={resp.status_code}")


async def test_chat_validation(client: httpx.AsyncClient, r: Result) -> None:
    # missing tenant_id -> 422
    resp = await client.post(f"{BASE}/chat", json={"message": "x"})
    add(r, "POST /chat missing tenant_id -> 422", resp.status_code == 422, f"status={resp.status_code}")
    # empty message -> 422 (min_length=1)
    resp = await client.post(f"{BASE}/chat", json={"tenant_id": TENANT, "message": ""})
    add(r, "POST /chat empty message -> 422", resp.status_code == 422, f"status={resp.status_code}")
    # oversize message -> 422 (max_length=4000)
    resp = await client.post(f"{BASE}/chat", json={"tenant_id": TENANT, "message": "x" * 4001})
    add(r, "POST /chat oversize message -> 422", resp.status_code == 422, f"status={resp.status_code}")
    # malformed JSON
    resp = await client.post(f"{BASE}/chat", content=b"{not json", headers={"Content-Type": "application/json"})
    add(r, "POST /chat malformed JSON -> 422", resp.status_code == 422, f"status={resp.status_code}")


async def test_chat_simple(client: httpx.AsyncClient, r: Result) -> None:
    resp = await client.post(
        f"{BASE}/chat",
        json={"tenant_id": TENANT, "message": "What's my GMV for the last 7 days?"},
        timeout=60.0,
    )
    if resp.status_code != 200:
        add(r, "POST /chat happy GMV -> 200", False, f"HTTP {resp.status_code}")
        return
    j = resp.json()
    ok = j.get("status") == "ok" and "₹" in j.get("text", "") and len(j.get("footnotes", [])) >= 1
    add(r, "POST /chat happy GMV: ok + ₹ + footnote", ok, f"status={j.get('status')} text[:60]={j.get('text', '')[:60]}")


async def test_chat_refusal(client: httpx.AsyncClient, r: Result) -> None:
    resp = await client.post(
        f"{BASE}/chat",
        json={"tenant_id": TENANT, "message": "What's an industry-typical RTO rate for D2C?"},
        timeout=60.0,
    )
    if resp.status_code != 200:
        add(r, "POST /chat refusal -> 200", False, f"HTTP {resp.status_code}")
        return
    j = resp.json()
    ok = j.get("status") in ("refused", "clarify") and len(j.get("footnotes", [])) == 0
    add(r, "POST /chat industry-benchmark -> refused/clarify, 0 footnotes", ok, f"status={j.get('status')}")


async def test_chat_stream(client: httpx.AsyncClient, r: Result) -> None:
    events: list[str] = []
    text = ""
    footnotes = 0
    async with client.stream(
        "POST",
        f"{BASE}/chat/stream",
        json={"tenant_id": TENANT, "message": "RTO rate last 30 days?"},
        timeout=120.0,
    ) as resp:
        if resp.status_code != 200:
            add(r, "POST /chat/stream -> 200", False, f"HTTP {resp.status_code}")
            return
        if "text/event-stream" not in resp.headers.get("content-type", ""):
            add(r, "POST /chat/stream content-type=text/event-stream", False, f"ct={resp.headers.get('content-type')}")
            return
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                name = None
                data = ""
                for line in frame.split("\n"):
                    if line.startswith("event:"):
                        name = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data += line[len("data:"):].strip()
                if name:
                    events.append(name)
                    if name == "token":
                        import json as _j
                        try:
                            text += _j.loads(data).get("text", "")
                        except Exception:
                            pass
                    elif name == "footnote":
                        footnotes += 1
    required = {"plan", "compose_start", "done"}
    add(r, "stream has plan + compose_start + done", required.issubset(events), f"events={events[:8]}")
    add(r, "stream emits at least one token", any(e == "token" for e in events), f"n_token={sum(1 for e in events if e == 'token')}")
    add(r, "stream has at least one footnote for metric q", footnotes >= 1, f"n_footnote={footnotes}")
    add(r, "stream ends with done", events[-1] == "done" if events else False, f"last={events[-1] if events else None}")


async def test_chat_provenance(client: httpx.AsyncClient, r: Result) -> None:
    # First produce a footnote
    resp = await client.post(f"{BASE}/chat", json={"tenant_id": TENANT, "message": "GMV last 7 days?"}, timeout=60.0)
    fns = resp.json().get("footnotes") or []
    if not fns:
        add(r, "GET /chat/provenance setup (need footnote)", False, "no footnote returned by /chat")
        return
    qh = fns[0].get("query_hash")
    if not qh:
        add(r, "GET /chat/provenance setup query_hash", False, "footnote missing query_hash")
        return
    resp2 = await client.get(f"{BASE}/chat/provenance/{qh}?tenant_id={TENANT}")
    add(r, "GET /chat/provenance/{qh} -> 200 rows", resp2.status_code == 200 and "rows" in resp2.json(), f"status={resp2.status_code}")
    # Wrong tenant -> 404
    resp3 = await client.get(f"{BASE}/chat/provenance/{qh}?tenant_id=00000000-0000-0000-0000-000000000099")
    add(r, "GET /chat/provenance wrong tenant -> 404", resp3.status_code == 404, f"status={resp3.status_code}")
    # Bogus hash -> 404
    resp4 = await client.get(f"{BASE}/chat/provenance/deadbeef?tenant_id={TENANT}")
    add(r, "GET /chat/provenance bogus hash -> 404", resp4.status_code == 404, f"status={resp4.status_code}")


async def test_triggers_webhook(client: httpx.AsyncClient, r: Result) -> None:
    resp = await client.post(f"{BASE}/triggers/webhook?tenant_id={TENANT}", timeout=30.0)
    ok = resp.status_code == 200 and resp.json().get("ok") is True and "raw_row_id" in resp.json()
    add(r, "POST /triggers/webhook -> 200 + raw_row_id", ok, f"status={resp.status_code}")


async def test_triggers_cron(client: httpx.AsyncClient, r: Result) -> None:
    for agent_id in ("pincode_cod_blocker", "meta_pauser"):
        resp = await client.post(f"{BASE}/triggers/cron/{agent_id}?tenant_id={TENANT}", timeout=30.0)
        ok = resp.status_code == 200 and "run_id" in resp.json()
        add(r, f"POST /triggers/cron/{agent_id} -> 200 + run_id", ok, f"status={resp.status_code}")
    # Unknown agent -> 400/404
    resp = await client.post(f"{BASE}/triggers/cron/nonexistent?tenant_id={TENANT}", timeout=10.0)
    add(r, "POST /triggers/cron/nonexistent -> 400/404", resp.status_code in (400, 404), f"status={resp.status_code}")


async def test_docs(client: httpx.AsyncClient, r: Result) -> None:
    resp = await client.get(f"{BASE}/docs")
    add(r, "GET /docs -> 200 swagger", resp.status_code == 200 and "swagger" in resp.text.lower(), f"status={resp.status_code}")
    resp = await client.get(f"{BASE}/openapi.json")
    add(r, "GET /openapi.json -> valid OpenAPI", resp.status_code == 200 and resp.json().get("openapi"), f"status={resp.status_code}")


async def main() -> int:
    r = Result()
    async with httpx.AsyncClient() as client:
        for fn in [
            test_health,
            test_tenants,
            test_metrics,
            test_runs,
            test_docs,
            test_chat_validation,
            test_chat_simple,
            test_chat_refusal,
            test_chat_stream,
            test_chat_provenance,
            test_triggers_webhook,
            test_triggers_cron,
        ]:
            try:
                await fn(client, r)
            except Exception as e:  # noqa: BLE001
                add(r, fn.__name__, False, f"EXC: {type(e).__name__}: {e}")

    pad = max(len(c.name) for c in r.checks)
    failed = 0
    for c in r.checks:
        mark = "✓" if c.status == "PASS" else "✗"
        print(f"  {mark}  {c.name.ljust(pad)}  {c.detail}")
        if c.status == "FAIL":
            failed += 1
    print(f"\n{len(r.checks) - failed} passed, {failed} failed out of {len(r.checks)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
