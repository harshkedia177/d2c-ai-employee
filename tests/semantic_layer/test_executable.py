from datetime import date

import pytest
from sqlalchemy import text

from packages.semantic_layer.compiler import compile_metric
from packages.warehouse.db import engine


@pytest.mark.asyncio
async def test_compiled_gmv_sql_runs_against_real_postgres():
    q = compile_metric("gmv", tenant_id="00000000-0000-0000-0000-000000000000")
    async with engine.connect() as conn:
        result = await conn.execute(text(q.sql), q.params)
        rows = result.fetchall()
    # No data inserted -> 1 row with value=NULL or value=0, citations=NULL or empty
    assert len(rows) == 1
    # value column present
    assert hasattr(rows[0], "value") or "value" in rows[0]._mapping
    # citations column present (may be None when no rows)
    assert "citations" in rows[0]._mapping


@pytest.mark.asyncio
async def test_compiled_post_rto_roas_with_dim_runs():
    q = compile_metric(
        "post_rto_roas",
        tenant_id="00000000-0000-0000-0000-000000000000",
        dimensions=["campaign"],
        filters={"date__gte": date(2026, 4, 1)},
    )
    async with engine.connect() as conn:
        result = await conn.execute(text(q.sql), q.params)
        # No rows expected (empty DB), just confirm no parse/plan error
        result.fetchall()
