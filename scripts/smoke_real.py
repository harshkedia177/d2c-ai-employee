"""End-to-end smoke test against a running /chat/stream with real Gemini.

Fires a fixed prompt set, prints the full SSE event timeline per prompt,
the final composed text, footnote count, and wall-clock timings.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

BASE = "http://localhost:8000"
TENANT = "00000000-0000-0000-0000-000000000001"

PROMPTS = [
    ("Q1 simple metric",   "What's my GMV?"),
    ("Q2 date-filtered",   "What's my GMV for the last 7 days?"),
    ("Q3 two metrics",     "What's my GMV and AOV for the last 30 days?"),
    ("Q4 dimensional",     "Show me the top 5 pincodes by RTO rate."),
    ("Q5 pct format",      "What's my RTO rate for the last 30 days?"),
    ("Q6 refusal",         "What's an industry-typical RTO rate for D2C?"),
    ("Q7 cac",             "What's my CAC for the last 14 days?"),
    ("Q8 compare windows", "Compare my GMV this month vs last month."),
]


async def run_one(client: httpx.AsyncClient, label: str, prompt: str) -> dict:
    start = time.monotonic()
    timings: dict[str, float] = {}
    events: list[tuple[float, str]] = []
    final_text = ""
    footnotes: list[dict] = []
    status_out = "?"

    async with client.stream(
        "POST",
        f"{BASE}/chat/stream",
        json={"tenant_id": TENANT, "message": prompt},
        timeout=120.0,
    ) as r:
        if r.status_code != 200:
            return {"label": label, "error": f"HTTP {r.status_code}"}
        buf = ""
        async for chunk in r.aiter_text():
            if "t_first_byte" not in timings:
                timings["t_first_byte"] = time.monotonic() - start
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                if not frame.strip():
                    continue
                ev_name = None
                data_lines: list[str] = []
                for line in frame.split("\n"):
                    if line.startswith("event:"):
                        ev_name = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:"):].strip())
                if not ev_name or not data_lines:
                    continue
                try:
                    payload = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    continue
                t = time.monotonic() - start
                events.append((t, ev_name))
                if ev_name == "plan" and "t_plan" not in timings:
                    timings["t_plan"] = t
                elif ev_name == "tool_result":
                    timings["t_tools_done"] = t
                elif ev_name == "join_decision":
                    timings["t_join"] = t
                elif ev_name == "token":
                    if "t_first_token" not in timings:
                        timings["t_first_token"] = t
                    final_text += payload.get("text", "")
                elif ev_name == "footnote":
                    footnotes.append(payload.get("footnote", {}))
                elif ev_name == "done":
                    timings["t_done"] = t
                    status_out = payload.get("status", "?")

    return {
        "label": label,
        "prompt": prompt,
        "status": status_out,
        "text": final_text,
        "footnotes": len(footnotes),
        "timings": timings,
        "events": events,
    }


async def main() -> int:
    async with httpx.AsyncClient() as client:
        results = []
        for label, prompt in PROMPTS:
            print(f"\n{'='*78}\n{label}: {prompt!r}\n{'='*78}")
            r = await run_one(client, label, prompt)
            results.append(r)
            if "error" in r:
                print(f"  ERROR: {r['error']}")
                continue
            ev_seq = " → ".join(name for _, name in r["events"])
            print(f"  events: {ev_seq}")
            print(f"  status: {r['status']}  footnotes: {r['footnotes']}")
            t = r["timings"]
            print(
                f"  timings (s): first_byte={t.get('t_first_byte', -1):.2f}"
                f"  plan={t.get('t_plan', -1):.2f}"
                f"  tools_done={t.get('t_tools_done', -1):.2f}"
                f"  join={t.get('t_join', -1):.2f}"
                f"  first_token={t.get('t_first_token', -1):.2f}"
                f"  done={t.get('t_done', -1):.2f}"
            )
            print(f"  text: {r['text']}")
        print(f"\n{'='*78}\nSummary\n{'='*78}")
        for r in results:
            if "error" in r:
                print(f"  {r['label']:24s} ERROR: {r['error']}")
                continue
            done = r["timings"].get("t_done", -1)
            print(f"  {r['label']:24s} status={r['status']:8s} t={done:5.2f}s  footnotes={r['footnotes']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
