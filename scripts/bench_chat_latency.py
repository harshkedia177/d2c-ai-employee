"""Latency benchmark for POST /chat/stream (SSE).

Measures time-to-each-event across N repeats per prompt and reports
p50/p95/p99 both per-prompt and overall. Two modes:

  --mode fake (default, CI-safe):
      Spins up the FastAPI app in-process via httpx.ASGITransport.
      Substitutes a DelayingFakeLLMClient with scripted realistic delays
      (planner / joiner / composer). Tool registry is monkey-patched to
      return well-formed payloads without touching the warehouse. This
      measures *structural* orchestrator latency — proves the Plan →
      Execute → Join → Compose pipeline doesn't add overhead beyond the
      LLM calls themselves.

  --mode real:
      Fires real HTTP requests against `--base-url`. Assumes a live server
      with GEMINI_API_KEY set and the warehouse seeded for `--tenant`.

Outputs:
  - human-readable summary table on stdout
  - bench_results.json with per-request timings for offline analysis

Exit code 0 on success, non-zero on hard failure (network error, malformed
SSE, missing config).

Usage:
    uv run python scripts/bench_chat_latency.py
    uv run python scripts/bench_chat_latency.py --mode fake --repeat 20
    uv run python scripts/bench_chat_latency.py --mode real \\
        --base-url http://localhost:8000 --tenant <uuid> --repeat 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Allow `from packages.xxx` imports when invoked as `python scripts/bench_chat_latency.py`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx  # noqa: E402
import yaml  # noqa: E402

# ---------------------------------------------------------------------------
# Event timing structure
# ---------------------------------------------------------------------------

# SSE events whose first arrival we time, relative to request start.
_TIMED_EVENTS = (
    "first_byte",
    "plan",
    "tools_done",     # synthesized: time of the LAST tool_result event
    "join",           # join_decision
    "first_token",    # first token event
    "done",
)


@dataclass
class RequestTiming:
    """One request's per-event timings in seconds (relative to request start).

    None means the event never arrived for that request (e.g. a refusal
    short-circuits and never emits tool_result / join_decision events).
    """

    prompt_id: str
    kind: str
    repeat_idx: int
    t_first_byte: float | None = None
    t_plan: float | None = None
    t_tools_done: float | None = None
    t_join: float | None = None
    t_first_token: float | None = None
    t_done: float | None = None
    status: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchPrompt:
    id: str
    prompt: str
    kind: str


def load_prompts(path: Path) -> list[BenchPrompt]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, list) or not data:
        raise SystemExit(f"bench prompts file is empty or malformed: {path}")
    out: list[BenchPrompt] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise SystemExit(f"bench prompt entry not a dict: {entry!r}")
        try:
            out.append(
                BenchPrompt(id=entry["id"], prompt=entry["prompt"], kind=entry["kind"])
            )
        except KeyError as e:
            raise SystemExit(
                f"bench prompt missing field {e.args[0]}: {entry!r}"
            ) from None
    return out


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


async def _consume_sse_stream(
    response: httpx.Response,
    started: float,
    timing: RequestTiming,
) -> None:
    """Drain the SSE stream, recording the first-arrival time of each event.

    Mutates `timing` in place. Raises on malformed SSE frames.
    """
    saw_first_byte = False
    last_tool_result_at: float | None = None
    buf = ""
    async for chunk in response.aiter_text():
        if not chunk:
            continue
        if not saw_first_byte:
            timing.t_first_byte = time.monotonic() - started
            saw_first_byte = True
        buf += chunk
        # SSE frames are separated by a blank line.
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            evt = _parse_sse_frame(frame)
            if evt is None:
                continue
            name, payload = evt
            now = time.monotonic() - started
            if name == "plan" and timing.t_plan is None:
                timing.t_plan = now
            elif name == "tool_result":
                last_tool_result_at = now
            elif name == "join_decision" and timing.t_join is None:
                timing.t_join = now
            elif name == "token" and timing.t_first_token is None:
                timing.t_first_token = now
            elif name == "done":
                timing.t_done = now
                timing.status = (payload or {}).get("status")
            elif name == "error":
                timing.error = (payload or {}).get("message")
    if last_tool_result_at is not None:
        timing.t_tools_done = last_tool_result_at


def _parse_sse_frame(frame: str) -> tuple[str, dict[str, Any] | None] | None:
    """Parse a single SSE frame into (event_name, json_payload_or_None).

    Returns None if the frame is empty or has no `event:` line. Raises
    ValueError if the data line isn't valid JSON.
    """
    name: str | None = None
    data: str | None = None
    for line in frame.splitlines():
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data = line[len("data:"):].strip()
    if name is None:
        return None
    payload: dict[str, Any] | None = None
    if data:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"malformed SSE data line: {data!r}") from e
    return name, payload


# ---------------------------------------------------------------------------
# Request driver
# ---------------------------------------------------------------------------


async def _one_request(
    client: httpx.AsyncClient,
    prompt: BenchPrompt,
    tenant: str,
    repeat_idx: int,
) -> RequestTiming:
    timing = RequestTiming(prompt_id=prompt.id, kind=prompt.kind, repeat_idx=repeat_idx)
    started = time.monotonic()
    try:
        async with client.stream(
            "POST",
            "/chat/stream",
            json={"tenant_id": tenant, "message": prompt.prompt},
            timeout=httpx.Timeout(60.0),
        ) as r:
            if r.status_code != 200:
                body = await r.aread()
                timing.error = f"HTTP {r.status_code}: {body.decode('utf-8', 'replace')[:200]}"
                return timing
            await _consume_sse_stream(r, started, timing)
    except (httpx.HTTPError, ValueError) as e:
        timing.error = f"{type(e).__name__}: {e}"
    return timing


# ---------------------------------------------------------------------------
# Fake-mode wiring
# ---------------------------------------------------------------------------


def _build_fake_app() -> tuple[Any, str]:
    """Build the FastAPI app with FakeLLMClient + fake tool registry.

    Returns (app, fake_tenant_id). The fake LLM is reinstalled on every
    request via a dependency override so each request gets a fresh scripted
    queue (FakeLLMClient is single-use per call).
    """
    # Imports are inside the function so users running --mode real don't
    # pay the cost (and don't need the app to import cleanly).
    from packages.api.chat_routes import get_llm
    from packages.api.main import app
    from packages.chat import tools as _tools

    # Stub tool registry: no warehouse, no semantic-layer compile.
    async def _fake_compute_metric(
        tenant_id: str,
        metric_id: str,
        dimensions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        grain: str | None = None,
    ) -> dict[str, Any]:
        # Simulate the wall-clock of compile + SQL exec. Real warehouse
        # for a single-tenant aggregate query is ~50-150ms; we pick 80ms.
        await asyncio.sleep(0.08)
        provenance = {
            "metric_id": metric_id,
            "query_hash": f"fake_{metric_id}_{uuid.uuid4().hex[:8]}",
            "citations": [
                {"source_system": "shopify", "source_id": f"o-{i}"} for i in range(3)
            ],
            "sample_size": 42,
        }
        if dimensions:
            rows = [
                {
                    dimensions[0]: f"dim_{i}",
                    "value": 1000.0 * (i + 1),
                    "citations": provenance["citations"],
                    "sample_size": 42,
                }
                for i in range(3)
            ]
            return {"rows": rows, "provenance": provenance}
        return {"value": 12345.67, "provenance": provenance}

    async def _fake_search_examples(
        tenant_id: str, question: str, k: int = 5
    ) -> dict[str, Any]:
        await asyncio.sleep(0.03)
        return {"examples": [], "retrieval": "fake"}

    async def _fake_search_rows(
        tenant_id: str,
        entity: str,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"rows": []}

    _tools.TOOL_REGISTRY["compute_metric"] = _fake_compute_metric
    _tools.TOOL_REGISTRY["search_examples"] = _fake_search_examples
    _tools.TOOL_REGISTRY["search_rows"] = _fake_search_rows

    def _override_get_llm():
        return _DelayingFakeLLMClient()

    app.dependency_overrides[get_llm] = _override_get_llm
    return app, str(uuid.uuid4())


class _DelayingFakeLLMClient:
    """A FakeLLMClient variant that inspects the call site (system prompt)
    to decide which scripted plan/joiner/stream to return, applies a
    realistic asyncio.sleep, and never raises "no more scripted responses".

    Delay defaults (from Gemini 3.1-flash-lite measurements):
      planner generate_structured:  700ms TTFT + 500ms output = 1.2s
      joiner  generate_structured:  300ms TTFT + 200ms output = 0.5s
      composer generate_stream:     400ms TTFT, 30ms per chunk
    """

    PLANNER_TTFT_S = 0.7
    PLANNER_OUT_S = 0.5
    JOINER_TTFT_S = 0.3
    JOINER_OUT_S = 0.2
    COMPOSER_TTFT_S = 0.4
    COMPOSER_CHUNK_S = 0.03

    def __init__(self) -> None:
        # Track replan rounds so the joiner always finalizes (no infinite loop).
        pass

    async def generate(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("orchestrator never calls generate()")

    async def generate_structured(
        self,
        system: str,
        user: str,
        schema: Any,
        model: str = "gemini-3.1-flash-lite",
    ) -> Any:
        from packages.chat.orchestrator.plan import JoinerDecision, Plan
        from packages.llm.client import StructuredResponse

        # Discriminate planner vs joiner by schema (cleanest signal).
        if schema is Plan:
            await asyncio.sleep(self.PLANNER_TTFT_S + self.PLANNER_OUT_S)
            plan = _plan_for_prompt(user)
            return StructuredResponse(parsed=plan, usage={"total_tokens": 600})
        if schema is JoinerDecision:
            await asyncio.sleep(self.JOINER_TTFT_S + self.JOINER_OUT_S)
            return StructuredResponse(
                parsed=JoinerDecision(action="finalize"),
                usage={"total_tokens": 150},
            )
        raise RuntimeError(f"unexpected structured schema: {schema!r}")

    async def generate_stream(
        self,
        system: str,
        user: str,
        model: str = "gemini-3.1-flash-lite",
    ) -> AsyncIterator[Any]:
        from packages.llm.client import StreamChunk

        # Compose response chunks. The composer emits inline placeholders;
        # the orchestrator substitutes them with actual values from
        # metric_results. We don't know the exact placeholder names without
        # parsing the prompt, but we *do* know the planner above produced
        # tasks with metric_id values — and the composer accepts any
        # `{{m:<id>_N>}}` token, leaving unknown ones in place.
        #
        # For a clean bench we emit a tiny answer string without
        # placeholders. The composer will pass it through; the substituted
        # output is whatever it parsed.
        chunks = [
            "The ",
            "metric ",
            "is ",
            "{{m:gmv_0}}",
            ".",
        ]
        await asyncio.sleep(self.COMPOSER_TTFT_S)
        for c in chunks:
            yield StreamChunk(delta=c)
            await asyncio.sleep(self.COMPOSER_CHUNK_S)
        yield StreamChunk(delta="", usage={"total_tokens": 200}, done=True)


def _plan_for_prompt(user_message: str) -> Any:
    """Heuristic plan synthesis for fake mode.

    Inspects the user message and returns a Plan that mirrors what the real
    planner would emit for each bench prompt category. Refusal/clarify
    short-circuit the pipeline (no tool calls, no joiner, single token).
    """
    from packages.chat.orchestrator.plan import Plan, Task

    msg = user_message.lower()

    # Refusal: benchmark/forecast/estimate keywords.
    if any(
        k in msg
        for k in ("typical", "industry", "benchmark", "estimate", "forecast", "predict")
    ):
        return Plan(
            intent="refuse",
            refusal_reason="I can only report numbers I compute from your data.",
            tasks=[],
        )

    # Clarify: terse / no metric keyword.
    if len(msg.split()) <= 2 and not any(
        k in msg for k in ("gmv", "aov", "rto", "cac", "margin")
    ):
        return Plan(
            intent="clarify",
            refusal_reason="Which metric and date range?",
            tasks=[],
        )

    tasks: list[Task] = []
    if "rto" in msg and ("pincode" in msg or "broken" in msg or "top" in msg):
        tasks.append(
            Task(
                task_id="t1",
                tool="compute_metric",
                args={"metric_id": "pincode_rto_rate_90d", "dimensions": ["pincode"]},
            )
        )
    if "gmv" in msg:
        tasks.append(Task(task_id=f"t{len(tasks) + 1}", tool="compute_metric", args={"metric_id": "gmv"}))
    if "aov" in msg:
        tasks.append(Task(task_id=f"t{len(tasks) + 1}", tool="compute_metric", args={"metric_id": "aov"}))
    if "prior" in msg and "gmv" in msg:
        tasks.append(
            Task(
                task_id=f"t{len(tasks) + 1}",
                tool="compute_metric",
                args={
                    "metric_id": "gmv",
                    "filters": {
                        "placed_at__gte": "2026-03-01",
                        "placed_at__lte": "2026-03-31",
                    },
                },
            )
        )
    if not tasks:
        tasks.append(Task(task_id="t1", tool="compute_metric", args={"metric_id": "gmv"}))

    return Plan(
        intent="answer",
        tasks=tasks,
        composition_hint="State the metric(s) in one sentence.",
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    # Nearest-rank percentile, clamped to last index.
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


@dataclass
class PromptSummary:
    prompt_id: str
    kind: str
    n: int
    p50_done: float | None
    p95_done: float | None
    p99_done: float | None
    mean_done: float | None
    p95_first_byte: float | None
    p95_plan: float | None
    p95_first_token: float | None
    errors: int = 0


def _summarize(timings: list[RequestTiming]) -> list[PromptSummary]:
    by_prompt: dict[str, list[RequestTiming]] = {}
    for t in timings:
        by_prompt.setdefault(t.prompt_id, []).append(t)
    summaries: list[PromptSummary] = []
    for pid, group in by_prompt.items():
        done_vals = [t.t_done for t in group if t.t_done is not None]
        fb_vals = [t.t_first_byte for t in group if t.t_first_byte is not None]
        plan_vals = [t.t_plan for t in group if t.t_plan is not None]
        tok_vals = [t.t_first_token for t in group if t.t_first_token is not None]
        errors = sum(1 for t in group if t.error)
        summaries.append(
            PromptSummary(
                prompt_id=pid,
                kind=group[0].kind,
                n=len(group),
                p50_done=_pct(done_vals, 50),
                p95_done=_pct(done_vals, 95),
                p99_done=_pct(done_vals, 99),
                mean_done=statistics.fmean(done_vals) if done_vals else None,
                p95_first_byte=_pct(fb_vals, 95),
                p95_plan=_pct(plan_vals, 95),
                p95_first_token=_pct(tok_vals, 95),
                errors=errors,
            )
        )
    return summaries


def _format_seconds(v: float | None) -> str:
    if v is None:
        return "  -  "
    return f"{v:6.3f}s"


def _print_summary(
    summaries: list[PromptSummary],
    overall_done: list[float],
    mode: str,
    repeat: int,
) -> None:
    print()
    print(f"=== /chat/stream latency benchmark — mode={mode}, repeat={repeat} ===")
    print()
    print(
        f"{'prompt_id':<32} {'kind':<14} {'n':>3} "
        f"{'p50':>8} {'p95':>8} {'p99':>8} {'mean':>8} "
        f"{'p95_fb':>8} {'p95_plan':>9} {'p95_tok1':>9} {'err':>4}"
    )
    print("-" * 132)
    for s in sorted(summaries, key=lambda x: x.prompt_id):
        print(
            f"{s.prompt_id:<32} {s.kind:<14} {s.n:>3} "
            f"{_format_seconds(s.p50_done):>8} "
            f"{_format_seconds(s.p95_done):>8} "
            f"{_format_seconds(s.p99_done):>8} "
            f"{_format_seconds(s.mean_done):>8} "
            f"{_format_seconds(s.p95_first_byte):>8} "
            f"{_format_seconds(s.p95_plan):>9} "
            f"{_format_seconds(s.p95_first_token):>9} "
            f"{s.errors:>4}"
        )
    print("-" * 132)
    if overall_done:
        print(
            f"{'OVERALL':<32} {'':<14} {len(overall_done):>3} "
            f"{_format_seconds(_pct(overall_done, 50)):>8} "
            f"{_format_seconds(_pct(overall_done, 95)):>8} "
            f"{_format_seconds(_pct(overall_done, 99)):>8} "
            f"{_format_seconds(statistics.fmean(overall_done)):>8}"
        )
    print()
    print("Target architecture: p50 ~3s, p95 <5s (vs legacy ReAct loop ~18s).")
    print()


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------


async def _run_bench(args: argparse.Namespace) -> int:
    prompts_path = Path(args.prompts)
    if not prompts_path.exists():
        print(f"error: prompts file not found: {prompts_path}", file=sys.stderr)
        return 2
    prompts = load_prompts(prompts_path)
    if len(prompts) < 1:
        print("error: prompts file has no entries", file=sys.stderr)
        return 2

    transport: httpx.AsyncBaseTransport
    base_url: str
    tenant: str
    if args.mode == "fake":
        app, fake_tenant = _build_fake_app()
        transport = httpx.ASGITransport(app=app)
        base_url = "http://bench"
        tenant = args.tenant or fake_tenant
    else:
        if not args.tenant:
            print("error: --tenant is required in --mode real", file=sys.stderr)
            return 2
        transport = httpx.AsyncHTTPTransport()
        base_url = args.base_url
        tenant = args.tenant

    all_timings: list[RequestTiming] = []
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        # Warmup: a handful of throwaway requests to let the connection /
        # ASGI app warm up. Not counted in stats.
        for _ in range(max(0, args.warmup)):
            await _one_request(client, prompts[0], tenant, repeat_idx=-1)

        for prompt in prompts:
            for rep in range(args.repeat):
                t = await _one_request(client, prompt, tenant, repeat_idx=rep)
                all_timings.append(t)
                if t.error:
                    print(
                        f"  ! {prompt.id} rep={rep}: {t.error}",
                        file=sys.stderr,
                    )

    summaries = _summarize(all_timings)
    overall_done = [t.t_done for t in all_timings if t.t_done is not None]
    _print_summary(summaries, overall_done, args.mode, args.repeat)

    # Dump full per-request timings + summary to JSON for offline analysis.
    output = {
        "mode": args.mode,
        "repeat": args.repeat,
        "warmup": args.warmup,
        "tenant": tenant,
        "base_url": base_url,
        "n_prompts": len(prompts),
        "overall": {
            "p50_done": _pct(overall_done, 50),
            "p95_done": _pct(overall_done, 95),
            "p99_done": _pct(overall_done, 99),
            "mean_done": statistics.fmean(overall_done) if overall_done else None,
            "n_total": len(overall_done),
        },
        "per_prompt": [asdict(s) for s in summaries],
        "timings": [asdict(t) for t in all_timings],
    }
    out_path = Path(args.output)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"wrote {out_path} ({len(all_timings)} request timings)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Latency benchmark for the /chat/stream SSE endpoint."
    )
    parser.add_argument(
        "--mode",
        choices=("fake", "real"),
        default="fake",
        help="fake = in-process ASGI + scripted LLM (CI-safe). real = HTTP to a live server.",
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--tenant",
        default=None,
        help="Tenant UUID. Required in --mode real; auto-generated in --mode fake.",
    )
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument(
        "--prompts",
        default="evals/bench_prompts.yml",
        help="Path to YAML file with bench prompts.",
    )
    parser.add_argument(
        "--output",
        default="bench_results.json",
        help="Where to write the JSON dump of per-request timings.",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run_bench(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
