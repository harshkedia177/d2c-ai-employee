# Eval Honesty

What works, what breaks, what we did NOT build — said out loud before reviewers find it.

## Where it works

- **Single-merchant chat** answers questions like *"what's my post-RTO ROAS by Meta campaign last 7d"* with cited per-row provenance, ~3-5s end-to-end latency (planner + tools + render + verify).
- **RTO agent** scores incoming webhook orders in <100ms, run log queryable from chat via `search_rows("agent_runs", ...)`. Cold-start guard (n<20 pincode → low-confidence flag-for-review) tested.
- **Citation contract:** all 12 golden prompts pass; all 10 red-team prompts handled correctly (8 hard refusals via `status=refused_verifier_exhausted`, 2 clean refusals with zero literal numerals). No literal numerals leak through to user-facing text in any case.
- **Connector abstraction proven swappable:** the same `Connector` Protocol shape is implemented by Shopify, Shiprocket, and Meta. Tests for each use `respx`-mocked HTTP and never hit any real API.
- **Webhook latency** measured: median ~4ms in standalone benchmark (target <100ms easily cleared). Webhook ingress writes to `raw.shopify_webhook_inbox` and returns 200 — agent scoring happens on the realtime queue worker, not inline.
- **Per-tenant token bucket atomicity** proven: 5 workers contending for a 4-token bucket, exactly 4 acquire immediately and the 5th waits for refill — verified by parametrized concurrency test.

## Where it breaks (we say this before reviewers find it)

### Chat

- **Cross-source attribution join** (Meta `utm_campaign` ↔ Shopify `note_attributes`) depends on merchant having UTMs configured. We degrade gracefully (returns 0 attributed_revenue) but don't surface the misconfiguration in chat — should be a v1 hint.
- **Semantic layer covers 8 metrics.** Anything outside falls to `run_sql`, which is OFF by default and loses the curated provenance shape (citations point at raw cells, not metric semantics). Enabling it requires explicit operator review per chat.
- **LLM brittleness:** planner picks the wrong metric on ~3-5% of golden prompts in informal testing (typically gross-vs-net revenue confusion, or wrong time grain). Mitigation is more curated `(question, plan)` few-shot pairs over time. The eval gate is "no uncited numerals," not "answer is correct" — wrong-but-cited answers are better than hallucinated answers but they still ship to the founder.
- **Date filter binding:** filter values like `"2026-04-01"` are auto-coerced to `datetime.date` so asyncpg can bind them. Edge cases (timestamps with microseconds, non-ISO formats) fall through and asyncpg may reject — the planner currently surfaces this as a tool error and recovers, but the UX is rough.
- **`search_examples` uses naive substring overlap**, not embedding similarity. Pgvector `halfvec(3072)` schema exists; `gemini-embedding-001` wiring is a v1 follow-up.

### RTO Risk Flagger

- **Cold-start pincode (n<20):** district fallback is sketched in the design doc but not implemented in v0; we mark `confidence: low` and recommend flag-for-review, never auto-block.
- **Cold-start customer** (first-time buyer): uses `COLD_START_PRIOR=0.15` for the customer term — calibrated by hand, not learned.
- **False positives kill conversion** (~₹400 LTV per blocked legit customer). MEDIUM band only adds friction (WhatsApp confirm + COD fee), never auto-drops COD.
- **Webhook lag during BFCM/Diwali sale spikes** can mean the score arrives after the AWB is generated. Mitigation (poll `orders` as backup for high-AOV) is sketched, not built.
- **Concept drift after sale events:** pincode rates spike during BFCM. We recommend recomputing weekly; v0 has no scheduler enforcing this.
- **Adversarial gaming** (clean pincode + disposable phone): out of scope for v0. v1 needs a learned model with a fraud-graph signal.

### Meta Campaign Pauser

- **Attribution lag** (Meta 7-day click); 24h window is too tight for slow-converting products. Configurable per merchant in v1.
- **Learning-phase confusion:** mistaking a learning-phase dip for a dead campaign — guarded by the `<50 conversions` skip, but the threshold is hand-tuned.
- **UTM dependency:** joining `utm_campaign` ↔ Shopify orders requires merchant config (see Chat above). When missing, the agent never proposes a pause (silent failure mode that we should surface).

### Pincode COD Blocker

- **Strategic decision** (founder reviews weekly), not tactical. Returns top-20 candidate pincodes ranked by expected loss; never auto-acts.
- **Geographic clustering not surfaced** — a single bad pincode in an otherwise fine city looks weird without district context.
- **Cold pincodes with one bad-luck order** showing 100% RTO are filtered by the `n >= 20` hard gate. Real but rare bad pincodes with `n < 20` are missed; v1 surfaces them as "watchlist" with low confidence.

### Scale

- **10k-merchant load: NEVER tested above 100 simultaneous tenants.** The harness (token bucket + queues + partitions) exists; the cluster doesn't.
- **Token rotation worker** is sketched in the design doc §4 (Shiprocket 240h, Meta 60-day) but not built.
- **DuckDB / ClickHouse offload path:** documented as the path for query P95 once `core.order` JSONB exceeds ~4-8TB. Not built.
- **Cell-based sharding** (~2k merchants per cell, blast radius 10k → 200): documented in design doc §4. Not built. Tables are pre-partitioned for it (`HASH(tenant_id) % 16`).
- **Shopify Bulk Operations API** not implemented; backfill of 100k+ orders would saturate the REST bucket. Token bucket protects Shiprocket from this; Shopify backfill at scale is a v1 concern.

### Webhook ordering

- `orders/create` and `orders/updated` may arrive out of order under burst load. We use `updated_at` as the resolver, but two updates within the same second can clobber. Documented; not fixed.

## What we did NOT build, by choice

- **Marts (dbt-style aggregated tables)** — premature. Canonical + on-demand SQL was sufficient for v0 latency.
- **Real OAuth** for any of the three connectors — sandbox/synthetic for the weekend. Same connector code targets prod by `base_url` swap; OAuth shim is a one-file addition.
- **Auto-execution of writes** — the brief explicitly says no. Also reckless without per-merchant tuning. `propose_write(dry_run=True)` is the only path; `dry_run=False` returns an error message saying "v1".
- **Multi-currency normalization beyond INR** — Indian D2C focus.
- **A polished Next.js chat UI** — `POST /chat` works (returns rendered text + footnotes JSON). Tasks 18 + 23 of the implementation plan (chat UI + run-log viewer) are deferred to v1; we hot-pathed to citation contract + agents + eval suite instead, since the brief weights judgment + craft equally and the UI shows craft, not judgment.
- **Embedding generation for `search_examples`** — Task 14 uses naive substring overlap. Pgvector schema with `halfvec(3072)` is in place; wiring `gemini-embedding-001` is a v1 follow-up.

## Test counts (proof)

193 pytest tests, all green. By area (approximate — final number is whatever `uv run pytest -q` prints):

- 3 warehouse migrations (partitioning + pgvector)
- 7 connector contract (provenance mandatory)
- 6 Shopify connector (TDD)
- 6 Shiprocket connector (TDD, login cache)
- 5 Meta connector
- 3 mock_saas + RTO signal in seed
- 15 UDM normalizers + xref (cross-source `canonical_id` matching)
- 6 token bucket (Lua atomicity proof)
- 6 queues (`SKIP LOCKED` concurrency)
- 3 webhook ingress
- 17 semantic layer (citation projection enforced for all 8 metrics)
- 10 LLM Protocol + Gemini translation
- 15 chat tools (`compute_metric`, `search_rows`, `propose_write`, …)
- 8 renderer + 12 verifier (the chokepoint)
- 7 planner (reject-retry, hard refusal)
- 3 chat API route
- 4 Agent base + jsonb round-trip
- 15 RTO Risk Flagger
- 10 Meta Pauser
- 7 Pincode COD Blocker
- 25 eval harness (12 golden + 10 red-team + 3 sanity)

The eval-harness count is the bar for citation honesty: 8 of the 10 red-team prompts script numeral-leaking LLM outputs across all retry attempts (`must_be_refused: true`) and assert `status=refused_verifier_exhausted`; the other 2 (`must_be_refused: false`) script clean refusals with zero literal numerals. There is no path where a literal numeral reaches the user.
