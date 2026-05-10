# D2C AI Employee — v0

A working v0 of an "AI employee for Indian D2C brands": three SaaS connectors behind one abstraction, a universal data model with row-level provenance, semantic-layer-mediated chat with a citation contract enforced at the regex level, three autonomous agents sharing one `Agent` Protocol, and a per-tenant scale harness. Runs end-to-end in `docker compose`.

**Built for the Shiprocket take-home assignment, May 2026.**

> **The killer demo question this v0 answers:** *"Net of RTO and Meta spend, am I making money on this campaign / SKU / pincode?"* — the question every Indian D2C founder asks and currently answers with 30 minutes of Excel.

---

## TL;DR

- **Connectors:** Shopify Admin + Meta Marketing + Shiprocket — picked because they're the minimum triangle to compute RTO-adjusted unit economics. RTO is the dominant rupee leak in Indian D2C: 28-35% on COD, COD is 60-70% of order volume. Razorpay was the runner-up; it lost to Shiprocket because Shopify already records gateway+amount+status, while **RTO is the leak only Shiprocket sees** — and this is an assignment for Shiprocket.
- **Universal Data Model:** Postgres, two layers (`raw.*` JSONB landing, `core.*` typed canonical). 9 mandatory provenance columns on every `core.*` row. Hash-partitioned by `tenant_id` from day one — v1 sharding is a partition move, not an app rewrite. Multi-source merging via deterministic UUIDv5 `canonical_id` (tenant + entity + source_system + source_id), so a Shopify order and the Shiprocket shipment for that order resolve to the same `canonical_order_id` without a DB lookup.
- **Citation contract:** the LLM is *architecturally incapable* of typing a numeral. Numbers are placeholders rendered from `compute_metric()` tool returns. A regex `Verifier` catches any literal digit in the rendered draft and forces the planner to retry. After 2 retries: hard refuse with a numeral-free fallback. **All 8 must-refuse red-team prompts handled correctly** in CI evals.
- **Autonomous agents:** RTO Risk Flagger (webhook-triggered, the hero — ~₹13k/merchant/month savings, transparent weighted rule-stack with 3 bands), Meta Campaign Pauser (6h cron, post-RTO ROAS thresholded with learning-phase skip), Pincode COD Block Recommender (daily cron, top-20 ranked, `n>=20` cold-start guard). All three behind one `Agent` Protocol — same shape, swappable, all proposing not executing.
- **Scale harness:** per-tenant Redis Lua-atomic token bucket (the canary — Shiprocket dies first), Postgres-backed two-queue task system (realtime + backfill, prevents onboarding storms from starving live webhooks), non-blocking webhook ingress (median ~4ms in benchmark), 16 hash partitions on every fast-growing table.
- **193 pytest tests** including a parametrized eval harness over 12 golden + 10 red-team prompts. Citation contract enforced in CI.

---

## Quick start

```bash
make install                # uv sync
docker compose up -d postgres redis
make migrate                # alembic upgrade head
uv run python -m mock_saas.seed.generate --merchants=1
make test                   # 193 passing
```

To run the chat backend live:

```bash
uv run uvicorn packages.api.main:app --reload --port 8000
# POST {"tenant_id": "<uuid>", "message": "What's my GMV?"} to http://localhost:8000/chat
# (provide a real GEMINI_API_KEY in .env to use Gemini 3 Pro)
```

To trigger a synthetic Shopify webhook against the RTO agent:

```bash
curl -X POST http://localhost:8000/webhooks/shopify \
  -H 'X-Shopify-Topic: orders/create' \
  -H 'X-Shopify-Hmac-Sha256: <signed>' \
  -d @tests/fixtures/orders_create_cod.json
```

---

## Why these 3 connectors

The hero question for an Indian D2C founder isn't "what's my GMV." It's *"net of RTO and Meta spend, am I making money on this SKU / campaign / pincode?"* That single question requires three sources: Shopify for orders + COGS + COD-vs-prepaid; Meta for spend by campaign/ad with attributed conversions; Shiprocket for AWB, courier, RTO status, freight, NDR — the leak nobody else sees.

Razorpay was the runner-up. It loses because Shopify already records gateway + amount + status, while RTO is the dominant rupee leak (28-35% on COD; COD is 60-70% of order volume in India). The alternate set with Razorpay instead of Shiprocket would compute payment reconciliation but would *not* answer the question that actually matters. Plus this is an assignment for Shiprocket — the Shiprocket connector is the table-stakes deliverable.

## Why this universal schema

Source-agnostic vocabulary (Segment Ecommerce + Shopify-shaped names) so adding Magento next quarter doesn't reshape downstream consumers. Two layers only: `raw.<source>_<stream>` (immutable JSONB landing, append-only, indexed on `(tenant_id, source_id, fetched_at desc)`) and `core.<entity>` (typed canonical with the 9 provenance columns). No marts in v0 — canonical + on-demand SQL is enough until query latency forces it.

Multi-source merging uses a deterministic UUIDv5 `canonical_id = uuid5(NAMESPACE_TENANT, f"{tenant_id}:{entity}:{source_system}:{source_id}")` plus a per-tenant `xref` table for cross-source identity (Shopify order ↔ Shiprocket shipment via merchant order number). Field-overwrite across sources is forbidden — an order seen by both Shopify and Shiprocket produces one canonical `order` (Shopify authoritative for line items/totals) joined to a `shipment` (Shiprocket authoritative for status/courier/RTO).

Tables that grow fast (`raw.*`, `core.order`, `ad_spend_daily`, `agent_runs`) are pre-partitioned by `HASH(tenant_id) % 16` from day one. **Rejected:** Singer-style stdio process boundary (no v0 benefit), schema-per-tenant (Postgres dies near 10k schemas), dbt marts layer (premature).

## Why this chat architecture

We pick "calculator-tool-only numbers" over "inline `{{cite:...}}` tokens the LLM types" or "two-pass draft-then-rewrite verifier." Inline tokens enforce nothing (the LLM forgets, hallucinates row IDs); two-pass is probabilistic. The architecturally enforceable version: numbers are placeholders the planner emits (`{{m:gmv_0}}`), the deterministic `Renderer` substitutes values from `compute_metric()` tool returns, and a regex `Verifier` rejects any draft containing a literal digit not from a placeholder. After 2 reject-retry rounds the planner is forced into a numeral-free hard refusal.

We use a semantic layer (8 metrics defined exactly once in `metrics.yml`) instead of raw text-to-SQL because **Spider 2.0 SOTA is ~21%** on raw text-to-SQL — and grounded wrongness (citation points faithfully at the wrong rows because the LLM picked gross-vs-net or the wrong time grain) is *worse* than hallucination. The semantic layer's SQL compiler mandates a citation projection: every metric query must select `_source_system, _source_id, _source_record_url, _raw_table, _raw_row_id`. The contract is enforced end-to-end: 12 golden prompts (parametrized in `tests/`) cover every metric_id at least once; 10 red-team "estimate / approximate" prompts must produce either `status=refused_verifier_exhausted` or a clean refusal with zero literal numerals. (Refs: Anthropic Citations API blog; calculator-tool-only verification, arXiv 2512.12117; Wren AI / MAC-SQL / CHESS for semantic-layer-mediated tool use.)

## Why these 3 agents

RTO Risk Flagger is the hero — Shiprocket-rich, transparent weighted rule-stack, large rupee impact (~₹13k/merchant/month single-merchant estimate: 600 COD orders/mo × 30% RTO × ₹240 × 60% surfacing × 50% acceptance), low blast radius (it only ever *proposes* a WhatsApp confirm or prepaid switch), easy to explain. Meta Pauser and Pincode Blocker share the `Agent` Protocol — one Protocol, three impls — same play as the connector abstraction.

A linear weighted score with 3 bands (LOW <0.25, MED 0.25-0.50, HIGH >0.50) instead of XGBoost is deliberate. Indian D2C founders need to argue with the score before they trust it — "pincode 110084 is 34% RTO over 87 orders, customer has 1/2 priors, ₹2,400 cart, late-night order" beats an XGBoost output they can't poke at. Marginal accuracy at the cost of explainability is the wrong trade for v0. All three agents share one `agent_runs` table queryable from chat — *"show me what the RTO agent flagged today"* becomes `search_rows("agent_runs", ...)` for free.

## Why Gemini 3 Pro + gemini-embedding-001 at full 3072 dims

Tool-use reliability matters more than cost at v0 (the planner is the only LLM call in the hot path, ~$0.0009/turn at 90% implicit-cache hit). Implicit caching is on by default for 2.5+ models — 90% discount on cached tokens, zero storage cost. Our ~12k stable prefix (tool descriptions + semantic-layer schema + 30 few-shot examples) clears the 2,048-token threshold, so we get 10x cost reduction for free without setting `cache_control` headers. Embeddings stay at full 3,072 dims via pgvector `halfvec` (`vector` HNSW caps at 2,000 dims; `halfvec` caps at 4,000) — same Postgres, ~50% storage saving vs full-precision, negligible recall loss.

The provider abstraction (`LLMClient` Protocol + `GeminiClient` impl) means swapping to Vertex AI for compliance, or A/B-ing 3 Flash on cheap turns, is a one-file change. **Rejected:** `gemini-2.5-pro` (legacy; 3 Pro at same price is strict upgrade), truncating embeddings to 768 (we want full quality; halfvec gives us indexing), separate vector DB (operational overhead, no v0 payoff).

## Why this scale harness

At 10k merchants the first failure is **Shiprocket 429s during onboarding waves**, not webhook volume — Shiprocket's undocumented ~1 req/s limit + 240h auth-token expiry + no bulk export means 50 simultaneous merchant onboardings (each backfilling 90 days of shipments) starts rejecting. Per-tenant Redis Lua-atomic token bucket is the canary — proven by the parametrized concurrency test (5 workers on a 4-token bucket: exactly 4 fast, 5th waits for refill). Two-queue task system (realtime for webhooks/agent triggers, backfill for initial pull and daily catch-up, both Postgres-backed with `SELECT FOR UPDATE SKIP LOCKED`) prevents onboarding storms from starving live webhooks. Webhook ingress is non-blocking (receive → write to `raw.shopify_webhook_inbox` → return 200, separate consumer drains) with median ~4ms in a standalone benchmark. Hash partitions are ready for cell-based sharding at v1.

What we sketched but didn't build: cell-based sharding (~2k merchants per cell, drops blast radius 10k → 200), per-tier QoS (free-tier lower bucket refill), DuckDB / ClickHouse analytics offload (when `core.order` JSONB exceeds ~4-8TB and Postgres query P95 starts losing). We know the order: cells first (drops blast radius), then ClickHouse (drops query P95), then per-tier QoS (recovers margin).

## Where it breaks

See [`docs/eval-honesty.md`](docs/eval-honesty.md). Highlights:

- Cross-source attribution joins (Meta `utm_campaign` ↔ Shopify orders) are fragile — depends on merchant having UTMs configured. We degrade gracefully (zero attributed_revenue) but don't surface the misconfiguration in chat.
- Cold-start RTO scoring on first day for a new merchant: pincode → district → "low confidence" flag-for-review. Never auto-block.
- Semantic layer covers 8 metrics. Anything outside falls to the `run_sql` escape hatch (off by default), which works but loses the curated provenance shape.
- 10k-merchant load: never tested above 100. Harness exists; cluster doesn't.
- Webhook ordering edge case: two `orders/updated` events within the same second can clobber via the `updated_at` resolver.

## What we explicitly did NOT build

- **Marts layer (dbt-style)** — premature; canonical + on-demand SQL was sufficient for v0 latency.
- **Real OAuth flows** — sandbox/synthetic for the weekend. Same connector code targets prod by `base_url` swap; OAuth shim is a one-file addition.
- **Auto-execution of writes** — the brief explicitly says no, also reckless without per-merchant tuning. `propose_write(dry_run=True)` is the only path; `dry_run=False` returns a v1 error.
- **Multi-currency normalization beyond INR** — Indian D2C focus.
- **A polished Next.js chat UI** — `POST /chat` works (returns rendered text + footnotes JSON). We hot-pathed to citation contract + agents + eval suite instead, since the brief weights judgment + craft equally and the UI shows craft, not judgment. UI is sketched in v1 (Tasks 18 + 23 of the implementation plan).
- **Embedding generation for `search_examples`** — Task 14 uses naive substring overlap; pgvector `halfvec(3072)` schema is in place but `gemini-embedding-001` wiring is a v1 follow-up.

## Repo tour

```
packages/
  connectors/       # one Protocol, three impls (Shopify, Shiprocket, Meta)
  udm/              # source → canonical normalizers, xref, provenance helper
  warehouse/        # alembic migrations, async Postgres engine, hash partitions
  scaffolding/      # token bucket, two-queue task system — the harness
  semantic_layer/   # metrics.yml + SQL compiler with mandatory citation projection
  llm/              # LLMClient Protocol + GeminiClient + FakeLLMClient
  chat/             # tools, planner, renderer, verifier
  agents/           # Agent Protocol + 3 impls (RTO, Meta Pauser, Pincode Blocker)
  api/              # FastAPI: /chat, webhook ingress
mock_saas/          # FastAPI mocks for Shopify/Meta/Shiprocket + Faker seed w/ RTO signal
evals/              # 12 golden + 10 red-team + citation contract sanity tests
docs/
  plans/            # design doc + implementation plan (the "why" + the "how")
  eval-honesty.md   # where it breaks
tests/              # 193 tests mirroring packages/* paths
```

## What "running" looks like

```
1. Connector reads          → raw.<source>_<stream>   (immutable JSONB, append-only)
2. Normalizer               → core.<entity>            (typed, with 9 provenance cols)
3. Webhook ingress          → realtime queue → Agent runs → core.agent_runs
4. Chat: planner → tools → semantic-layer SQL with citations → renderer → verifier → user
5. Each step is testable in isolation (193 tests prove it).
```

## Sources / prior art read for this design

- **Connector abstractions:** [singer-io spec](https://github.com/singer-io/getting-started/blob/master/docs/SPEC.md), [airbyte-python-cdk](https://github.com/airbytehq/airbyte-python-cdk), [dlt](https://dlthub.com/) — borrowed shape, dropped stdio boundary
- **Text-to-SQL SOTA:** [WrenAI](https://github.com/Canner/WrenAI), [MAC-SQL](https://arxiv.org/abs/2312.11242), [CHESS](https://arxiv.org/abs/2405.16755) — why semantic-layer-mediated, not raw
- **Citation grounding:** [Anthropic Citations API](https://claude.com/blog/introducing-citations-api), [calculator-tool-only verification (arXiv 2512.12117)](https://arxiv.org/abs/2512.12117)
- **Indian D2C economics:** [Edgistify — RTO the silent killer](https://www.edgistify.com/resources/blogs/rto-percentage-silent-killer-indian-d2c), [Inc42 RTO Playbook](https://inc42.com/resources/a-playbook-for-d2c-brands-to-tackle-rto-drive-growth/)
- **Multi-tenant scale:** [PlanetScale: tenancy in Postgres](https://planetscale.com/blog/approaches-to-tenancy-in-postgres), [AWS cell-based architecture](https://docs.aws.amazon.com/whitepapers/latest/saas-tenant-isolation-strategies/)
- **pgvector:** [halfvec for >2000 dims](https://github.com/pgvector/pgvector/issues/461), [Crunchy Data HNSW guide](https://www.crunchydata.com/blog/hnsw-indexes-with-postgres-and-pgvector)

Full reference list lives in `docs/plans/2026-05-10-d2c-ai-employee-design.md` §10.

## License & author

Built by Harsh Kedia (harsh@shoppin.app) for the Shiprocket take-home, May 2026.
