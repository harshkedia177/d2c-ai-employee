# scripts/

Operational scripts. All are runnable via `uv run python scripts/<name>.py`.

| script | purpose |
| --- | --- |
| `bootstrap.py` | One-shot end-to-end bootstrap (Postgres migrations + seed). |
| `seed_demo_tenant.py` | Seed a single demo tenant into the warehouse. |
| `seed_examples.py` | Embed `(question, plan)` few-shot examples into `core.few_shot_examples`. |
| `pull_demo_data.py` | Pull mock SaaS data into the warehouse via the ETL path. |
| `run_demo.py` | End-to-end demo runner. |
| `bench_chat_latency.py` | Latency benchmark for the `/chat/stream` SSE endpoint. |

---

## Measuring chat latency

The chat endpoint was rewritten from a ReAct loop (~18s p95) to a
`Plan -> Execute -> Join -> Compose` pipeline. The target is **p50 ~3s,
p95 <5s**. The bench script produces the numbers to prove or disprove
that claim.

### `scripts/bench_chat_latency.py`

Hits `POST /chat/stream` N times per prompt and reports p50/p95/p99 for
total wall-clock and per-event arrival times. Two modes:

#### Fake mode (default, CI-safe — no setup needed)

```bash
uv run python scripts/bench_chat_latency.py
# or with custom repeat:
uv run python scripts/bench_chat_latency.py --repeat 20 --warmup 2
```

What it does:
- Spins up the FastAPI app in-process via `httpx.ASGITransport`.
- Overrides `get_llm` with a `DelayingFakeLLMClient` that simulates
  realistic Gemini 3.1-flash-lite delays:
  - planner `generate_structured`: 700ms TTFT + 500ms output = 1.2s
  - joiner `generate_structured`:  300ms TTFT + 200ms output = 0.5s
  - composer `generate_stream`:    400ms TTFT, 30ms per chunk
- Stubs `TOOL_REGISTRY.compute_metric` / `search_examples` /
  `search_rows` so no warehouse round-trips happen. Each fake tool
  sleeps 30-80ms to mimic a single-tenant aggregate query.

This measures **structural orchestrator latency** — it proves the
Plan/Execute/Join/Compose pipeline doesn't add overhead beyond the LLM
calls themselves. It does NOT measure real Gemini latency variance,
warehouse cold-start, or network jitter.

Caveat: `httpx.ASGITransport` does not preserve fine-grained per-chunk
timing the way a real TCP socket does. In fake mode, `t_first_byte`,
`t_plan`, and `t_first_token` will often collapse to nearly the same
value because ASGITransport batches stream chunks. `t_done` (total
wall-clock) is the reliable signal.

#### Real mode (live server, real Gemini, real warehouse)

```bash
# Terminal 1: start the API server with a real key
GEMINI_API_KEY=... uv run uvicorn packages.api.main:app --port 8000

# Terminal 2: drive the bench
uv run python scripts/bench_chat_latency.py \
    --mode real \
    --base-url http://localhost:8000 \
    --tenant <seeded-tenant-uuid> \
    --repeat 10 \
    --warmup 2
```

Requirements:
- `GEMINI_API_KEY` exported on the server side.
- Warehouse seeded for `<tenant>` (run `scripts/seed_demo_tenant.py`
  first if you don't have one).
- The server listening at `--base-url` (no Docker timeouts in front).

### Interpreting the output

Two artefacts are produced per run:

1. **Stdout summary table** — one row per prompt with `p50_done`,
   `p95_done`, `p99_done`, plus a final OVERALL row across all requests.
2. **`bench_results.json`** — the per-request structured dump:
   ```json
   {
     "mode": "fake",
     "overall": { "p50_done": 2.347, "p95_done": 2.353, "p99_done": 2.362 },
     "per_prompt": [ { "prompt_id": "b01_gmv_simple", "p50_done": 2.349, ... } ],
     "timings": [ { "prompt_id": "...", "t_plan": 1.21, "t_done": 2.35, ... } ]
   }
   ```

Per-request fields (all seconds, relative to request start; `null` if
the event never fired):

| field | when |
| --- | --- |
| `t_first_byte` | First SSE byte received from the server. |
| `t_plan` | `event: plan` frame arrival. |
| `t_tools_done` | Last `event: tool_result` frame (synthesized). |
| `t_join` | `event: join_decision` arrival. |
| `t_first_token` | First `event: token` arrival (TTFT for compose). |
| `t_done` | `event: done` arrival — total wall-clock. |

Refusal and clarify prompts short-circuit before the executor and
joiner, so `t_tools_done` / `t_join` will be `null` for those rows.

### Verifying the architecture targets

Run fake mode against a clean checkout — this benchmarks only the
orchestrator + simulated LLM. The fake delays sum to ~2.5s for a
single-metric answer:
- planner 1.2s + (tool 0.08s + joiner 0.5s + composer TTFT 0.4s + ~10 chunks)
- ≈ 2.3s end-to-end, which matches the observed p95.

If real-mode p95 exceeds 5s, the additional latency is coming from
(a) Gemini variance, (b) warehouse query time, or (c) network — NOT
from the orchestrator's control-flow overhead.

### `evals/bench_prompts.yml`

The prompt set the harness runs. Eight prompts covering the latency
space (one_metric, two_metric, dimensional, refusal, clarify). Each
entry: `{id, prompt, kind}`. The `kind` column lets you group results
by complexity in the summary.

Add more prompts here as new metric shapes land — the harness picks
them up automatically.
