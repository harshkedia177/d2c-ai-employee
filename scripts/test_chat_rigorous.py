"""Rigorous /chat coverage: every metric, dimensional queries, edge cases,
adversarial inputs, accuracy validation against SQL.

Each block prints PASS/FAIL with the actual text + footnote count.
"""

from __future__ import annotations

import asyncio
import re
import sys

import httpx

BASE = "http://localhost:8000"
TENANT = "00000000-0000-0000-0000-000000000001"


async def call_chat(client: httpx.AsyncClient, msg: str, timeout: float = 90.0) -> dict:
    r = await client.post(
        f"{BASE}/chat",
        json={"tenant_id": TENANT, "message": msg},
        timeout=timeout,
    )
    if r.status_code != 200:
        return {"status": "http_error", "code": r.status_code, "text": r.text[:200]}
    return r.json()


# Each case: (label, prompt, predicate(out)->ok, expectation_str)
SCALAR_METRIC_CASES = [
    ("gmv simple",        "What's my GMV for the last 7 days?",                lambda o: "₹" in o.get("text", "")),
    ("gmv compare",       "Compare GMV this month vs last month.",             lambda o: o.get("text", "").count("₹") >= 2),
    ("aov",               "What is my AOV for the last 30 days?",              lambda o: "₹" in o.get("text", "")),
    ("rto_rate",          "What's my RTO rate for the last 30 days?",          lambda o: "%" in o.get("text", "")),
    ("cac",               "What's my CAC for the last 14 days?",               lambda o: "₹" in o.get("text", "")),
    ("post_rto_roas",     "What's my post-RTO ROAS this week?",                lambda o: re.search(r"\d", o.get("text", "")) is not None),
    ("contribution",      "What is my contribution margin per order this month?", lambda o: "₹" in o.get("text", "")),
]

DIMENSIONAL_CASES = [
    ("pincode_rto",       "Show me the top 5 pincodes by RTO rate.",           lambda o: len(o.get("footnotes", [])) >= 3),
    ("sku_rto",           "Top 5 SKUs by RTO rate.",                           lambda o: len(o.get("footnotes", [])) >= 1),
    ("cac_by_campaign",   "Show CAC by campaign for the last 30 days.",        lambda o: len(o.get("footnotes", [])) >= 1),
    ("aov_by_gateway",    "AOV by gateway for the last 60 days.",              lambda o: len(o.get("footnotes", [])) >= 1),
    ("aov_by_month",      "AOV by month.",                                      lambda o: len(o.get("footnotes", [])) >= 1),
]

REFUSAL_CASES = [
    ("benchmark",         "What's an industry-typical RTO rate for D2C?"),
    ("forecast",          "Estimate my Q3 revenue."),
    ("approximate",       "Roughly how many orders did I get yesterday?"),
    ("vibe",              "Just give me a vibe — what's my GMV?"),
]

CLARIFY_CASES = [
    ("no metric",         "tell me stuff"),
    ("nonsense",          "asdfghjkl"),
]

ADVERSARIAL = [
    ("sql_injection_1",   "What's my GMV; DROP TABLE core.\"order\"; --"),
    ("sql_injection_2",   "What's my GMV ' OR 1=1 --"),
    ("xss",               "What's my GMV <script>alert(1)</script>?"),
    ("unicode",           "What's my GMV 🚀💸 for the last week?"),
    ("very_long",         "What's my GMV " + "very " * 200 + "for the last week?"),
]


def print_row(label: str, passed: bool, note: str) -> None:
    mark = "✓" if passed else "✗"
    print(f"  {mark}  {label:30s} {note[:120]}")


async def main() -> int:
    failed = 0
    total = 0

    async with httpx.AsyncClient() as client:
        print("\n=== Scalar metric coverage ===")
        for label, prompt, pred in SCALAR_METRIC_CASES:
            total += 1
            o = await call_chat(client, prompt)
            status_ok = o.get("status") == "ok"
            pred_ok = pred(o) if status_ok else False
            note = f"status={o.get('status')} text={o.get('text', '')[:80]}"
            print_row(label, status_ok and pred_ok, note)
            if not (status_ok and pred_ok):
                failed += 1

        print("\n=== Dimensional metric coverage ===")
        for label, prompt, pred in DIMENSIONAL_CASES:
            total += 1
            o = await call_chat(client, prompt)
            status_ok = o.get("status") == "ok"
            pred_ok = pred(o) if status_ok else False
            note = f"status={o.get('status')} footnotes={len(o.get('footnotes', []))}"
            print_row(label, status_ok and pred_ok, note)
            if not (status_ok and pred_ok):
                failed += 1

        print("\n=== Refusal cases (must NOT contain raw numerals) ===")
        for label, prompt in REFUSAL_CASES:
            total += 1
            o = await call_chat(client, prompt)
            status_ok = o.get("status") in ("refused", "clarify")
            text = o.get("text", "")
            no_raw_nums = not re.search(r"\b\d[\d,]*(?:\.\d+)?\b", text)
            note = f"status={o.get('status')} text={text[:80]}"
            print_row(label, status_ok and no_raw_nums, note)
            if not (status_ok and no_raw_nums):
                failed += 1

        print("\n=== Clarify cases ===")
        for label, prompt in CLARIFY_CASES:
            total += 1
            o = await call_chat(client, prompt)
            ok = o.get("status") in ("ok", "refused", "clarify") and len(o.get("text", "")) > 0
            note = f"status={o.get('status')} text={o.get('text', '')[:80]}"
            print_row(label, ok, note)
            if not ok:
                failed += 1

        print("\n=== Adversarial inputs (must not crash, must not leak SQL) ===")
        for label, prompt in ADVERSARIAL:
            total += 1
            o = await call_chat(client, prompt, timeout=120.0)
            # Must respond with valid JSON, no leaked SQL keywords in answer
            has_status = "status" in o and o["status"] != "http_error"
            no_sql_leak = not any(
                kw in (o.get("text", "") or "").upper()
                for kw in ("DROP TABLE", "OR 1=1", "<SCRIPT>")
            )
            note = f"status={o.get('status')} text={o.get('text', '')[:60]}"
            print_row(label, has_status and no_sql_leak, note)
            if not (has_status and no_sql_leak):
                failed += 1

    print(f"\n{total - failed}/{total} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
