# Eval Honesty

The point of this doc is to say the hard parts out loud before a reviewer has to find them. Every system this size has rough edges; pretending otherwise is the fastest way to lose trust. Here's the honest tour.

## Where it works

- **Single-merchant chat** answers questions like *"what's my post-RTO ROAS by Meta campaign last 7d"* with cited per-row provenance in roughly 3-5 seconds end-to-end (planner + tools + render + verify). That's the headline path and it's solid.
- **RTO agent** scores incoming webhook orders in under 100ms, and the run log is queryable from chat via `search_rows("agent_runs", ...)`. The cold-start guard for low-volume pincodes (`n < 20` → low-confidence flag-for-review, never auto-block) is tested.
- **Citation contract** is the load-bearing claim, and it holds in CI: all 12 golden prompts pass, all 10 red-team prompts handled correctly — 8 of them via `status=refused_verifier_exhausted` after the planner couldn't produce a numeral-free draft, the other 2 as clean refusals with zero literal numerals. There's no test path where a literal numeral reaches the user.
- **Connector abstraction is genuinely swappable** — one `Connector` Protocol, three implementations (Shopify, Shiprocket, Meta), and every test uses `respx`-mocked HTTP so nothing ever hits a real API.
- **Webhook latency** measured: median ~4ms in a standalone benchmark, well under the 100ms target. Webhook ingress writes to `raw.shopify_webhook_inbox` and returns 200 immediately; agent scoring happens out-of-band on the realtime queue worker, never inline with the request.
- **Per-tenant token bucket atomicity** is proven, not just claimed: a parametrized concurrency test fires 5 workers at a 4-token bucket, exactly 4 acquire immediately and the 5th waits for refill. No spurious double-acquires under load.

## Where it breaks

### Chat

- **Cross-source attribution joins are fragile.** Meta `utm_campaign` ↔ Shopify `note_attributes` only works when the merchant has UTMs configured, which is depressingly often *not* the case in real Indian D2C catalogs. We degrade gracefully — attributed_revenue comes back as 0 instead of throwing — but the misconfiguration isn't surfaced to the user in chat. The right behavior is a chat-side hint along the lines of "this merchant has 0 UTM-tagged orders; CAC and ROAS will be unreliable," and that isn't built.
- **Semantic layer covers exactly 8 metrics.** Anything outside that set falls to the `run_sql` escape hatch, which is *off* by default. Turning it on works but loses the curated provenance shape — citations point at raw cells instead of metric semantics — so we gate it behind explicit operator review per chat rather than letting the LLM freelance arbitrary SQL.
- **LLM brittleness, told honestly.** In informal testing the planner picks the *wrong* metric on roughly 3-5% of golden prompts — usually gross-vs-net confusion or the wrong time grain. The fix over time is more curated `(question, plan)` few-shot pairs. The thing to internalize is that the eval gate is *"no uncited numerals,"* not *"answer is correct"* — a wrong-but-cited answer is structurally better than a hallucination (the founder can trace the citation back and discover the mistake), but it still ships. Wrong answers don't bypass the contract; they pass the contract while being wrong.
- **Date filter binding.** ISO-string date values for the canonical time fields (`placed_at`, `shipped_at`, `date`, etc.) get auto-coerced to `datetime.date` so asyncpg binds them cleanly, and the compiler aliases generic LLM-emitted keys (`date__gte`, `created_at__gte`) to each metric's declared `time_column`. So `date__gte` against the GMV metric quietly becomes `placed_at__gte`. Truly malformed strings — timestamps with microseconds, locale-formatted dates — still fall through to asyncpg and surface as a tool error. The planner recovers via retry but the UX during the recovery is rough.
- **`search_examples` uses pgvector halfvec cosine NN** with `gemini-embedding-001` at 3072 dims (HNSW index on `core.few_shot_examples`). Two failure modes degrade to substring overlap on the same examples file: no API key configured, or the table is empty. Both paths return the same shape so the planner can't tell which one it got.

### RTO Risk Flagger

The RTO agent is the hero of the system; it's also the one with the most operationally messy edges, because the cost of a false positive is real money lost to a legitimate customer.

- **Cold-start pincodes (`n<20`).** Real RTO data is too sparse to score confidently for brand-new pincodes. The design doc sketches a district fallback (aggregate over the broader district when the pincode itself doesn't have enough orders); the implementation marks `confidence: low` and recommends flag-for-review instead. Never auto-block.
- **Cold-start customer (first-time buyer).** The customer-history term uses a hand-tuned `COLD_START_PRIOR=0.15`. That's calibration by gut-feel, not by data. A learned prior is the right next step but it isn't here.
- **False positives are expensive.** Roughly ₹400 LTV per legit customer turned away. The MEDIUM band is deliberately soft — it adds a WhatsApp confirm and a small COD fee, it never auto-drops COD. The hard "switch to prepaid" suggestion only fires in HIGH band, and even then only as a proposal.
- **Webhook lag during BFCM/Diwali spikes.** Under burst load, the score can arrive *after* the AWB is generated, at which point the agent's recommendation is moot. A polling-fallback path for high-AOV orders is sketched in the design doc; not built.
- **Concept drift after sale events.** Pincode RTO rates spike during BFCM, which means a score trained on pre-sale data is wrong about post-sale risk. The recommendation is to recompute weekly; there's no scheduler enforcing it.
- **Adversarial gaming** (a fraudster using a clean pincode and a disposable phone number to defeat the score) is explicitly out of scope here. The defense is a learned model with a fraud-graph signal — a much bigger build than this assignment scopes.

### Meta Campaign Pauser

- **Attribution lag.** Meta's standard 7-day click window doesn't fit slow-converting products; a 24h post-RTO ROAS evaluation can pause a campaign that's actually still profitable on a 5-day delay. Per-merchant configuration of the window is the right fix and isn't surfaced.
- **Learning-phase confusion.** Mistaking a learning-phase dip for a dead campaign is a classic failure mode. The `<50 conversions` skip guard prevents the worst case, but the threshold is hand-tuned — not learned.
- **UTM dependency, again.** Joining `utm_campaign` ↔ Shopify orders requires merchant configuration (same root cause as the Chat section above). When UTMs are missing, the agent never proposes a pause — a silent failure mode that we should surface to the merchant rather than swallow.

### Pincode COD Blocker

- **This is a strategic decision, not a tactical one.** Founder reviews the top-20 candidate pincodes weekly. The agent ranks them by expected loss; it never auto-acts.
- **Geographic clustering isn't surfaced.** A single bad pincode in an otherwise healthy city looks weird without district context. The score is right; the presentation is incomplete.
- **`n >= 20` hard gate trades miss for safety.** Cold pincodes with one bad-luck order showing 100% RTO get filtered out — but so do real, rare-but-bad pincodes that legitimately have `n < 20`. The cleaner design surfaces those as a "watchlist" with low confidence rather than dropping them silently; that's not implemented.

### Scale

- **10k-merchant load has never actually been tested above 100 simultaneous tenants.** The harness — token bucket, two-queue task system, hash partitions — exists. The cluster to prove it at scale doesn't. This is the single biggest "we said it works" caveat in the system.
- **Token rotation worker.** The design doc §4 sketches it (Shiprocket tokens expire every 240 hours, Meta every 60 days). Not built — currently a token expiry would surface as a connector pull failure that an operator has to manually refresh.
- **DuckDB / ClickHouse offload.** The documented path for query P95 once `core.order` JSONB crosses ~4-8TB. Not built.
- **Cell-based sharding** (~2k merchants per cell, blast radius 10k → 200) is documented in §4 and the tables are pre-partitioned for it (`HASH(tenant_id) % 16`), but the cell router isn't.
- **Shopify Bulk Operations API** is not implemented, so a true 100k+-order backfill would saturate Shopify's REST bucket. The per-tenant token bucket gates outbound Shiprocket calls too, so the same backfill-storm pattern doesn't hit Shiprocket; the equivalent Shopify-side defense (Bulk Operations) is the missing piece.

### Webhook ordering

- `orders/create` and `orders/updated` can arrive out of order under burst load. We resolve via `updated_at` timestamp, but two updates within the same second can clobber each other. Documented, not fixed — fixing it cleanly probably means introducing a monotonic per-merchant sequence on the receiving side.

## What we did NOT build, by choice

These are conscious omissions, not oversights. Each one has a reason that's true at the scope of this submission.

- **Marts (dbt-style aggregated tables).** Premature. Canonical plus on-demand SQL handles the current query latencies cleanly, and adding a marts layer before it actually hurts ends up freezing in place. Worth revisiting if and when chat P95 starts losing.
- **Interactive OAuth (Partner Dashboard / Business Manager install flow).** Each connector authenticates with the platform's documented server-to-server credential — Shopify Custom App access token, Meta System User token, Shiprocket API user. The dual-mode `mock_saas → real API` switch is wired and exercised end to end. App-store-style OAuth (popup, scopes review, redirect URI handshake) is the missing piece if this were ever shipped as a multi-tenant Shopify App Store install; for an "AI employee" deployed per merchant the credential path is what production looks like.
- **Auto-execution of writes.** The brief explicitly says no, and frankly it'd be reckless without per-merchant tuning anyway. `propose_write(dry_run=True)` is the only path; calling with `dry_run=False` returns a clear error rather than executing.
- **Multi-currency normalization beyond INR.** Indian D2C is the focus, so the FX normalization layer would be theoretical work.
- **A polished, production Next.js chat UI.** `POST /chat` works perfectly and the bundled `apps/chat-ui` is a thin shell over it — enough to drive the demo end to end, but not a full product surface. A dedicated run-log viewer with timeline scrubbing wasn't built; the run-log JSON is accessible via `/runs` and queryable from chat.

## Test counts (proof)

227 pytest tests, all green. The breakdown below is approximate — the canonical number is whatever `uv run pytest -q` actually prints — but the shape is real. Note where the testing is heaviest: the chokepoint of the system (verifier + renderer + planner + eval harness) gets the most coverage, because that's where a regression hurts most.

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

The eval-harness count is the bar for citation honesty, and the construction matters. Eight of the ten red-team prompts script an LLM that *keeps* trying to leak numerals across every retry attempt (`must_be_refused: true`) — the test asserts the planner exhausts its retries and lands on `status=refused_verifier_exhausted` rather than letting a number through. The other two (`must_be_refused: false`) script a model that produces a clean refusal with zero literal numerals on the first try. Both paths are exercised in CI. There is no test path — and, by construction, no runtime path — where a literal numeral reaches the user.
