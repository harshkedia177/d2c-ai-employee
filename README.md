# D2C AI Employee

An AI employee for Indian D2C brands. Answers the one question the founder spends 30 minutes on every Monday morning — *"net of RTO and Meta spend, am I making money on this campaign / SKU / pincode?"* — with cited numbers, then proposes ₹-saving actions on its own.

---

## Quick start (one command)

```bash
cp .env.example .env       # set GEMINI_API_KEY=...
docker compose up          # builds, migrates, ingests, embeds, serves
# → open http://localhost:3000 when bootstrap-1 exits 0
```

The bring-up generates ~2k synthetic Shopify orders, ~2k Shiprocket shipments, 900 Meta ad insights, runs the cron agents, and embeds 8 few-shot examples via `gemini-embedding-001`. Postgres volume persists across runs; `down -v` wipes it.

Drop real `SHOPIFY_ACCESS_TOKEN` / `META_ACCESS_TOKEN` / `SHIPROCKET_EMAIL+PASSWORD` into `.env` and each connector flips from `mock_saas` to the real API on the next bring-up — see *Connecting real APIs* below.

---

## Architecture

```
┌─────────────────────┐   ┌──────────────────┐   ┌────────────────────┐
│  Shopify Admin REST │   │  Meta Marketing  │   │  Shiprocket /v1    │
│  (real or mock)     │   │  Graph API       │   │  /external/orders  │
└──────────┬──────────┘   └────────┬─────────┘   └──────────┬─────────┘
           │                       │                        │
           ▼                       ▼                        ▼
       ┌───────────────────────────────────────────────────────┐
       │   one  Connector  Protocol   (check / streams / read) │
       │   per-tenant Redis Lua-atomic  token bucket            │
       └───────────────────────────┬───────────────────────────┘
                                   │  (records + checkpoints)
                                   ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │  raw.*  JSONB landing (append-only, hash-partitioned, 16 buckets) │
   └────────┬───────────────────────────────────────────────────┬──────┘
            │                                                   │
            │  pull → enqueue                  webhook → inbox  │
            ▼                                                   ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  control.queue_realtime  +  control.queue_backfill              │
   │  (Postgres-backed, SELECT FOR UPDATE SKIP LOCKED)               │
   └────────────────────────────────┬────────────────────────────────┘
                                    │  worker
                                    ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  core.*  typed canonical, 9 NOT-NULL provenance cols per row    │
   │  deterministic UUIDv5 canonical_id (no DB-lookup joins)         │
   │  order · order_line · customer · product · shipment · refund    │
   │  campaign · ad_spend_daily · agent_runs · xref                  │
   └────┬───────────────────────────────────────────────────┬────────┘
        │                                                   │
        ▼ semantic layer                                    ▼ agents
   ┌──────────────────────────────────┐   ┌──────────────────────────────────┐
   │ metrics.yml: 8 metrics + 8 dims  │   │ Agent Protocol — 3 impls          │
   │ SQL compiler with MANDATORY      │   │ • RTO Risk Flagger  (webhook)     │
   │ citation projection              │   │ • Meta Pauser       (6h cron)     │
   └──────────────┬───────────────────┘   │ • Pincode COD Blocker (daily)     │
                  │                       │ propose-only → core.agent_runs    │
                  ▼                       └──────────────┬───────────────────┘
   ┌─────────────────────────────────────────────────┐  │
   │ chat orchestrator: Plan → Execute → Join → Compose│  │
   │                                                 │  │
   │  Planner LLM  (Gemini 3.1 Flash-Lite, JSON)     │  │
   │     ↓ typed Plan (DAG of Tasks with $N refs)    │  │
   │  Executor  asyncio.gather over compute_metric,  │  │
   │            search_examples, search_rows         │  │
   │     ↓ {task_id → result}                        │  │
   │  Joiner LLM  (always on, finalize | replan ×1)  │  │
   │     ↓                                           │  │
   │  Composer LLM  (streaming, inline {{m:..}})     │  │
   │     ↓ token / footnote / done SSE events        │  │
   │  Verifier  warns on uncited literal digits      │  │
   └──────────────┬──────────────────────────────────┘  │
                  │                                     │
                  ▼                                     ▼
       FastAPI :8000  /chat  /chat/stream  /runs  /webhooks/shopify
                  │
                  ▼
         Next.js :3000  chat_ui (SSE consumer)
```

One chat turn (SSE): planner emits a `Plan` of tool tasks → executor fans them out in parallel against the semantic layer → joiner inspects results (replan ≤1 if data missing) → composer streams tokens; placeholders are substituted inline so the wire emits already-cited text. Refusal (forecasts/benchmarks) and clarify (ambiguous) short-circuit before the executor. Hard timeouts: total `chat_total_timeout_s=60`, per-task `chat_per_task_timeout_s=15`, token budget `chat_request_token_budget=50_000`.

---

## 1. What I built (5-line summary)

- **Connectors:** Shopify Admin + Meta Marketing + Shiprocket behind one `Connector` Protocol. Real-API mode (documented auth + cursor pagination) or local `mock_saas` mode per connector, decided by which env vars are set.
- **UDM:** Two-layer Postgres — `raw.*` JSONB landing, `core.*` typed canonical. **9 provenance columns NOT NULL on every core row.** Cross-source identity via deterministic UUIDv5 `canonical_id`. Hash-partitioned by `tenant_id` from day one.
- **Chat:** LLMCompiler-shaped pipeline — **Plan → Parallel-Execute → Join → Stream Compose**. One planner LLM call emits a typed Pydantic DAG; deterministic executor fans out tool calls via `asyncio.gather`; joiner verifies (always on); composer streams tokens with inline placeholder substitution. p95 ~5s real-Gemini vs ~18s for the legacy ReAct loop. SSE endpoint `POST /chat/stream` streams events to the UI; `POST /chat` kept as JSON shim. Citation contract enforced architecturally — placeholders → renderer → regex `Verifier` → warning event on violation.
- **Agents:** 3 agents on one `Agent` Protocol (RTO Risk Flagger, Meta Pauser, Pincode COD Blocker). All propose-only. Every run persists `reasoning`, `score`, `band`, `expected_savings_inr`, and `cited_provenance` to `core.agent_runs`.
- **Scale harness:** Per-tenant Redis Lua-atomic token bucket, two-queue task system (realtime + backfill) with `SKIP LOCKED`, non-blocking webhook ingress (~4ms median), 16 hash partitions on every fast-growing table. 246 tests including an eval harness over 12 golden + 10 red-team prompts.

---

## 2. Connectors — why these 3

- **Shopify** — orders, COGS, COD-vs-prepaid split, customers.
- **Meta Marketing** — campaign/ad spend with attributed conversions.
- **Shiprocket** — AWB, courier, RTO status, freight, NDR. **The leak nobody else sees.**

Razorpay was the runner-up. It lost because Shopify already records gateway + amount + status, while **RTO is the dominant rupee leak in Indian D2C** (28–35% on COD, COD is 60–70% of order volume). Without Shiprocket you can reconcile payments but you can't answer the founder's real question. And this is an assignment for Shiprocket — the Shiprocket connector was table stakes either way.

One shared Protocol in `packages/connectors/base.py:67-81` (`check / streams / read`). Each connector is testable in isolation with `respx`-mocked HTTP. Real-mode auth matches the documented contract: Shopify `X-Shopify-Access-Token` header + Link-header pagination, Meta `access_token` query param + `paging.cursors.after` cursors, Shiprocket Bearer token from `POST /v1/external/auth/login` (240h TTL).

---

## 3. Schema — why this shape

- **Two layers, no marts:** `raw.<source>_<stream>` (immutable JSONB landing, append-only) → `core.<entity>` (typed canonical). Canonical + on-demand SQL handles current query latency; dragging in dbt before it actually hurts is the kind of scaffolding that freezes in place.
- **9 mandatory provenance columns on every `core.*` row** (`source_system, source_id, source_record_url, raw_table, raw_row_id, raw_payload_hash, fetched_at, ingested_at, connector_version`), 8 of 9 enforced `NOT NULL` at the schema layer. Source-agnostic vocabulary (Segment Ecommerce + Shopify-shaped names) — adding Magento next quarter doesn't reshape downstream consumers.
- **Cross-source identity is deterministic, not lookup-based:** `canonical_id = uuid5(NAMESPACE_TENANT, f"{tenant_id}:{entity}:{source_system}:{source_id}")`. A Shopify order and the Shiprocket shipment for that order resolve to the same `canonical_order_id` without a DB lookup. Per-tenant `xref` table for cases where the only link is the merchant order number. Field-overwrite across sources forbidden — Shopify authoritative for line items, Shiprocket authoritative for shipment status.
- **Hash-partitioned by `tenant_id` % 16 from day one** on `raw.*`, `core.order`, `core.ad_spend_daily`, `core.agent_runs`. Cell-based sharding becomes a partition move, not an app rewrite.

**Rejected:** Singer-style stdio process boundary (no v0 payoff), schema-per-tenant (Postgres dies near 10k schemas), marts layer (premature).

---

## 4. Chat — tool schema + how citation works

**7 tools exposed to the planner** (`packages/chat/tools.py`):

| Tool | Purpose |
|---|---|
| `get_schema` | Lists the 8 metrics + 8 dimensions defined in `metrics.yml`. |
| `search_examples` | Halfvec cosine NN over curated `(question, plan)` pairs; substring fallback. |
| `compute_metric` | THE chokepoint. Compiles a metric query with mandatory citation projection. |
| `search_rows` | Filter `core.*` rows for context. |
| `get_provenance` | Re-execute a cached query by `query_hash` to dump the cited source rows. |
| `run_sql` | Off-by-default escape hatch — read-only, operator-flagged. |
| `propose_write` | Write path, dry-run only. `dry_run=False` returns an error. |

**How citation works (orchestrator pipeline):**

1. **Planner** (Gemini 3.1 Flash-Lite, JSON-mode bound to a Pydantic `Plan` schema) emits a small DAG of `Task(tool, args)` — never raw numbers. The DAG supports `$task_id` refs so a downstream task can consume an upstream result.
2. **Executor** (deterministic, no LLM) runs tasks in topological waves via `asyncio.gather`. `compute_metric` is the chokepoint — every metric query compiles with mandatory citation projection.
3. **Joiner** (always on, per spec) inspects results and emits `finalize` or `replan` (capped at 1 replan). If the data is genuinely missing it asks the planner for a broader window; otherwise it finalizes.
4. **Composer** (streaming) emits `{{m:placeholder}}` tokens; the orchestrator buffers partial placeholders and substitutes them inline as they complete — the SSE wire never carries raw `{{m:..}}` to the client.
5. **Verifier** (`NUMERAL_RE = \b\d[\d,]*(?:\.\d+)?\b`) scans the final substituted text; any uncited literal digit emits a `warning` SSE event alongside the answer. The text is shipped because the client has already received it; the warning is the contract escalation.
6. **Proven by 12 golden + 10 red-team prompts** in `evals/citation_contract_test.py` — every YAML case is a `(Plan, JoinerDecision, compose_chunks)` triple; refusal cases short-circuit before tools.

We use a semantic layer (8 metrics) instead of raw text-to-SQL because Spider 2.0 SOTA is ~21% on raw text-to-SQL — and *grounded wrongness* (citation points faithfully at the wrong rows) is worse than hallucination. The compiler mandates that every metric query selects the 5 citation columns.

**Why this shape vs ReAct.** The earlier implementation was a ReAct loop: LLM emits tool calls, sees results, decides next move, loop. Each turn was a 2–5s round-trip and complex questions needed 3–4 turns, putting p95 around 18s. The orchestrator collapses that to **3 fixed LLM hops** (plan + join + compose) with the tool fan-out done deterministically between them — measured **p50 3.49s / p95 4.91s** against real Gemini in `scripts/bench_chat_latency.py --mode real`.

---

## 5. Agent — what it does, why this one

**RTO Risk Flagger** is the hero (webhook-triggered, the "AI employee" the brief asks for). Two other agents (Meta Pauser, Pincode COD Blocker) ship under the same `Agent` Protocol to prove the abstraction.

| Agent | Trigger | Watches | Proposes | ₹-saving rationale |
|---|---|---|---|---|
| **RTO Risk Flagger** | Shopify webhook | Incoming COD orders | `send_whatsapp_confirm` / `downgrade_to_prepaid` / `ship_as_is` | ~₹13k/merchant/month: 600 COD orders × 30% RTO × ₹240 × 60% surfacing × 50% acceptance |
| **Meta Pauser** | 6h cron | Post-RTO ROAS by campaign | `pause_campaign` / `reduce_budget_50` | Stops bleed on dead campaigns once attribution is RTO-adjusted |
| **Pincode COD Blocker** | Daily cron | Pincode-level RTO rates (`n>=20` gate) | `block_cod_pincode` for top-20 | Hard-blocks the worst offenders weekly with founder review |

**Score is a linear weighted sum with 3 bands (LOW <0.25 / MED 0.25–0.50 / HIGH >0.50), not XGBoost.** Indian D2C founders need to *argue* with the score before they trust it: *"pincode 110084 is 34% RTO over 87 orders, customer has 1 RTO out of 2 priors, ₹2,400 cart, 11pm"* beats an XGBoost output they can't poke at.

**Propose-only.** `make_run_log` hard-codes `dry_run: True`. No `httpx`/`requests` import anywhere in `packages/agents/`. Every run persists `reasoning`, `score`, `band`, `expected_savings_inr`, `evidence`, `cited_provenance` to `core.agent_runs` — queryable from chat as *"show me what the RTO agent flagged today."*

**Failure modes called out** in `eval-honesty.md`: cold-start pincode (`n<20` → flag-for-review, never auto-block), cold-start customer (hand-tuned prior), false-positive cost (~₹400 LTV), webhook lag during BFCM, adversarial gaming.

---

## 6. Scale — 1 → 10k merchants

**What breaks first:** rate-limit pressure on third-party APIs during onboarding waves, not webhook volume. Shiprocket doesn't publish per-plan request quotas, tokens expire every 240h, and there's no bulk-export endpoint — 50 simultaneous merchant onboardings each backfilling 90 days starts getting rejected.

**What absorbs it (in code, not just docs):**

- **Per-tenant Redis Lua-atomic token bucket** keyed `bucket:{tenant_id}:{source}`. Atomicity proven by a parametrized concurrency test: 5 workers, 4-token bucket → 4 acquire fast, 5th waits (`tests/scaffolding/test_rate_limit.py:69-97`).
- **Two-queue task system** (realtime + backfill, Postgres `SELECT FOR UPDATE SKIP LOCKED`). A fresh merchant's 30k-row backfill can't push a live webhook to the back of the line.
- **Non-blocking webhook ingress** — write to inbox, return 200 in ~4ms median, separate consumer drains.
- **HASH(tenant_id) % 16 partitions** on every fast-growing table. Cell-based sharding becomes a partition move, not an app rewrite.

**What's sketched but not built** (honest list): cell router (~2k merchants/cell, blast radius 10k → 200), DuckDB/ClickHouse offload (`core.order` JSONB beyond ~4–8TB), Shopify Bulk Operations, token rotation worker (Shiprocket 240h, Meta 60d), per-tier QoS.

**10k merchants was never tested above ~100 simultaneous tenants.** Harness exists; cluster doesn't. Called out up front in `eval-honesty.md`.

---

## 7. Eval — where it breaks

See [`eval-honesty.md`](./eval-honesty.md). Top items the reviewer should hear from me first:

- **Planner picks the wrong metric on ~3–5% of golden prompts** in informal testing (typically gross-vs-net confusion, or wrong time grain). The eval gate is *"no uncited numerals,"* not *"answer is correct."*
- **Cross-source attribution joins** (Meta `utm_campaign` ↔ Shopify orders) depend on merchant having UTMs configured. Degrades gracefully (zero attributed revenue), doesn't surface the misconfiguration in chat.
- **10k load never proven.** Harness in code, cluster not.
- **Webhook lag at BFCM** can deliver an RTO score *after* AWB generation. Backup polling path sketched, not built.

---

## 8. Hours / AI tool disclosure

- **Time:** built across the May 10 → May 14 window in 3–4 working sessions.
- **AI tools:** Claude / GPT-class models used heavily as a pair-programmer. I drove all architecture decisions (which 3 connectors, two-layer UDM, citation contract enforced via regex verifier, propose-only agents, harness shape) and the eval/test strategy. The LLM wrote most of the implementation under tight per-file review. The README + eval-honesty are my framing; LLM helped tighten prose. Commit history is the receipt.

---

## 9. What I'd do with another week

1. Run real load — 1k synthetic tenants, measure p95 chat latency + worker queue depth under concurrency (single-tenant p95 is already measured: 4.91s real-Gemini, see `scripts/bench_chat_latency.py`).
2. Build the **cell router** (2k merchants/cell). Hash partitions are ready for it.
3. **Embedding-based metric disambiguation** to fix the gross-vs-net confusion — store metric definitions as embeddings, pick the closest match instead of relying on the LLM's prior.
4. **Gemini prompt caching** — pad the planner + composer system prompts past the 4096-token implicit-cache minimum to shave another 30–50% off TTFT.
5. **Token rotation worker** (Shiprocket 240h, Meta 60d).
6. **Per-merchant tuning UI** for agent thresholds — the linear-weighted-score design only works if the founder can tune it.
7. **Bulk Operations** for Shopify backfill.

---

## What I explicitly did NOT build

- **Marts layer (dbt-style)** — premature.
- **Interactive App-Store-style OAuth flows** — credential-based auth (Shopify Custom App token, Meta System User token, Shiprocket API user) is what production looks like for a per-merchant deployment.
- **Auto-execution of writes** — brief says no. `propose_write(dry_run=False)` errors out.
- **Multi-currency beyond INR** — Indian D2C focus.

---

## Connecting real APIs

Each connector decides its own mode by inspecting which env vars are set. Mixing is supported (real Shopify + mock Meta + real Shiprocket all work).

```bash
# Shopify Admin REST
SHOPIFY_SHOP_DOMAIN=your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_xxxxxxxxxxxxxxxxxxxxxx
SHOPIFY_WEBHOOK_SECRET=xxxxx            # turns on HMAC verification

# Meta Marketing API
META_ACCESS_TOKEN=EAAxxxxxxxx
META_AD_ACCOUNT_ID=act_1234567890

# Shiprocket (Settings → API → Configure → API user)
SHIPROCKET_EMAIL=apiuser@yourbrand.in
SHIPROCKET_PASSWORD=xxxxxxxx
```

Real-mode contract per source:

- **Shopify** — `https://<shop>.myshopify.com/admin/api/<v>/orders.json`, `X-Shopify-Access-Token` header, `Link: <…>; rel="next"` cursor pagination ([docs](https://shopify.dev/docs/api/admin-rest)). Webhook ingress HMAC-verifies per [Shopify's documented scheme](https://shopify.dev/docs/apps/build/webhooks/subscribe/https#step-3-verify-the-webhook).
- **Meta** — `https://graph.facebook.com/v19.0/act_<id>/insights`, `paging.cursors.after` cursor.
- **Shiprocket** — `POST /v1/external/auth/login` → Bearer (240h, [auth docs](https://support.shiprocket.in/support/solutions/articles/43000337456-shiprocket-api-document-helpsheet)), `GET /v1/external/orders` with `page` / `per_page`.

---

## Repo tour

```
packages/
  connectors/       # one Protocol, three impls (Shopify, Shiprocket, Meta)
  udm/              # source → canonical normalizers, xref, provenance helper
  warehouse/        # alembic migrations, async Postgres engine, hash partitions
  scaffolding/      # token bucket + two-queue task system — the harness
  semantic_layer/   # metrics.yml + SQL compiler with mandatory citation projection
  llm/              # LLMClient Protocol + GeminiClient + FakeLLMClient
  chat/             # tools, renderer, verifier
  chat/orchestrator/  # plan, planner, executor, joiner, composer, events, budgets
  agents/           # Agent Protocol + 3 impls
  api/              # FastAPI: /chat, /chat/stream (SSE), /runs, /webhooks/shopify
mock_saas/          # FastAPI mocks for Shopify/Meta/Shiprocket + Faker seed w/ RTO signal
apps/chat-ui/       # Next.js 16 / React 19 chat surface; SSE consumer
evals/              # 12 golden + 10 red-team + bench prompts + citation contract harness
tests/              # 246 tests mirroring packages/* paths
```
