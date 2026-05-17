import uuid

import pytest
from sqlalchemy import text

from packages.chat.tools import (
    TOOL_REGISTRY,
    TOOL_SCHEMAS,
    compute_metric,
    get_provenance,
    get_schema,
    propose_write,
    run_sql,
    search_examples,
    search_rows,
)
from packages.warehouse.db import SessionLocal


async def _insert_order(tenant_id: str, total: float = 1000.0) -> str:
    canonical_id = str(uuid.uuid4())
    async with SessionLocal() as s:
        await s.execute(
            text("""
              INSERT INTO core."order" (
                tenant_id, canonical_id, placed_at, status, gateway,
                total, currency, shipping_pincode,
                source_system, source_id, source_record_url,
                raw_table, raw_row_id, raw_payload_hash,
                fetched_at, ingested_at, connector_version
              ) VALUES (
                :t, :c, '2026-05-01T00:00:00Z', 'paid', 'razorpay',
                :total, 'INR', '560001',
                'shopify', :sid, 'https://shop.example.com/admin/orders/test',
                'raw.shopify_orders', 1, 'h',
                now(), now(), 'shopify@0.1.0'
              )
            """),
            {"t": tenant_id, "c": canonical_id, "total": total, "sid": canonical_id[:8]},
        )
        await s.commit()
    return canonical_id


@pytest.fixture(autouse=True)
async def _cleanup_test_orders():
    yield
    async with SessionLocal() as s:
        await s.execute(
            text(
                'DELETE FROM core."order" '
                "WHERE source_record_url = 'https://shop.example.com/admin/orders/test' "
                "AND ingested_at < now() - interval '5 minutes'"
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_get_schema_returns_metrics_and_dimensions():
    s = await get_schema(tenant_id="t1")
    metric_ids = {m["id"] for m in s["metrics"]}
    assert "gmv" in metric_ids and "post_rto_roas" in metric_ids
    dim_ids = {d["id"] for d in s["dimensions"]}
    assert "campaign" in dim_ids and "pincode" in dim_ids


@pytest.mark.asyncio
async def test_compute_metric_returns_value_with_provenance():
    tid = str(uuid.uuid4())
    await _insert_order(tid, total=999.50)
    res = await compute_metric(tenant_id=tid, metric_id="gmv")
    assert res["value"] == 999.5
    p = res["provenance"]
    assert p["metric_id"] == "gmv"
    assert p["query_hash"]
    assert len(p["citations"]) >= 1
    assert p["citations"][0]["url"] == "https://shop.example.com/admin/orders/test"
    assert p["sample_size"] == 1


@pytest.mark.asyncio
async def test_compute_metric_with_dimension_returns_rows():
    tid = str(uuid.uuid4())
    await _insert_order(tid, total=500)
    res = await compute_metric(tenant_id=tid, metric_id="gmv", dimensions=["gateway"])
    assert "rows" in res
    assert res["rows"][0]["gateway"] == "razorpay"
    assert res["rows"][0]["value"] == 500.0
    assert "citations" in res["rows"][0]


@pytest.mark.asyncio
async def test_compute_metric_with_date_filter_string_is_coerced():
    tid = str(uuid.uuid4())
    await _insert_order(tid, total=400)
    res = await compute_metric(
        tenant_id=tid,
        metric_id="gmv",
        filters={"placed_at__gte": "2026-04-01"},
    )
    assert res["value"] == 400.0


@pytest.mark.asyncio
async def test_get_provenance_reexecutes_prior_query():
    tid = str(uuid.uuid4())
    await _insert_order(tid, total=300)
    res = await compute_metric(tenant_id=tid, metric_id="gmv")
    h = res["provenance"]["query_hash"]
    p2 = await get_provenance(tenant_id=tid, query_hash=h)
    assert p2["metric_id"] == "gmv"
    assert len(p2["rows"]) == 1


@pytest.mark.asyncio
async def test_get_provenance_rejects_other_tenant():
    tid = str(uuid.uuid4())
    other = str(uuid.uuid4())
    await _insert_order(tid, total=300)
    res = await compute_metric(tenant_id=tid, metric_id="gmv")
    h = res["provenance"]["query_hash"]
    p2 = await get_provenance(tenant_id=other, query_hash=h)
    assert "error" in p2


@pytest.mark.asyncio
async def test_search_rows_filters_by_tenant():
    tid = str(uuid.uuid4())
    other = str(uuid.uuid4())
    await _insert_order(tid, total=200)
    out = await search_rows(tenant_id=tid, entity="order", limit=10)
    assert all(str(r["tenant_id"]) == tid for r in out["rows"])

    out2 = await search_rows(tenant_id=other, entity="order", limit=10)
    assert out2["rows"] == []


@pytest.mark.asyncio
async def test_search_rows_rejects_unknown_entity():
    with pytest.raises(ValueError):
        await search_rows(tenant_id="t1", entity="not_a_thing")


@pytest.mark.asyncio
async def test_run_sql_disabled_by_default():
    out = await run_sql(tenant_id="t1", sql="SELECT 1")
    assert "error" in out


@pytest.mark.asyncio
async def test_run_sql_blocks_writes_when_enabled():
    with pytest.raises(ValueError):
        await run_sql(tenant_id="t1", sql='DELETE FROM core."order"', enable=True)


@pytest.mark.asyncio
async def test_search_examples_returns_relevant_results():
    out = await search_examples(tenant_id="t1", question="what's my GMV last 30 days")
    assert len(out["examples"]) >= 1
    assert "gmv" in out["examples"][0]["question"].lower() or any(
        "gmv" in str(call.get("args", {})) for call in out["examples"][0]["plan"]
    )


@pytest.mark.asyncio
async def test_propose_write_dry_run_returns_diff():
    out = await propose_write(
        tenant_id="t1",
        action_type="downgrade_to_prepaid",
        payload={"order_id": "ord-1", "reason": "high RTO risk"},
    )
    assert out["dry_run"] is True
    assert out["action_type"] == "downgrade_to_prepaid"


@pytest.mark.asyncio
async def test_propose_write_rejects_non_dry_run():
    out = await propose_write(
        tenant_id="t1",
        action_type="downgrade_to_prepaid",
        payload={},
        dry_run=False,
    )
    assert "error" in out
    assert "v1" in out["error"]


@pytest.mark.asyncio
async def test_propose_write_rejects_unknown_action():
    out = await propose_write(
        tenant_id="t1",
        action_type="launch_missile",
        payload={},
    )
    assert "error" in out


@pytest.mark.asyncio
async def test_search_examples_falls_back_to_substring_when_no_embeddings():
    from packages.chat import tools as tools_mod

    # Force the lazy client to None and override the getter even when
    # GEMINI_API_KEY is set in the env, so we exercise the fallback.
    tools_mod._embeddings_client = None
    orig = tools_mod._get_embeddings_client
    tools_mod._get_embeddings_client = lambda: None
    try:
        out = await search_examples(tenant_id="t1", question="what's my GMV last 30 days")
        assert out["examples"]
        assert out.get("retrieval") == "substring_fallback"
    finally:
        tools_mod._get_embeddings_client = orig
        tools_mod._embeddings_client = None


@pytest.mark.asyncio
async def test_search_examples_uses_halfvec_when_examples_indexed(monkeypatch):
    import json
    from datetime import UTC, datetime

    from packages.chat import tools as tools_mod
    from packages.llm.embeddings import FakeEmbeddings

    fake = FakeEmbeddings()
    monkeypatch.setattr(tools_mod, "_get_embeddings_client", lambda: fake)

    seeded_qs = [
        (
            "How is my GMV last 30 days?",
            [{"tool": "compute_metric", "args": {"metric_id": "gmv"}}],
        ),
        (
            "Which pincodes have RTO issues?",
            [
                {
                    "tool": "compute_metric",
                    "args": {
                        "metric_id": "pincode_rto_rate_90d",
                        "dimensions": ["pincode"],
                    },
                }
            ],
        ),
        (
            "Show campaigns by ROAS",
            [
                {
                    "tool": "compute_metric",
                    "args": {
                        "metric_id": "post_rto_roas",
                        "dimensions": ["campaign"],
                    },
                }
            ],
        ),
    ]

    def _fmt(v: list[float]) -> str:
        return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

    async with SessionLocal() as cs:
        await cs.execute(
            text("DELETE FROM core.few_shot_examples WHERE source_record_url LIKE 'test://%'")
        )
        await cs.commit()
    try:
        async with SessionLocal() as s:
            for q, plan in seeded_qs:
                vec = await fake.embed(q)
                await s.execute(
                    text("""
                      INSERT INTO core.few_shot_examples (
                        tenant_id, question, plan, embedding,
                        source_record_url, fetched_at, embedding_model, embedding_version
                      ) VALUES (
                        NULL, :q, CAST(:p AS jsonb), CAST(:e AS halfvec),
                        :url, :ts, 'fake', 'v1'
                      )
                    """),
                    {
                        "q": q,
                        "p": json.dumps(plan),
                        "e": _fmt(vec),
                        "url": "test://" + q[:20],
                        "ts": datetime.now(UTC),
                    },
                )
            await s.commit()

        out = await search_examples(tenant_id="t1", question="How is my GMV last 30 days?")
        assert out.get("retrieval") == "halfvec_cosine_nn"
        assert len(out["examples"]) >= 1
        assert out["examples"][0]["question"] == "How is my GMV last 30 days?"
    finally:
        async with SessionLocal() as cs:
            await cs.execute(
                text("DELETE FROM core.few_shot_examples WHERE source_record_url LIKE 'test://%'")
            )
            await cs.commit()
        tools_mod._embeddings_client = None


@pytest.mark.asyncio
async def test_search_examples_does_not_leak_across_tenants(monkeypatch):
    """Tenant A's curated example must not surface in tenant B's search."""
    import json
    import uuid
    from datetime import UTC, datetime

    from packages.chat import tools as tools_mod
    from packages.llm.embeddings import FakeEmbeddings

    fake = FakeEmbeddings()
    monkeypatch.setattr(tools_mod, "_get_embeddings_client", lambda: fake)

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    secret_q = "Tenant A only: how is my secret KPI?"

    def _fmt(v: list[float]) -> str:
        return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

    async with SessionLocal() as cs:
        await cs.execute(
            text("DELETE FROM core.few_shot_examples WHERE source_record_url LIKE 'test://iso%'")
        )
        await cs.commit()
    try:
        async with SessionLocal() as s:
            vec = await fake.embed(secret_q)
            await s.execute(
                text("""
                  INSERT INTO core.few_shot_examples (
                    tenant_id, question, plan, embedding,
                    source_record_url, fetched_at, embedding_model, embedding_version
                  ) VALUES (
                    CAST(:t AS uuid), :q, CAST(:p AS jsonb), CAST(:e AS halfvec),
                    :url, :ts, 'fake', 'v1'
                  )
                """),
                {
                    "t": tenant_a,
                    "q": secret_q,
                    "p": json.dumps([{"tool": "compute_metric"}]),
                    "e": _fmt(vec),
                    "url": "test://iso-a",
                    "ts": datetime.now(UTC),
                },
            )
            await s.commit()

        # Same question, different tenant — should NOT see tenant A's example.
        out_b = await search_examples(tenant_id=tenant_b, question=secret_q)
        questions_b = [e["question"] for e in out_b.get("examples", [])]
        assert secret_q not in questions_b, "tenant B leaked tenant A's example"

        # Tenant A queries the same thing — should see it.
        out_a = await search_examples(tenant_id=tenant_a, question=secret_q)
        questions_a = [e["question"] for e in out_a.get("examples", [])]
        assert secret_q in questions_a
    finally:
        async with SessionLocal() as cs:
            await cs.execute(
                text("DELETE FROM core.few_shot_examples WHERE source_record_url LIKE 'test://iso%'")
            )
            await cs.commit()
        tools_mod._embeddings_client = None


def test_tool_registry_has_seven_tools():
    assert len(TOOL_REGISTRY) == 7
    assert len(TOOL_SCHEMAS) == 7
    names_reg = set(TOOL_REGISTRY.keys())
    names_schemas = {s["name"] for s in TOOL_SCHEMAS}
    assert names_reg == names_schemas
    assert {
        "get_schema",
        "search_examples",
        "compute_metric",
        "search_rows",
        "get_provenance",
        "run_sql",
        "propose_write",
    } == names_reg
