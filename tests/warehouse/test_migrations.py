import pytest
from sqlalchemy import text

from packages.warehouse.db import engine


@pytest.mark.asyncio
async def test_migration_creates_core_order_partitions():
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FROM pg_inherits "
                "WHERE inhparent = 'core.\"order\"'::regclass"
            )
        )
        assert result.scalar() == 16


@pytest.mark.asyncio
async def test_pgvector_extension_present():
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname='vector'")
        )
        assert result.scalar() == "vector"


@pytest.mark.asyncio
async def test_halfvec_column_on_few_shot_examples():
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT format_type(atttypid, atttypmod) "
                "FROM pg_attribute WHERE attrelid='core.few_shot_examples'::regclass "
                "AND attname='embedding'"
            )
        )
        assert "halfvec" in result.scalar()
