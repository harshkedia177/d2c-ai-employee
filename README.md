# D2C AI Employee

A working "AI employee" for Indian D2C brands. Underneath: three SaaS connectors hiding behind a single abstraction, a universal data model with row-level provenance on every fact, a chat planner mediated by a semantic layer (with a citation contract enforced at the regex level — the model is architecturally incapable of inventing a number), three autonomous agents sharing one `Agent` Protocol, and a per-tenant scale harness. The whole thing comes up end-to-end with a single `docker compose up`.

**Built for the Shiprocket take-home assignment, May 2026.**

> **The killer demo question this answers:** *"Net of RTO and Meta spend, am I making money on this campaign / SKU / pincode?"* — the question every Indian D2C founder asks, and currently answers with 30 minutes of Excel and a slightly heroic VLOOKUP.

---

## TL;DR

- **Connectors:** Shopify Admin + Meta Marketing + Shiprocket — picked because they're the minimum triangle to compute RTO-adjusted unit economics. RTO is the dominant rupee leak in Indian D2C: 28-35% on COD, COD is 60-70% of order volume. Razorpay was the runner-up; it lost to Shiprocket because Shopify already records gateway+amount+status, while **RTO is the leak only Shiprocket sees** — and this is an assignment for Shiprocket.
- **Universal Data Model:** Postgres, two layers (`raw.*` JSONB landing, `core.*` typed canonical). 9 mandatory provenance columns on every `core.*` row. Hash-partitioned by `tenant_id` from day one, so moving to per-cell sharding later is a partition move rather than an app rewrite. Multi-source merging via deterministic UUIDv5 `canonical_id` (tenant + entity + source_system + source_id), so a Shopify order and the Shiprocket shipment for that order resolve to the same `canonical_order_id` without a DB lookup.
- **Citation contract:** the LLM is *architecturally incapable* of typing a numeral. Numbers are placeholders rendered from `compute_metric()` tool returns. A regex `Verifier` catches any literal digit in the rendered draft and forces the planner to retry. After 2 retries: hard refuse with a numeral-free fallback. **All 8 must-refuse red-team prompts handled correctly** in CI evals.
- **Autonomous agents:** RTO Risk Flagger (webhook-triggered, the hero — ~₹13k/merchant/month savings, transparent weighted rule-stack with 3 bands), Meta Campaign Pauser (6h cron, post-RTO ROAS thresholded with learning-phase skip), Pincode COD Block Recommender (daily cron, top-20 ranked, `n>=20` cold-start guard). All three behind one `Agent` Protocol — same shape, swappable, all proposing not executing.
- **Scale harness:** per-tenant Redis Lua-atomic token bucket so onboarding waves don't trip third-party rate limits, Postgres-backed two-queue task system (realtime + backfill, prevents onboarding storms from starving live webhooks), non-blocking webhook ingress (median ~4ms in benchmark), 16 hash partitions on every fast-growing table.
- **223 pytest tests** including a parametrized eval harness over 12 golden + 10 red-team prompts. Citation contract enforced in CI.

---

## Quick start (one command)

```bash
cp .env.example .env        # set GEMINI_API_KEY=...
docker compose up           # builds, migrates, ingests, embeds, serves
```

When `bootstrap-1 exited (0)` and `chat_ui-1 ready in N ms` appear, open
**http://localhost:3000** — the demo tenant is pre-populated with ~2k orders,
~2k shipments, ad insights, agent runs, and 8 embedded few-shot examples.
No host-side Python/uv/Node required.

What the bring-up does, in order:
1. **postgres + redis + mock_saas** boot with healthchecks
2. **migrate** runs `alembic upgrade head` (idempotent)
3. **worker** starts draining `control.queue_realtime`
4. **bootstrap** pulls connector data → core via the worker, runs cron agents,
   embeds `core.few_shot_examples` via `gemini-embedding-001`, prints a summary
5. **api** (FastAPI :8000) and **chat_ui** (Next.js :3000) come up only after
   bootstrap exits 0 — so the recruiter never lands on an empty UI

Leave `GEMINI_API_KEY` blank to skip embedding seed and the live LLM call;
`search_examples` will fall back to substring overlap.

The Postgres volume persists across runs (`docker compose down` keeps data;
`docker compose down -v` wipes it). The bootstrap skips re-pulling /
re-embedding if data is already there, so subsequent `docker compose up`
calls are fast.

### Developer-mode (host-side python)

```bash
make install                # uv sync
docker compose up -d postgres redis mock_saas
make migrate
make test                   # 223 passing
uv run python scripts/bootstrap.py    # populate data + embeddings
uv run uvicorn packages.api.main:app --reload --port 8000
```

### Trigger a synthetic Shopify webhook against the RTO agent

```bash
curl -X POST http://localhost:8000/webhooks/shopify \
  -H 'X-Shopify-Topic: orders/create' \
  -H 'X-Shopify-Hmac-Sha256: <signed>' \
  -d @tests/fixtures/orders_create_cod.json
```

---

## Why these 3 connectors

The first thing I asked: which three connectors actually answer the founder's real question? Not "what's my GMV" — every dashboard does that — but *"net of RTO and Meta spend, am I making money on this SKU / campaign / pincode?"*. That one question shakes out three sources. Shopify gives you orders, COGS, and the COD-vs-prepaid split. Meta gives you spend per campaign and ad, with attributed conversions. Shiprocket gives you the AWB, courier, RTO status, freight, NDR — the leak nobody else can see.

Razorpay was the obvious runner-up and I genuinely went back and forth on it. It lost because Shopify already records gateway, amount, and status, while RTO is the dominant rupee leak in Indian D2C — 28-35% on COD, and COD is 60-70% of order volume. Swap Shiprocket for Razorpay and you can reconcile payments cleanly but you still can't answer the question that actually matters. And given this is an assignment *for* Shiprocket, the Shiprocket connector was table stakes either way.

## Why this universal schema

The vocabulary is source-agnostic on purpose — Segment Ecommerce shapes with Shopify-style field names — so when Magento or WooCommerce shows up next quarter, downstream consumers don't have to reshape. Just two layers: `raw.<source>_<stream>` (immutable JSONB landing, append-only, indexed on `(tenant_id, source_id, fetched_at desc)`) and `core.<entity>` (typed canonical, nine provenance columns on every row). No marts layer. Canonical plus on-demand SQL handles current query latency cleanly, and dragging dbt in before it actually hurts is the kind of premature scaffolding that ends up frozen in place.

Cross-source identity is where most warehouses get sloppy. A Shopify order and the Shiprocket shipment for that same order have different source IDs but represent the same business object. The resolution is deterministic: `canonical_id = uuid5(NAMESPACE_TENANT, f"{tenant_id}:{entity}:{source_system}:{source_id}")`, plus a per-tenant `xref` table for the cases where the only link is a merchant order number. Field-overwrite across sources is forbidden — one canonical `order`, Shopify authoritative for line items and totals, Shiprocket authoritative for shipment status and RTO. No "last write wins" surprises six months later.

Fast-growing tables (`raw.*`, `core.order`, `ad_spend_daily`, `agent_runs`) are hash-partitioned by `tenant_id` into 16 partitions from day one — so when this scales to per-cell sharding, it's a partition swap, not an app rewrite. **Rejected:** Singer-style stdio process boundary (no payoff at this scale), schema-per-tenant (Postgres falls over around 10k schemas), dbt marts layer (premature).

## Why this chat architecture

There are basically three ways to ground numbers in an LLM chat. You can have the LLM type inline `{{cite:...}}` tokens — the obvious choice and the one most products ship — and it enforces nothing, because the LLM forgets the format the moment things get interesting or invents row IDs that look right. You can do two-pass verification: draft, then rewrite. That's probabilistic, which is the wrong adjective for a financial number that a founder is about to make a decision on. The version that actually holds is to make the LLM architecturally incapable of typing a number at all. The planner emits placeholders like `{{m:gmv_0}}`. A deterministic `Renderer` substitutes the real value from `compute_metric()`. And a regex `Verifier` rejects any draft containing a literal digit that didn't come from a placeholder. Two reject-retries, then a hard refusal with zero numerals. The model can be sloppy; the wire format can't be.

Underneath that contract is a semantic layer — eight metrics defined exactly once in `metrics.yml` — rather than raw text-to-SQL. Spider 2.0 SOTA on raw text-to-SQL is around 21%, and *grounded wrongness* (a citation that points faithfully at the wrong rows because the LLM picked gross instead of net, or the wrong time grain) is worse than hallucination, because it earns the user's trust before it betrays it. The compiler enforces a citation projection on every metric query — `_source_system, _source_id, _source_record_url, _raw_table, _raw_row_id` are not optional columns, they're invariants. The whole contract is verified end-to-end in CI: 12 golden prompts cover every metric_id at least once, and 10 red-team "estimate / approximate / ballpark" prompts must either exhaust the verifier (`status=refused_verifier_exhausted`) or come back as a clean numeral-free refusal. (Refs: Anthropic Citations API blog; calculator-tool-only verification, arXiv 2512.12117; Wren AI / MAC-SQL / CHESS for semantic-layer-mediated tool use.)

## Why these 3 agents

RTO Risk Flagger is the hero. It's Shiprocket-rich (which makes it on-brand for this assignment), the rupee impact is large and easy to back out of an envelope — roughly ₹13k per merchant per month on a 600-COD-orders/mo brand with a 30% RTO rate, ₹240 per failed delivery, 60% surfacing rate, and 50% merchant acceptance — and the blast radius is tiny because it only ever *proposes* a WhatsApp confirm or a prepaid nudge. Nothing auto-acts. Meta Pauser and Pincode Blocker share the same `Agent` Protocol — one shape, three implementations — same play as the connector abstraction.

The score is a linear weighted sum with three bands (LOW under 0.25, MEDIUM 0.25-0.50, HIGH above 0.50), not XGBoost, and that's deliberate. An Indian D2C founder needs to *argue* with the score before they trust it. "Pincode 110084 is 34% RTO over 87 orders, customer has 1 RTO out of 2 prior orders, ₹2,400 cart, placed at 11pm" is something a human can poke at, agree with, or override. An XGBoost output isn't. Trading marginal accuracy for explainability is the right call when nobody trusts the agent yet — the moment a recommendation can't be justified to the founder, the agent is dead. All three agents share a single `agent_runs` table that's queryable from chat — *"show me what the RTO agent flagged today"* becomes `search_rows("agent_runs", ...)` for free.

## Why Gemini 3 Flash Preview + gemini-embedding-001 at full 3072 dims

Flash is the right shape for this hot path. A single chat turn traces the planner four-to-six times — `get_schema → search_examples → compute_metric → final draft`, with the occasional verifier-reject retry — so per-turn latency compounds, and at ~2-3 seconds per turn on Pro you'd be staring at a coffee-break loading spinner. Google positions Flash as the latency- and cost-optimized tier of the 3 family with near-Pro reasoning, around 3x faster than the 2.5 Pro generation ([Gemini 3 Flash launch post](https://blog.google/products-and-platforms/products/gemini/gemini-3-flash/)). Pricing lands at $0.50 / 1M input, $3.00 / 1M output — roughly ⅙ of Pro pricing and below the comparable-model average of $1.67 / $8.00 ([Artificial Analysis](https://artificialanalysis.ai/models/gemini-3-flash-reasoning), [VentureBeat coverage](https://venturebeat.com/technology/gemini-3-flash-arrives-with-reduced-costs-and-latency-a-powerful-combo-for)).

The killer fact for *this* particular app is on the **Berkeley Function Calling Leaderboard (BFCL v3) — the public benchmark closest to what the planner actually does — Gemini 3 Flash scores 78%, which outperforms Gemini 3 Pro on the same benchmark** ([BFCL leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html)). The distillation that makes Flash cheaper also seems to tighten the tool-calling distribution it draws from, and tool calls are exactly the cone our planner operates inside. Pair that with frontier-class reasoning numbers (GPQA Diamond 90.4%, MMMU Pro 81.2% via Artificial Analysis) and the historical "Pro for reliability, Flash for cost" trade-off is genuinely gone for Gemini 3. Flash is the principled choice here, not a compromise.

Whatever residual drift Flash still has — a wrong filter key, a guessed placeholder id — the citation contract absorbs structurally. Every numeral round-trips through `compute_metric → renderer → regex Verifier`. The compiler aliases generic time-filter keys to each metric's declared `time_column` so `date__gte` quietly becomes `placed_at__gte` when the metric is GMV. The planner echoes placeholder ids back to the model so it can quote them verbatim instead of guessing. Two reject-retries, then a hard numeral-free refusal. What reaches the user is either correctly cited or explicitly refused. And if a future deployment ever wants Pro, that's a one-line change in the `LLMClient` Protocol.

A few infrastructure choices fall out of this. Implicit caching is on by default for 2.5+ models — 90% discount on cached tokens, no storage cost ([context caching docs](https://ai.google.dev/gemini-api/docs/caching)) — and the stable prefix (tool descriptions + semantic-layer schema + few-shot examples) clears the 2,048-token threshold, so we get the discount without ever touching `cache_control` headers. Embeddings stay at full 3,072 dims via pgvector `halfvec` — the `vector` type's HNSW index caps at 2,000 dims while `halfvec` goes to 4,000 — which keeps everything in Postgres at ~50% storage savings versus full precision with negligible recall loss ([pgvector halfvec discussion](https://github.com/pgvector/pgvector/issues/461)).

**Rejected:** `gemini-2.5-pro` (legacy; the 3 family is a strict upgrade), truncating embeddings to 768 (we want full quality, and halfvec already solves the indexing), a separate vector DB (operational overhead with no payoff at this scale).

## Why this scale harness

At 10k merchants, the first thing that breaks isn't webhook volume — it's **rate-limit pressure on the third-party APIs during onboarding waves**. The Shiprocket API ([apidocs.shiprocket.in](https://apidocs.shiprocket.in/)) doesn't publish per-plan request quotas, which is itself the engineering problem: you can't pre-size against an SLA you can't see. The token expires every [240 hours (10 days)](https://support.shiprocket.in/support/solutions/articles/43000337456-shiprocket-api-document-helpsheet), and the order-fetch endpoint paginates rather than offering a dedicated bulk-export path. Put those three together and 50 simultaneous merchant onboardings — each backfilling 90 days of shipments through a paginated endpoint with refreshing tokens — is the failure mode worth defending against from day one. The per-tenant Redis Lua-atomic token bucket is exactly that defense, and the parametrized concurrency test proves the atomicity: spin up 5 workers against a 4-token bucket, exactly 4 acquire immediately, the 5th waits for refill. No spurious double-acquires under load.

The two-queue task system (realtime for webhooks and agent triggers, backfill for initial pull and daily catch-up — both Postgres-backed with `SELECT FOR UPDATE SKIP LOCKED`) is there for one specific failure mode: an onboarding storm starving live webhooks. With separate queues, a fresh merchant's 30k-row backfill can't push a real customer's order to the back of the line. Webhook ingress itself is non-blocking — write to `raw.shopify_webhook_inbox`, return 200, let a separate consumer drain — and clocks median ~4ms in a standalone benchmark. The hash partitions are pre-baked for cell-based sharding when the tenant count grows enough to need it.

## Where it breaks

See [`docs/eval-honesty.md`](docs/eval-honesty.md). Highlights:

- Cross-source attribution joins (Meta `utm_campaign` ↔ Shopify orders) are fragile — depends on merchant having UTMs configured. We degrade gracefully (zero attributed_revenue) but don't surface the misconfiguration in chat.
- Cold-start RTO scoring on first day for a new merchant: pincode → district → "low confidence" flag-for-review. Never auto-block.
- Semantic layer covers 8 metrics. Anything outside falls to the `run_sql` escape hatch (off by default), which works but loses the curated provenance shape.
- 10k-merchant load: never tested above 100. Harness exists; cluster doesn't.
- Webhook ordering edge case: two `orders/updated` events within the same second can clobber via the `updated_at` resolver.

## What we explicitly did NOT build

- **Marts layer (dbt-style)** — premature; canonical plus on-demand SQL is sufficient for the query latencies this system needs to hit.
- **Real OAuth flows** — sandbox / synthetic credentials for this submission. The same connector code targets prod by swapping `base_url`; the OAuth shim is a one-file addition that doesn't change anything else.
- **Auto-execution of writes** — the brief explicitly says no, and frankly it would be reckless without per-merchant tuning anyway. `propose_write(dry_run=True)` is the only path; calling with `dry_run=False` returns a clear error rather than executing.
- **Multi-currency normalization beyond INR** — Indian D2C focus by design.
- **A polished, production Next.js chat UI** — `POST /chat` works perfectly (returns rendered text + footnotes JSON), and the bundled `apps/chat-ui` is a thin shell over that endpoint that's enough to drive the demo. A dedicated run-log viewer with timeline scrubbing wasn't built; the run-log JSON is accessible via `/runs` and queryable from chat.

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
tests/              # 223 tests mirroring packages/* paths
```

## What "running" looks like

```
1. Connector reads          → raw.<source>_<stream>   (immutable JSONB, append-only)
2. Normalizer               → core.<entity>            (typed, with 9 provenance cols)
3. Webhook ingress          → realtime queue → Agent runs → core.agent_runs
4. Chat: planner → tools → semantic-layer SQL with citations → renderer → verifier → user
5. Each step is testable in isolation (223 tests prove it).
```

## Sources / prior art read for this design

- **Connector abstractions:** [singer-io spec](https://github.com/singer-io/getting-started/blob/master/docs/SPEC.md), [airbyte-python-cdk](https://github.com/airbytehq/airbyte-python-cdk), [dlt](https://dlthub.com/) — borrowed shape, dropped stdio boundary
- **Text-to-SQL SOTA:** [WrenAI](https://github.com/Canner/WrenAI), [MAC-SQL](https://arxiv.org/abs/2312.11242), [CHESS](https://arxiv.org/abs/2405.16755) — why semantic-layer-mediated, not raw
- **Citation grounding:** [Anthropic Citations API](https://claude.com/blog/introducing-citations-api), [calculator-tool-only verification (arXiv 2512.12117)](https://arxiv.org/abs/2512.12117)
- **Indian D2C economics:** [Edgistify — RTO the silent killer](https://www.edgistify.com/resources/blogs/rto-percentage-silent-killer-indian-d2c), [Inc42 RTO Playbook](https://inc42.com/resources/a-playbook-for-d2c-brands-to-tackle-rto-drive-growth/)
- **Multi-tenant scale:** [PlanetScale: tenancy in Postgres](https://planetscale.com/blog/approaches-to-tenancy-in-postgres), [AWS cell-based architecture](https://docs.aws.amazon.com/whitepapers/latest/saas-tenant-isolation-strategies/)
- **pgvector:** [halfvec for >2000 dims](https://github.com/pgvector/pgvector/issues/461), [Crunchy Data HNSW guide](https://www.crunchydata.com/blog/hnsw-indexes-with-postgres-and-pgvector)


## License & author

Built by Harsh Kedia (harsh@shoppin.app) for the Shiprocket take-home, May 2026.
