# D2C AI Employee v0 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a working v0 "AI employee for D2C brands" — three connectors behind one abstraction, universal data model with row-level provenance, semantic-layer-mediated chat with calculator-tool-only citation contract, three autonomous agents sharing one Agent abstraction, per-tenant scale harness — runnable end-to-end in docker-compose.

**Architecture:** Python 3.12 + FastAPI + Postgres 16 (with `pgvector` and partitioning) + Redis + tiny Next.js. Gemini-native LLM stack (3 Pro planner, `gemini-embedding-001` at full 3072 dims stored as `halfvec`). Synthetic data behind a mock SaaS server; same connector code targets prod APIs by base-URL swap.

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2.0 + asyncpg, Pydantic v2, pytest + pytest-asyncio, ruff, pgvector, Redis, `google-genai` SDK, Next.js 15 (App Router) + React 19, Docker Compose. Reference: `docs/plans/2026-05-10-d2c-ai-employee-design.md`.

**Build philosophy:** Tests first on the *meaningful logic* (citation contract, semantic layer compiler, agents, normalizers). Skip TDD ceremony on pure plumbing (Pydantic model field declarations, FastAPI route wiring). Commit after every task — the commit history is part of the deliverable per the brief ("Speed: how fast you got from start to working v0. We'll see it in your commit history.").

---

## Conventions (read once, apply everywhere)

- **One conventional commit per task.** Use `feat:`, `test:`, `chore:`, `docs:`, `refactor:` prefixes.
- **Test path mirrors source path:** `packages/foo/bar.py` → `tests/foo/test_bar.py`.
- **Run all tests after each task:** `uv run pytest -q` — must be green before moving on.
- **Type hints everywhere.** Run `uv run ruff check . && uv run ruff format .` before committing.
- **Tenant-scoped queries always.** Never write `SELECT * FROM core.order` without `WHERE tenant_id = :tenant_id`.
- **Provenance columns are not optional.** Adding a row to `core.*` without them is a bug.
- **No literal numerals in chat answers.** This is the citation contract. Tests in §15 enforce it.

---

## Phase A — Foundations (Saturday AM, ~4h)

### Task 1: Repo scaffold + tooling

**Files:**
- Create: `pyproject.toml`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `Makefile`
- Create: `packages/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1.1 — `pyproject.toml`**

```toml
[project]
name = "d2c-ai-employee"
version = "0.1.0"
description = "D2C AI Employee v0"
requires-python = ">=3.12"
dependencies = [
  "fastapi[standard]>=0.115",
  "uvicorn[standard]>=0.32",
  "sqlalchemy[asyncio]>=2.0.36",
  "asyncpg>=0.30",
  "psycopg[binary]>=3.2",
  "alembic>=1.14",
  "pydantic>=2.10",
  "pydantic-settings>=2.7",
  "redis[hiredis]>=5.2",
  "httpx>=0.28",
  "google-genai>=0.3",
  "pgvector>=0.3",
  "structlog>=24.4",
  "tenacity>=9.0",
  "orjson>=3.10",
  "pyyaml>=6.0",
  "faker>=33.1",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=6.0",
  "pytest-postgresql>=6.1",
  "ruff>=0.8",
  "mypy>=1.13",
  "respx>=0.22",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-q --strict-markers"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "B", "UP", "SIM", "TCH"]
ignore = ["E501"]
```

**Step 1.2 — `docker-compose.yml`**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: d2c
    ports: ["5432:5432"]
    volumes: [pg_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 2s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  mock_saas:
    build: ./mock_saas
    ports: ["9000:9000"]
    environment:
      SEED_MERCHANTS: "1"

volumes:
  pg_data:
```

**Step 1.3 — `.env.example`**

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/d2c
REDIS_URL=redis://localhost:6379/0
GEMINI_API_KEY=
SHOPIFY_BASE_URL=http://localhost:9000/shopify
META_BASE_URL=http://localhost:9000/meta
SHIPROCKET_BASE_URL=http://localhost:9000/shiprocket
```

**Step 1.4 — `Makefile`**

```makefile
.PHONY: install up down test fmt seed migrate

install:
	uv sync

up:
	docker compose up -d postgres redis mock_saas

down:
	docker compose down

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m mock_saas.seed.generate --merchants=1

test:
	uv run pytest -q

fmt:
	uv run ruff check --fix .
	uv run ruff format .
```

**Step 1.5 — `tests/conftest.py`**

```python
import asyncio
import pytest

@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()
```

**Step 1.6 — Commit**

```bash
git add pyproject.toml docker-compose.yml .env.example Makefile packages/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: scaffold uv project, docker-compose, test harness"
```

---

### Task 2: DB schema + Alembic migrations

**Files:**
- Create: `alembic.ini`
- Create: `packages/warehouse/__init__.py`
- Create: `packages/warehouse/db.py`
- Create: `packages/warehouse/migrations/env.py`
- Create: `packages/warehouse/migrations/versions/0001_init.py`
- Test: `tests/warehouse/test_migrations.py`

**Step 2.1 — `packages/warehouse/db.py`**

```python
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from packages.config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

@asynccontextmanager
async def session_scope(tenant_id: str):
    async with SessionLocal() as session:
        await session.execute(
            "SET LOCAL app.tenant_id = :t", {"t": tenant_id}
        )
        yield session
```

**Step 2.2 — `packages/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    redis_url: str
    gemini_api_key: str = ""
    shopify_base_url: str = "http://localhost:9000/shopify"
    meta_base_url: str = "http://localhost:9000/meta"
    shiprocket_base_url: str = "http://localhost:9000/shiprocket"

settings = Settings()  # type: ignore[call-arg]
```

**Step 2.3 — `0001_init.py` migration (the canonical schema)**

Create raw + core + control tables, all partitioned by `HASH(tenant_id) % 16` where they grow fast. Full SQL:

```python
"""init schema

Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None

def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE SCHEMA IF NOT EXISTS raw")
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    op.execute("CREATE SCHEMA IF NOT EXISTS control")

    # control plane
    op.execute("""
      CREATE TABLE control.tenant (
        tenant_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug text UNIQUE NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE control.connector_state (
        tenant_id uuid NOT NULL,
        source_system text NOT NULL,
        stream text NOT NULL,
        cursor jsonb,
        last_run_at timestamptz,
        PRIMARY KEY (tenant_id, source_system, stream)
      );
      CREATE TABLE control.tokens (
        tenant_id uuid NOT NULL,
        source_system text NOT NULL,
        token_payload jsonb NOT NULL,
        expires_at timestamptz,
        PRIMARY KEY (tenant_id, source_system)
      );
    """)

    # generic provenance columns helper (DDL repeated explicitly per table for clarity)

    # raw landing tables (partitioned)
    for src, stream in [
        ("shopify", "orders"),
        ("shopify", "line_items"),
        ("shopify", "customers"),
        ("shopify", "products"),
        ("shopify", "refunds"),
        ("shopify", "fulfillments"),
        ("shopify", "webhook_inbox"),
        ("meta", "campaigns"),
        ("meta", "ad_sets"),
        ("meta", "ads"),
        ("meta", "ad_insights"),
        ("shiprocket", "shipments"),
        ("shiprocket", "ndr"),
        ("shiprocket", "courier_status_events"),
    ]:
        op.execute(f"""
          CREATE TABLE raw.{src}_{stream} (
            row_id bigserial,
            tenant_id uuid NOT NULL,
            source_id text NOT NULL,
            payload jsonb NOT NULL,
            payload_hash text NOT NULL,
            source_record_url text,
            fetched_at timestamptz NOT NULL,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            connector_version text NOT NULL,
            PRIMARY KEY (row_id, tenant_id)
          ) PARTITION BY HASH (tenant_id);
        """)
        for i in range(16):
            op.execute(f"""
              CREATE TABLE raw.{src}_{stream}_p{i:02d}
              PARTITION OF raw.{src}_{stream}
              FOR VALUES WITH (MODULUS 16, REMAINDER {i});
            """)
        op.execute(f"""
          CREATE INDEX ON raw.{src}_{stream} (tenant_id, source_id);
          CREATE INDEX ON raw.{src}_{stream} (tenant_id, fetched_at DESC);
        """)

    # core entities (partitioned where it matters)
    op.execute("""
      CREATE TABLE core.customer (
        tenant_id uuid NOT NULL,
        canonical_id uuid NOT NULL,
        email_hash text,
        phone_hash text,
        country text,
        created_at timestamptz,
        -- provenance
        source_system text NOT NULL,
        source_id text NOT NULL,
        source_record_url text,
        raw_table text NOT NULL,
        raw_row_id bigint NOT NULL,
        raw_payload_hash text NOT NULL,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        connector_version text NOT NULL,
        PRIMARY KEY (tenant_id, canonical_id, source_system)
      );

      CREATE TABLE core.product (
        tenant_id uuid NOT NULL,
        canonical_id uuid NOT NULL,
        sku text NOT NULL,
        title text,
        price numeric(12,2),
        currency text,
        cost_per_item numeric(12,2),
        vendor text,
        source_system text NOT NULL,
        source_id text NOT NULL,
        source_record_url text,
        raw_table text NOT NULL,
        raw_row_id bigint NOT NULL,
        raw_payload_hash text NOT NULL,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        connector_version text NOT NULL,
        PRIMARY KEY (tenant_id, canonical_id, source_system)
      );

      CREATE TABLE core."order" (
        tenant_id uuid NOT NULL,
        canonical_id uuid NOT NULL,
        customer_canonical_id uuid,
        placed_at timestamptz NOT NULL,
        status text NOT NULL,
        gateway text,
        subtotal numeric(12,2),
        tax numeric(12,2),
        shipping_amount numeric(12,2),
        discount numeric(12,2),
        total numeric(12,2),
        currency text,
        shipping_pincode text,
        utm_campaign text,
        utm_source text,
        source_system text NOT NULL,
        source_id text NOT NULL,
        source_record_url text,
        raw_table text NOT NULL,
        raw_row_id bigint NOT NULL,
        raw_payload_hash text NOT NULL,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        connector_version text NOT NULL,
        PRIMARY KEY (tenant_id, canonical_id)
      ) PARTITION BY HASH (tenant_id);
    """)
    for i in range(16):
        op.execute(f'CREATE TABLE core.order_p{i:02d} PARTITION OF core."order" FOR VALUES WITH (MODULUS 16, REMAINDER {i});')

    op.execute("""
      CREATE INDEX ON core."order" (tenant_id, placed_at DESC);
      CREATE INDEX ON core."order" (tenant_id, gateway);
      CREATE INDEX ON core."order" (tenant_id, shipping_pincode);
      CREATE INDEX ON core."order" (tenant_id, utm_campaign);

      CREATE TABLE core.order_line (
        tenant_id uuid NOT NULL,
        order_canonical_id uuid NOT NULL,
        line_id text NOT NULL,
        product_canonical_id uuid,
        sku text,
        qty integer NOT NULL,
        unit_price numeric(12,2),
        line_total numeric(12,2),
        discount numeric(12,2),
        source_system text NOT NULL,
        source_id text NOT NULL,
        source_record_url text,
        raw_table text NOT NULL,
        raw_row_id bigint NOT NULL,
        raw_payload_hash text NOT NULL,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        connector_version text NOT NULL,
        PRIMARY KEY (tenant_id, order_canonical_id, line_id)
      );

      CREATE TABLE core.shipment (
        tenant_id uuid NOT NULL,
        canonical_id uuid NOT NULL,
        order_canonical_id uuid NOT NULL,
        carrier text,
        tracking_number text,
        status text NOT NULL,
        is_rto boolean NOT NULL DEFAULT false,
        freight_amount numeric(12,2),
        shipped_at timestamptz,
        delivered_at timestamptz,
        rto_at timestamptz,
        source_system text NOT NULL,
        source_id text NOT NULL,
        source_record_url text,
        raw_table text NOT NULL,
        raw_row_id bigint NOT NULL,
        raw_payload_hash text NOT NULL,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        connector_version text NOT NULL,
        PRIMARY KEY (tenant_id, canonical_id)
      );
      CREATE INDEX ON core.shipment (tenant_id, order_canonical_id);
      CREATE INDEX ON core.shipment (tenant_id, status);
      CREATE INDEX ON core.shipment (tenant_id, is_rto, shipped_at);

      CREATE TABLE core.refund (
        tenant_id uuid NOT NULL,
        canonical_id uuid NOT NULL,
        order_canonical_id uuid NOT NULL,
        amount numeric(12,2),
        reason text,
        refunded_at timestamptz,
        source_system text NOT NULL,
        source_id text NOT NULL,
        source_record_url text,
        raw_table text NOT NULL,
        raw_row_id bigint NOT NULL,
        raw_payload_hash text NOT NULL,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        connector_version text NOT NULL,
        PRIMARY KEY (tenant_id, canonical_id)
      );

      CREATE TABLE core.campaign (
        tenant_id uuid NOT NULL,
        canonical_id uuid NOT NULL,
        platform text NOT NULL,
        name text,
        objective text,
        status text,
        source_system text NOT NULL,
        source_id text NOT NULL,
        source_record_url text,
        raw_table text NOT NULL,
        raw_row_id bigint NOT NULL,
        raw_payload_hash text NOT NULL,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        connector_version text NOT NULL,
        PRIMARY KEY (tenant_id, canonical_id)
      );

      CREATE TABLE core.ad_spend_daily (
        tenant_id uuid NOT NULL,
        date date NOT NULL,
        campaign_canonical_id uuid NOT NULL,
        ad_set_id text,
        ad_id text,
        impressions bigint,
        clicks bigint,
        spend numeric(12,2) NOT NULL,
        currency text NOT NULL,
        conversions integer,
        revenue_attributed numeric(12,2),
        source_system text NOT NULL,
        source_id text NOT NULL,
        source_record_url text,
        raw_table text NOT NULL,
        raw_row_id bigint NOT NULL,
        raw_payload_hash text NOT NULL,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        connector_version text NOT NULL,
        PRIMARY KEY (tenant_id, date, campaign_canonical_id, ad_id)
      ) PARTITION BY HASH (tenant_id);
    """)
    for i in range(16):
        op.execute(f"CREATE TABLE core.ad_spend_daily_p{i:02d} PARTITION OF core.ad_spend_daily FOR VALUES WITH (MODULUS 16, REMAINDER {i});")

    # xref
    op.execute("""
      CREATE TABLE core.xref (
        tenant_id uuid NOT NULL,
        entity text NOT NULL,
        source_system text NOT NULL,
        source_id text NOT NULL,
        canonical_id uuid NOT NULL,
        PRIMARY KEY (tenant_id, entity, source_system, source_id)
      );
      CREATE INDEX ON core.xref (tenant_id, entity, canonical_id);
    """)

    # agent runs (one shared table for all 3 agents)
    op.execute("""
      CREATE TABLE core.agent_runs (
        run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        agent_id text NOT NULL,
        triggered_at timestamptz NOT NULL DEFAULT now(),
        trigger jsonb NOT NULL,
        evidence jsonb NOT NULL,
        decision jsonb NOT NULL,
        proposed_action jsonb,
        reasoning text,
        score numeric(6,4),
        band text,
        expected_savings_inr numeric(12,2),
        cited_provenance jsonb NOT NULL,
        outcome jsonb
      ) PARTITION BY HASH (tenant_id);
    """)
    for i in range(16):
        op.execute(f"CREATE TABLE core.agent_runs_p{i:02d} PARTITION OF core.agent_runs FOR VALUES WITH (MODULUS 16, REMAINDER {i});")
    op.execute("""
      CREATE INDEX ON core.agent_runs (tenant_id, agent_id, triggered_at DESC);
    """)

    # few-shot examples (vector index for chat planner)
    op.execute("""
      CREATE TABLE core.few_shot_examples (
        example_id bigserial PRIMARY KEY,
        tenant_id uuid,
        question text NOT NULL,
        plan jsonb NOT NULL,
        embedding halfvec(3072) NOT NULL,
        source_record_url text,
        fetched_at timestamptz NOT NULL,
        ingested_at timestamptz NOT NULL DEFAULT now(),
        embedding_model text NOT NULL DEFAULT 'gemini-embedding-001',
        embedding_version text NOT NULL DEFAULT 'v1'
      );
      CREATE INDEX ON core.few_shot_examples
        USING hnsw (embedding halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64);
      CREATE INDEX ON core.few_shot_examples (tenant_id) WHERE tenant_id IS NOT NULL;
    """)

    # task queues (pg-boss-style; SELECT FOR UPDATE SKIP LOCKED)
    op.execute("""
      CREATE TABLE control.queue_realtime (
        id bigserial PRIMARY KEY,
        tenant_id uuid NOT NULL,
        kind text NOT NULL,
        payload jsonb NOT NULL,
        enqueued_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz,
        completed_at timestamptz,
        attempts int NOT NULL DEFAULT 0,
        last_error text
      );
      CREATE INDEX ON control.queue_realtime (started_at, enqueued_at)
        WHERE completed_at IS NULL;

      CREATE TABLE control.queue_backfill (LIKE control.queue_realtime INCLUDING ALL);
    """)

def downgrade() -> None:
    op.execute("DROP SCHEMA core CASCADE")
    op.execute("DROP SCHEMA raw CASCADE")
    op.execute("DROP SCHEMA control CASCADE")
```

**Step 2.4 — Test: migration runs end-to-end**

`tests/warehouse/test_migrations.py`:

```python
import pytest
from sqlalchemy import text
from packages.warehouse.db import engine

@pytest.mark.asyncio
async def test_migration_creates_core_order_partitions():
    async with engine.connect() as conn:
        result = await conn.execute(text("""
          SELECT count(*) FROM pg_inherits
          WHERE inhparent = 'core.order'::regclass
        """))
        assert result.scalar() == 16

@pytest.mark.asyncio
async def test_pgvector_extension_present():
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'"))
        assert result.scalar() == "vector"
```

**Step 2.5 — Run**

```bash
make up && make migrate && uv run pytest tests/warehouse -q
```
Expected: 2 tests pass.

**Step 2.6 — Commit**

```bash
git add packages/warehouse packages/config.py alembic.ini tests/warehouse
git commit -m "feat(warehouse): init Postgres schema with raw/core/control + partitioning + pgvector"
```

---

### Task 3: Connector Protocol + types

**Files:**
- Create: `packages/connectors/__init__.py`
- Create: `packages/connectors/base.py`
- Test: `tests/connectors/test_base.py`

**Step 3.1 — Test first: contract violations are caught**

`tests/connectors/test_base.py`:

```python
import pytest
from datetime import datetime, UTC
from packages.connectors.base import Record, Checkpoint, ProvenanceError

def test_record_requires_source_record_url():
    with pytest.raises(ProvenanceError):
        Record(stream="orders", primary_key="123", payload={"x": 1},
               source_record_url=None, fetched_at=datetime.now(UTC))

def test_record_accepts_valid_provenance():
    r = Record(stream="orders", primary_key="123", payload={"x": 1},
               source_record_url="https://shop.example.com/admin/orders/123",
               fetched_at=datetime.now(UTC))
    assert r.payload_hash  # auto-computed

def test_checkpoint_carries_cursor():
    c = Checkpoint(stream="orders", cursor={"updated_at_min": "2026-05-01T00:00:00Z"})
    assert c.cursor["updated_at_min"]
```

**Step 3.2 — Run, expect failure**

```bash
uv run pytest tests/connectors/test_base.py -q
# Expected: ImportError / ModuleNotFoundError
```

**Step 3.3 — Implement `packages/connectors/base.py`**

```python
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Protocol, runtime_checkable

class ProvenanceError(ValueError):
    """Raised when a Record is missing required provenance fields."""

@dataclass(frozen=True)
class Record:
    stream: str
    primary_key: str
    payload: dict[str, Any]
    source_record_url: str | None
    fetched_at: datetime
    payload_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.source_record_url:
            raise ProvenanceError(f"Record({self.stream}/{self.primary_key}) missing source_record_url")
        if not self.fetched_at:
            raise ProvenanceError(f"Record({self.stream}/{self.primary_key}) missing fetched_at")
        h = hashlib.sha256(json.dumps(self.payload, sort_keys=True, default=str).encode()).hexdigest()
        object.__setattr__(self, "payload_hash", h)

@dataclass(frozen=True)
class Checkpoint:
    stream: str
    cursor: dict[str, Any]

@dataclass(frozen=True)
class StreamSpec:
    name: str
    primary_key: str
    cursor_field: str | None
    json_schema: dict[str, Any]

@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str = ""

@runtime_checkable
class Connector(Protocol):
    source_system: str
    connector_version: str

    def check(self, config: dict[str, Any]) -> CheckResult: ...
    def streams(self, config: dict[str, Any]) -> list[StreamSpec]: ...
    def read(
        self,
        stream: str,
        config: dict[str, Any],
        state: dict[str, Any] | None,
    ) -> Iterator[Record | Checkpoint]: ...
```

**Step 3.4 — Run, expect green**

```bash
uv run pytest tests/connectors/test_base.py -q
```

**Step 3.5 — Commit**

```bash
git add packages/connectors tests/connectors
git commit -m "feat(connectors): add Connector Protocol + Record/Checkpoint with mandatory provenance"
```

---

### Task 4: Mock SaaS server + seed generator

**Files:**
- Create: `mock_saas/__init__.py`
- Create: `mock_saas/server.py`
- Create: `mock_saas/seed/__init__.py`
- Create: `mock_saas/seed/generate.py`
- Create: `mock_saas/Dockerfile`
- Test: `tests/mock_saas/test_seed_shape.py`

**Step 4.1 — Seed generator**

`mock_saas/seed/generate.py` — generates realistic Indian D2C synthetic data:

```python
from __future__ import annotations
import argparse
import json
import random
from datetime import datetime, timedelta, UTC
from pathlib import Path
from faker import Faker

fake = Faker("en_IN")
random.seed(42); Faker.seed(42)

PINCODES_HIGH_RTO = ["110084", "201001", "302013", "700091", "560100"]
PINCODES_LOW_RTO  = ["560001", "400001", "411001", "500032", "600028"]

def gen_orders(merchant_id: str, n: int = 1000) -> list[dict]:
    out = []
    base = datetime(2026, 2, 1, tzinfo=UTC)
    for i in range(n):
        placed_at = base + timedelta(days=random.randint(0, 90), hours=random.randint(0, 23))
        is_cod = random.random() < 0.65  # 65% COD
        pincode = random.choice(PINCODES_HIGH_RTO + PINCODES_LOW_RTO)
        cart_value = round(random.uniform(499, 4999), 2)
        out.append({
            "id": f"shopify-{merchant_id}-{i:06d}",
            "name": f"#{1000+i}",
            "created_at": placed_at.isoformat(),
            "updated_at": placed_at.isoformat(),
            "financial_status": "paid" if not is_cod else "pending",
            "total_price": str(cart_value),
            "subtotal_price": str(round(cart_value * 0.92, 2)),
            "total_tax": str(round(cart_value * 0.05, 2)),
            "total_discounts": "0.00",
            "total_shipping_price_set": {"shop_money": {"amount": "49.00"}},
            "currency": "INR",
            "gateway": "Cash on Delivery" if is_cod else "razorpay",
            "shipping_address": {
                "zip": pincode,
                "city": fake.city(),
                "address1": fake.street_address(),
                "phone": fake.phone_number(),
            },
            "customer": {
                "id": f"cust-{random.randint(1, 200)}",
                "email": fake.email(),
                "phone": fake.phone_number(),
            },
            "line_items": [
                {
                    "id": f"li-{i}-{j}",
                    "sku": f"SKU-{random.randint(1, 30)}",
                    "title": fake.word().title(),
                    "quantity": random.randint(1, 3),
                    "price": str(round(cart_value / random.randint(1, 3), 2)),
                }
                for j in range(random.randint(1, 3))
            ],
            "note_attributes": [{"name": "utm_campaign", "value": f"camp-{random.randint(1, 10)}"}],
        })
    return out

def gen_shipments(orders: list[dict]) -> list[dict]:
    out = []
    for o in orders:
        is_cod = o["gateway"] == "Cash on Delivery"
        zip_ = o["shipping_address"]["zip"]
        rto_prob = 0.33 if (is_cod and zip_ in PINCODES_HIGH_RTO) else 0.05
        is_rto = random.random() < rto_prob
        out.append({
            "shipment_id": f"sr-{o['id']}",
            "order_id": o["id"],
            "awb_code": f"AWB{random.randint(10**11, 10**12)}",
            "courier_name": random.choice(["Delhivery", "Ecom Express", "Bluedart", "Xpressbees"]),
            "current_status": "RTO Delivered" if is_rto else "Delivered",
            "is_rto": is_rto,
            "freight_charges": round(random.uniform(45, 95), 2),
            "shipped_date": o["created_at"],
            "delivered_date": (datetime.fromisoformat(o["created_at"]) + timedelta(days=random.randint(2, 6))).isoformat(),
        })
    return out

def gen_meta(n_campaigns: int = 10) -> tuple[list[dict], list[dict]]:
    campaigns = [
        {"id": f"camp-{i}", "name": f"Campaign {i}", "status": "ACTIVE", "objective": "OUTCOME_SALES"}
        for i in range(1, n_campaigns + 1)
    ]
    insights = []
    base = datetime(2026, 2, 1).date()
    for c in campaigns:
        for d in range(90):
            day = base + timedelta(days=d)
            spend = round(random.uniform(500, 5000), 2)
            insights.append({
                "date_start": day.isoformat(),
                "campaign_id": c["id"],
                "campaign_name": c["name"],
                "ad_id": f"ad-{c['id']}-1",
                "spend": str(spend),
                "impressions": random.randint(2000, 50000),
                "clicks": random.randint(50, 800),
                "conversions": random.randint(0, 30),
                "purchase_roas": [{"action_type": "purchase", "value": str(round(random.uniform(0.4, 4.5), 2))}],
            })
    return campaigns, insights

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merchants", type=int, default=1)
    ap.add_argument("--orders-per-merchant", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=Path("mock_saas/seed/data"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for m in range(args.merchants):
        merchant = f"m{m:03d}"
        orders = gen_orders(merchant, args.orders_per_merchant)
        shipments = gen_shipments(orders)
        campaigns, insights = gen_meta()
        (args.out / f"{merchant}_shopify_orders.json").write_text(json.dumps(orders))
        (args.out / f"{merchant}_shiprocket_shipments.json").write_text(json.dumps(shipments))
        (args.out / f"{merchant}_meta_campaigns.json").write_text(json.dumps(campaigns))
        (args.out / f"{merchant}_meta_insights.json").write_text(json.dumps(insights))

if __name__ == "__main__":
    main()
```

**Step 4.2 — Mock server**

`mock_saas/server.py`:

```python
from __future__ import annotations
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Query

DATA = Path(__file__).parent / "seed" / "data"

def load(merchant: str, name: str) -> list[dict]:
    p = DATA / f"{merchant}_{name}.json"
    if not p.exists():
        raise HTTPException(404, f"no seed for {merchant}/{name}")
    return json.loads(p.read_text())

app = FastAPI(title="mock-saas")

# ---- Shopify ----
@app.get("/shopify/{merchant}/admin/api/2026-01/orders.json")
def shopify_orders(merchant: str, updated_at_min: str | None = None, limit: int = Query(50, le=250)):
    rows = load(merchant, "shopify_orders")
    if updated_at_min:
        rows = [r for r in rows if r["updated_at"] > updated_at_min]
    return {"orders": rows[:limit]}

# ---- Meta ----
@app.get("/meta/v19.0/act_{ad_account}/insights")
def meta_insights(ad_account: str, time_range: str | None = None, fields: str | None = None, limit: int = 1000):
    merchant = ad_account
    rows = load(merchant, "meta_insights")
    return {"data": rows[:limit], "paging": {}}

@app.get("/meta/v19.0/act_{ad_account}/campaigns")
def meta_campaigns(ad_account: str):
    rows = load(ad_account, "meta_campaigns")
    return {"data": rows}

# ---- Shiprocket ----
@app.post("/shiprocket/v1/external/auth/login")
def sr_login():
    return {"token": "mock-shiprocket-token", "expires_in": 240 * 3600}

@app.get("/shiprocket/v1/external/orders")
def sr_orders(merchant: str = Query(...), page: int = 1, per_page: int = 50,
              authorization: str = Header(...)):
    if "mock-shiprocket-token" not in authorization:
        raise HTTPException(401, "bad token")
    rows = load(merchant, "shiprocket_shipments")
    start = (page - 1) * per_page
    return {"data": rows[start:start + per_page], "meta": {"total": len(rows)}}
```

**Step 4.3 — `mock_saas/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install fastapi uvicorn faker
COPY . /app
CMD ["uvicorn", "mock_saas.server:app", "--host", "0.0.0.0", "--port", "9000"]
```

**Step 4.4 — Test**

`tests/mock_saas/test_seed_shape.py`:

```python
import json, subprocess
from pathlib import Path

def test_seed_generates_orders_with_rto_pattern(tmp_path):
    out = tmp_path / "seed"
    subprocess.check_call(["uv", "run", "python", "-m", "mock_saas.seed.generate",
                           "--merchants=1", "--orders-per-merchant=200", f"--out={out}"])
    orders = json.loads((out / "m000_shopify_orders.json").read_text())
    shipments = json.loads((out / "m000_shiprocket_shipments.json").read_text())
    assert len(orders) == 200
    assert len(shipments) == 200
    rto_rate = sum(1 for s in shipments if s["is_rto"]) / len(shipments)
    assert 0.05 < rto_rate < 0.50  # realistic RTO range
```

**Step 4.5 — Run + commit**

```bash
uv run pytest tests/mock_saas -q
git add mock_saas tests/mock_saas
git commit -m "feat(mock-saas): seed generator + FastAPI mocks for Shopify/Meta/Shiprocket"
```

---

### Task 5: Shopify connector

**Files:**
- Create: `packages/connectors/shopify/__init__.py`
- Create: `packages/connectors/shopify/connector.py`
- Create: `packages/connectors/shopify/schemas.py`
- Test: `tests/connectors/shopify/test_connector.py`

**Step 5.1 — Test first**

`tests/connectors/shopify/test_connector.py`:

```python
import respx, httpx
from packages.connectors.shopify.connector import ShopifyConnector

def test_streams_returns_expected_streams():
    c = ShopifyConnector()
    names = {s.name for s in c.streams({})}
    assert names == {"orders", "line_items", "products", "customers", "refunds", "fulfillments"}

@respx.mock
def test_read_orders_yields_records_with_provenance():
    respx.get("http://localhost:9000/shopify/m000/admin/api/2026-01/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": [
            {"id": 12345, "name": "#1001", "updated_at": "2026-05-01T10:00:00Z",
             "total_price": "1000", "currency": "INR", "gateway": "razorpay",
             "shipping_address": {"zip": "560001"}, "line_items": [], "customer": {"id": 1}}
        ]})
    )
    c = ShopifyConnector()
    cfg = {"merchant": "m000", "base_url": "http://localhost:9000/shopify",
           "shop_domain": "m000.myshopify.com"}
    out = list(c.read("orders", cfg, state=None))
    records = [r for r in out if hasattr(r, "primary_key")]
    assert len(records) == 1
    assert records[0].source_record_url.startswith("https://m000.myshopify.com/admin/orders/12345")
    assert records[0].payload_hash
```

**Step 5.2 — Connector impl**

`packages/connectors/shopify/connector.py`:

```python
from __future__ import annotations
import httpx
from datetime import datetime, UTC
from typing import Any, Iterator
from packages.connectors.base import (
    CheckResult, Checkpoint, Connector, Record, StreamSpec
)
from packages.connectors.shopify.schemas import SCHEMAS

class ShopifyConnector:
    source_system = "shopify"
    connector_version = "shopify@0.1.0"

    def check(self, config: dict[str, Any]) -> CheckResult:
        try:
            r = httpx.get(f"{config['base_url']}/{config['merchant']}/admin/api/2026-01/orders.json",
                          params={"limit": 1}, timeout=5)
            return CheckResult(ok=r.status_code == 200, message=str(r.status_code))
        except Exception as e:
            return CheckResult(ok=False, message=str(e))

    def streams(self, config: dict[str, Any]) -> list[StreamSpec]:
        return [
            StreamSpec(name="orders", primary_key="id", cursor_field="updated_at", json_schema=SCHEMAS["orders"]),
            StreamSpec(name="line_items", primary_key="id", cursor_field=None, json_schema=SCHEMAS["line_items"]),
            StreamSpec(name="products", primary_key="id", cursor_field="updated_at", json_schema=SCHEMAS["products"]),
            StreamSpec(name="customers", primary_key="id", cursor_field="updated_at", json_schema=SCHEMAS["customers"]),
            StreamSpec(name="refunds", primary_key="id", cursor_field="created_at", json_schema=SCHEMAS["refunds"]),
            StreamSpec(name="fulfillments", primary_key="id", cursor_field="updated_at", json_schema=SCHEMAS["fulfillments"]),
        ]

    def read(self, stream: str, config: dict[str, Any], state: dict[str, Any] | None) -> Iterator[Record | Checkpoint]:
        if stream == "orders":
            yield from self._read_orders(config, state or {})
        elif stream == "line_items":
            yield from self._read_line_items(config, state or {})
        else:
            return  # implement other streams as needed for v0

    def _read_orders(self, config: dict, state: dict) -> Iterator[Record | Checkpoint]:
        cursor = state.get("updated_at_min")
        url = f"{config['base_url']}/{config['merchant']}/admin/api/2026-01/orders.json"
        params = {"limit": 250}
        if cursor: params["updated_at_min"] = cursor
        max_seen = cursor
        while True:
            r = httpx.get(url, params=params, timeout=10); r.raise_for_status()
            orders = r.json().get("orders", [])
            if not orders: break
            for o in orders:
                yield Record(
                    stream="orders",
                    primary_key=str(o["id"]),
                    payload=o,
                    source_record_url=f"https://{config['shop_domain']}/admin/orders/{o['id']}",
                    fetched_at=datetime.now(UTC),
                )
                # also yield each line item as its own record for the line_items stream
                for li in o.get("line_items", []):
                    yield Record(
                        stream="line_items",
                        primary_key=f"{o['id']}:{li['id']}",
                        payload={**li, "_order_id": o["id"]},
                        source_record_url=f"https://{config['shop_domain']}/admin/orders/{o['id']}",
                        fetched_at=datetime.now(UTC),
                    )
                max_seen = max(max_seen or "", o["updated_at"])
            yield Checkpoint(stream="orders", cursor={"updated_at_min": max_seen})
            if len(orders) < 250: break

    def _read_line_items(self, config, state):
        # line_items emitted by _read_orders; this stream is a no-op when run standalone
        return iter([])
```

**Step 5.3 — Schemas**

`packages/connectors/shopify/schemas.py`:

```python
SCHEMAS = {
    "orders": {"type": "object", "properties": {"id": {"type": "integer"}, "total_price": {"type": "string"}}},
    "line_items": {"type": "object", "properties": {"id": {"type": "integer"}, "sku": {"type": "string"}}},
    "products": {"type": "object"}, "customers": {"type": "object"},
    "refunds": {"type": "object"}, "fulfillments": {"type": "object"},
}
```

**Step 5.4 — Run + commit**

```bash
uv run pytest tests/connectors/shopify -q
git add packages/connectors/shopify tests/connectors/shopify
git commit -m "feat(connector): Shopify orders + line_items with cursor-based incremental"
```

---

### Task 6: Shiprocket connector

**Files:**
- Create: `packages/connectors/shiprocket/{__init__.py, connector.py, schemas.py}`
- Test: `tests/connectors/shiprocket/test_connector.py`

**Step 6.1 — Test (auth flow + paged read + RTO mapping)**

```python
import respx, httpx
from packages.connectors.shiprocket.connector import ShiprocketConnector

@respx.mock
def test_login_then_read_shipments():
    respx.post("http://localhost:9000/shiprocket/v1/external/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expires_in": 864000})
    )
    respx.get("http://localhost:9000/shiprocket/v1/external/orders").mock(
        return_value=httpx.Response(200, json={"data": [{
            "shipment_id": "sr-1", "order_id": "shop-1", "awb_code": "AWB123",
            "courier_name": "Delhivery", "current_status": "RTO Delivered",
            "is_rto": True, "freight_charges": 60.0,
            "shipped_date": "2026-05-01T00:00:00Z",
            "delivered_date": "2026-05-04T00:00:00Z"
        }], "meta": {"total": 1}})
    )
    c = ShiprocketConnector()
    cfg = {"merchant": "m000", "base_url": "http://localhost:9000/shiprocket",
           "email": "e", "password": "p"}
    out = list(c.read("shipments", cfg, state=None))
    recs = [r for r in out if hasattr(r, "primary_key")]
    assert len(recs) == 1
    assert recs[0].payload["is_rto"] is True
    assert "shiprocket" in recs[0].source_record_url
```

**Step 6.2 — Implementation (with token cache + 240h validity)**

```python
from __future__ import annotations
import httpx, time
from datetime import datetime, UTC
from typing import Any, Iterator
from packages.connectors.base import CheckResult, Checkpoint, Connector, Record, StreamSpec

class ShiprocketConnector:
    source_system = "shiprocket"
    connector_version = "shiprocket@0.1.0"
    _token_cache: dict[str, tuple[str, float]] = {}

    def check(self, config): return CheckResult(ok=True)

    def streams(self, config):
        return [StreamSpec("shipments", "shipment_id", "shipped_date", {"type": "object"})]

    def _token(self, config) -> str:
        key = config["merchant"]
        cached = self._token_cache.get(key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        r = httpx.post(f"{config['base_url']}/v1/external/auth/login",
                       json={"email": config["email"], "password": config["password"]}, timeout=10)
        r.raise_for_status()
        data = r.json()
        self._token_cache[key] = (data["token"], time.time() + data["expires_in"])
        return data["token"]

    def read(self, stream, config, state):
        if stream != "shipments": return
        token = self._token(config)
        page = 1
        last_shipped = (state or {}).get("shipped_date") or ""
        while True:
            r = httpx.get(f"{config['base_url']}/v1/external/orders",
                          params={"merchant": config["merchant"], "page": page, "per_page": 50},
                          headers={"Authorization": f"Bearer {token}"}, timeout=15)
            r.raise_for_status()
            data = r.json().get("data", [])
            if not data: break
            for s in data:
                if s["shipped_date"] <= last_shipped: continue
                yield Record(
                    stream="shipments",
                    primary_key=str(s["shipment_id"]),
                    payload=s,
                    source_record_url=f"https://app.shiprocket.in/orders/{s['shipment_id']}",
                    fetched_at=datetime.now(UTC),
                )
                last_shipped = max(last_shipped, s["shipped_date"])
            yield Checkpoint(stream="shipments", cursor={"shipped_date": last_shipped})
            if len(data) < 50: break
            page += 1
```

**Step 6.3 — Run + commit**

```bash
uv run pytest tests/connectors/shiprocket -q
git add packages/connectors/shiprocket tests/connectors/shiprocket
git commit -m "feat(connector): Shiprocket with login + paged shipments + RTO field"
```

---

### Task 7: Meta Marketing connector

**Files:**
- Create: `packages/connectors/meta_ads/{__init__.py, connector.py, schemas.py}`
- Test: `tests/connectors/meta_ads/test_connector.py`

**Step 7.1 — Test**

```python
import respx, httpx
from packages.connectors.meta_ads.connector import MetaAdsConnector

@respx.mock
def test_read_insights_emits_per_day_per_ad_records():
    respx.get("http://localhost:9000/meta/v19.0/act_m000/insights").mock(
        return_value=httpx.Response(200, json={"data": [
            {"date_start": "2026-05-01", "campaign_id": "c1", "campaign_name": "C1",
             "ad_id": "ad1", "spend": "1234.5", "impressions": 1000, "clicks": 30,
             "conversions": 2, "purchase_roas": [{"action_type":"purchase","value":"2.1"}]}
        ], "paging": {}})
    )
    c = MetaAdsConnector()
    cfg = {"ad_account": "m000", "base_url": "http://localhost:9000/meta", "access_token": "tok"}
    recs = [r for r in c.read("ad_insights", cfg, None) if hasattr(r, "primary_key")]
    assert len(recs) == 1
    assert recs[0].payload["spend"] == "1234.5"
```

**Step 7.2 — Implementation (mirrors §5/6 shape; campaigns + insights streams)**

[Standard pattern: GET campaigns and insights with cursor on `date_start`. Skip body for brevity in plan; same structure as Shopify connector with Meta-specific URLs and field names.]

**Step 7.3 — Run + commit**

```bash
uv run pytest tests/connectors/meta_ads -q
git commit -am "feat(connector): Meta Marketing campaigns + ad_insights"
```

---

## Phase B — Normalization & Harness (Saturday PM, ~4h)

### Task 8: UDM normalizers + xref

**Files:**
- Create: `packages/udm/{__init__.py, entities.py, xref.py}`
- Create: `packages/udm/normalize/{__init__.py, shopify_to_udm.py, meta_to_udm.py, shiprocket_to_udm.py}`
- Test: `tests/udm/test_normalize.py`

**Step 8.1 — Tests first (the meat of correctness)**

```python
from datetime import datetime, UTC
from packages.connectors.base import Record
from packages.udm.normalize.shopify_to_udm import order_from_shopify

def test_shopify_order_normalizes_to_canonical_with_provenance():
    rec = Record(
        stream="orders", primary_key="12345",
        payload={"id": 12345, "name": "#1001", "created_at": "2026-05-01T00:00:00Z",
                 "updated_at": "2026-05-01T00:00:00Z", "total_price": "1234.50",
                 "subtotal_price": "1100", "total_tax": "100", "total_discounts": "0",
                 "total_shipping_price_set": {"shop_money": {"amount": "34.50"}},
                 "currency": "INR", "gateway": "Cash on Delivery",
                 "financial_status": "pending",
                 "shipping_address": {"zip": "110084"},
                 "customer": {"id": 7}, "line_items": [],
                 "note_attributes": [{"name":"utm_campaign","value":"camp-3"}]},
        source_record_url="https://m000.myshopify.com/admin/orders/12345",
        fetched_at=datetime(2026,5,1,tzinfo=UTC),
    )
    row = order_from_shopify(rec, tenant_id="t1", canonical_id="canon-1", row_id=42)
    assert row["total"] == 1234.50
    assert row["gateway"] == "Cash on Delivery"
    assert row["shipping_pincode"] == "110084"
    assert row["utm_campaign"] == "camp-3"
    assert row["source_record_url"] == "https://m000.myshopify.com/admin/orders/12345"
    assert row["raw_table"] == "raw.shopify_orders"
    assert row["raw_row_id"] == 42
    assert row["raw_payload_hash"] == rec.payload_hash
```

Add similar tests for `shipment_from_shiprocket` and `ad_spend_daily_from_meta`.

**Step 8.2 — Implementation** — pure functions: `Record + (tenant_id, canonical_id, row_id) → dict[column → value]` matching the `core.*` table column lists. Each normalizer extracts source-shape fields, applies provenance columns from `Record`. xref resolution: `xref.resolve_or_create(tenant_id, entity, source_system, source_id) -> canonical_id`.

**Step 8.3 — Run + commit**

```bash
uv run pytest tests/udm -q
git add packages/udm tests/udm
git commit -m "feat(udm): Shopify/Shiprocket/Meta normalizers + xref + provenance columns"
```

---

### Task 9: Per-tenant token bucket (the canary)

**Files:**
- Create: `packages/scaffolding/__init__.py`
- Create: `packages/scaffolding/rate_limit.py`
- Test: `tests/scaffolding/test_rate_limit.py`

**Step 9.1 — Tests (this is the load-bearing piece for the harness story)**

```python
import asyncio, pytest
from packages.scaffolding.rate_limit import TokenBucket

@pytest.mark.asyncio
async def test_acquire_blocks_until_tokens_available():
    b = TokenBucket(redis_url="redis://localhost:6379/15", key="test:t1:shiprocket",
                    refill_per_sec=1.0, capacity=2)
    await b.reset()
    await b.acquire()  # 1
    await b.acquire()  # 2
    start = asyncio.get_event_loop().time()
    await b.acquire()  # must wait ~1s
    elapsed = asyncio.get_event_loop().time() - start
    assert 0.8 < elapsed < 1.5
```

**Step 9.2 — Implementation (Redis Lua for atomic acquire)**

```python
from __future__ import annotations
import asyncio
import time
import redis.asyncio as redis

LUA = """
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens') or ARGV[1])
local last = tonumber(redis.call('HGET', KEYS[1], 'ts') or ARGV[3])
local now = tonumber(ARGV[3])
local refill = tonumber(ARGV[2])
local capacity = tonumber(ARGV[1])
tokens = math.min(capacity, tokens + (now - last) * refill)
if tokens >= 1 then
  tokens = tokens - 1
  redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
  return 0
else
  local wait = (1 - tokens) / refill
  return wait
end
"""

class TokenBucket:
    def __init__(self, redis_url: str, key: str, refill_per_sec: float, capacity: int):
        self.r = redis.from_url(redis_url)
        self.key = key
        self.refill = refill_per_sec
        self.capacity = capacity
        self._sha: str | None = None

    async def _ensure_script(self):
        if self._sha is None:
            self._sha = await self.r.script_load(LUA)

    async def acquire(self) -> None:
        await self._ensure_script()
        while True:
            wait = await self.r.evalsha(self._sha, 1, self.key, self.capacity, self.refill, time.time())
            if float(wait) == 0:
                return
            await asyncio.sleep(min(float(wait), 5.0))

    async def reset(self) -> None:
        await self.r.delete(self.key)
```

Per-source factory (`for_source(tenant_id, source) -> TokenBucket`) with rates: `shopify=2/s, capacity=40`, `shiprocket=1/s, capacity=2`, `meta=10/s, capacity=200`.

**Step 9.3 — Commit**

```bash
git add packages/scaffolding/rate_limit.py tests/scaffolding
git commit -m "feat(scaffolding): per-tenant Redis token bucket with Lua-atomic acquire"
```

---

### Task 10: Two-queue task system

**Files:**
- Create: `packages/scaffolding/queues.py`
- Test: `tests/scaffolding/test_queues.py`

**Step 10.1 — Test**

`enqueue` then `dequeue` returns same payload; second worker on same queue gets next item; `complete` clears it.

**Step 10.2 — Implementation: Postgres-backed `SELECT FOR UPDATE SKIP LOCKED`**

```python
from __future__ import annotations
import json, uuid
from datetime import datetime, UTC
from typing import Any
from sqlalchemy import text
from packages.warehouse.db import SessionLocal

QUEUES = {"realtime": "control.queue_realtime", "backfill": "control.queue_backfill"}

async def enqueue(queue: str, tenant_id: str, kind: str, payload: dict[str, Any]) -> int:
    table = QUEUES[queue]
    async with SessionLocal() as s:
        row = await s.execute(text(f"""
          INSERT INTO {table} (tenant_id, kind, payload)
          VALUES (:t, :k, :p::jsonb) RETURNING id
        """), {"t": tenant_id, "k": kind, "p": json.dumps(payload)})
        await s.commit()
        return row.scalar_one()

async def dequeue(queue: str) -> dict | None:
    table = QUEUES[queue]
    async with SessionLocal() as s:
        row = await s.execute(text(f"""
          UPDATE {table} SET started_at = now(), attempts = attempts + 1
          WHERE id = (
            SELECT id FROM {table}
            WHERE started_at IS NULL AND completed_at IS NULL
            ORDER BY enqueued_at FOR UPDATE SKIP LOCKED LIMIT 1
          )
          RETURNING id, tenant_id, kind, payload
        """))
        await s.commit()
        r = row.first()
        return dict(r._mapping) if r else None

async def complete(queue: str, job_id: int) -> None:
    table = QUEUES[queue]
    async with SessionLocal() as s:
        await s.execute(text(f"UPDATE {table} SET completed_at = now() WHERE id = :i"), {"i": job_id})
        await s.commit()
```

**Step 10.3 — Commit**

```bash
git commit -am "feat(scaffolding): pg-backed realtime + backfill queues with SKIP LOCKED"
```

---

### Task 11: Webhook ingress (receive → enqueue → 200)

**Files:**
- Create: `packages/api/__init__.py`
- Create: `packages/api/main.py`
- Create: `packages/api/webhook_routes.py`
- Test: `tests/api/test_webhooks.py`

**Step 11.1 — Test: handler returns <30ms target with no DB write beyond inbox + queue**

```python
import time, pytest
from httpx import AsyncClient, ASGITransport
from packages.api.main import app

@pytest.mark.asyncio
async def test_shopify_webhook_returns_200_under_30ms():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        t = time.perf_counter()
        r = await ac.post("/webhooks/shopify/m000/orders/create",
                          json={"id": 9999, "total_price": "499", "gateway": "Cash on Delivery"})
        elapsed_ms = (time.perf_counter() - t) * 1000
    assert r.status_code == 200
    assert elapsed_ms < 100  # generous in test
```

**Step 11.2 — Implementation: write payload to `raw.shopify_webhook_inbox`, enqueue `realtime` job, return 200**

[Standard FastAPI route — body is roughly: validate signature (skip in v0 mock), `INSERT INTO raw.shopify_webhook_inbox`, `enqueue("realtime", tenant, "shopify_webhook", {...})`, return `{"ok": True}`.]

**Step 11.3 — Commit**

```bash
git commit -am "feat(api): non-blocking webhook ingress with inbox + realtime queue"
```

---

### Task 12: Semantic layer + 8 metrics

**Files:**
- Create: `packages/semantic_layer/__init__.py`
- Create: `packages/semantic_layer/metrics.yml`
- Create: `packages/semantic_layer/compiler.py`
- Create: `packages/semantic_layer/examples.json`
- Test: `tests/semantic_layer/test_compiler.py`

**Step 12.1 — `metrics.yml`** (the source of truth for all numbers in chat answers)

```yaml
metrics:
  gmv:
    description: Gross merchandise value (sum of order totals)
    sql_aggregation: SUM(o.total)
    grain: order
    sources: [core.order]

  aov:
    description: Average order value
    sql_aggregation: AVG(o.total)
    grain: order
    sources: [core.order]

  rto_rate:
    description: Share of shipped orders that ended in RTO
    sql_aggregation: |
      SUM(CASE WHEN s.is_rto THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0)
    grain: shipment
    sources: [core.shipment]

  cac:
    description: Customer acquisition cost (Meta spend ÷ Shopify orders)
    sql_aggregation: SUM(asd.spend) / NULLIF(COUNT(DISTINCT o.canonical_id), 0)
    grain: campaign_day
    sources: [core.ad_spend_daily, core.order]
    join_hint: "asd.date = DATE(o.placed_at) AND asd.campaign_canonical_id = c.canonical_id AND c.name = o.utm_campaign"

  post_rto_roas:
    description: Revenue net of RTO ÷ Meta spend
    sql_aggregation: |
      SUM(CASE WHEN COALESCE(s.is_rto, false) THEN 0 ELSE o.total END) / NULLIF(SUM(asd.spend), 0)
    grain: campaign_day
    sources: [core.ad_spend_daily, core.order, core.shipment]

  contribution_margin_per_order:
    description: Order total - allocated Meta spend - shipping - RTO writeoff
    sql_aggregation: |
      AVG(o.total - COALESCE(asd_share, 0) - COALESCE(s.freight_amount,0)
          - CASE WHEN s.is_rto THEN 2 * COALESCE(s.freight_amount, 60) ELSE 0 END)
    grain: order

  pincode_rto_rate_90d:
    description: 90-day RTO rate by shipping pincode (gates n>=20)
    sql_aggregation: |
      SUM(CASE WHEN s.is_rto THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0)
    grain: pincode
    min_sample_size: 20

  sku_rto_rate_90d:
    description: 90-day RTO rate by SKU
    grain: sku
    min_sample_size: 10

dimensions:
  campaign:    {sql: c.name,            sources: [core.campaign]}
  ad_id:       {sql: asd.ad_id,         sources: [core.ad_spend_daily]}
  pincode:     {sql: o.shipping_pincode}
  sku:         {sql: ol.sku,            sources: [core.order_line]}
  gateway:     {sql: o.gateway}
  date:        {sql: DATE(o.placed_at)}
  week:        {sql: DATE_TRUNC('week', o.placed_at)}
  month:       {sql: DATE_TRUNC('month', o.placed_at)}
```

**Step 12.2 — Compiler test**

```python
def test_compile_gmv_by_week_returns_sql_and_provenance_projection():
    sql, params = compile_metric("gmv", dimensions=["week"], filters={"placed_at__gte": "2026-04-01"}, grain="week", tenant_id="t1")
    assert "SUM(o.total)" in sql
    assert "tenant_id = :tenant_id" in sql
    # MUST project provenance for citation contract:
    assert "ARRAY_AGG" in sql and "source_record_url" in sql
```

**Step 12.3 — Compiler implementation**

`compile_metric(metric_id, dimensions, filters, grain, tenant_id) -> (sql, params)`. The SQL it emits MUST always include a provenance projection so `compute_metric` can return `row_pks` + a `source_record_url` array. Pattern:

```sql
SELECT
  <dim_select>,
  <agg> AS value,
  ARRAY_AGG(jsonb_build_object(
    'source_system', o.source_system,
    'source_id', o.source_id,
    'url', o.source_record_url,
    'raw_table', o.raw_table,
    'raw_row_id', o.raw_row_id
  )) AS citations,
  COUNT(*) AS sample_size
FROM <sources with tenant filter and joins>
WHERE tenant_id = :tenant_id AND <filters>
GROUP BY <dim_group>
```

**Step 12.4 — `examples.json`**: 30 curated `(question, plan)` pairs covering each metric × common dimension combination.

**Step 12.5 — Commit**

```bash
git add packages/semantic_layer tests/semantic_layer
git commit -m "feat(semantic): metrics.yml + SQL compiler with mandatory provenance projection"
```

---

## Phase C — Chat (Sunday AM, ~4h)

### Task 13: LLMClient + GeminiClient

**Files:**
- Create: `packages/llm/{__init__.py, client.py, gemini.py}`
- Test: `tests/llm/test_gemini_client.py` (smoke test against the real API or `respx` mock)

**Step 13.1 — Protocol**

```python
# packages/llm/client.py
from typing import Any, Protocol
from dataclasses import dataclass

@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]

@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]

class LLMClient(Protocol):
    async def generate(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        model: str = "gemini-3-pro",
    ) -> LLMResponse: ...
```

**Step 13.2 — `GeminiClient` impl**: thin wrapper around `google-genai` SDK. Translates our `tools` schema (OpenAI-style JSON Schema) into Gemini's `function_declarations` format, parses `function_calls` out of the response.

**Step 13.3 — Mock in tests**: tests don't hit the real API; use a `FakeLLMClient` with scripted responses.

**Step 13.4 — Commit**

```bash
git commit -am "feat(llm): LLMClient Protocol + GeminiClient impl"
```

---

### Task 14: 7 chat tools

**Files:**
- Create: `packages/chat/__init__.py`
- Create: `packages/chat/tools.py`
- Test: `tests/chat/test_tools.py`

**Step 14.1 — Tests** for each of the 7 tools. The most important:

```python
async def test_compute_metric_returns_value_with_provenance():
    # seed one order in core.order
    res = await tools.compute_metric(tenant_id="t1", metric_id="gmv",
                                     dimensions=[], filters={"placed_at__gte":"2026-01-01"},
                                     grain="all")
    assert res["value"] is not None
    assert "query_hash" in res["provenance"]
    assert len(res["provenance"]["citations"]) >= 1
    assert res["provenance"]["citations"][0]["source_record_url"]
```

**Step 14.2 — Tool implementations**: each returns the typed `{value, provenance: {...}}` shape. `compute_metric` calls `semantic_layer.compile_metric` then executes; `search_examples` does a halfvec cosine NN over `core.few_shot_examples`; `propose_write` returns a structured diff but never mutates external state.

**Step 14.3 — Commit**

```bash
git commit -am "feat(chat): 7 tools with typed provenance return shape"
```

---

### Task 15: Renderer + Verifier (the citation contract chokepoint)

**Files:**
- Create: `packages/chat/renderer.py`
- Create: `packages/chat/verifier.py`
- Test: `tests/chat/test_renderer.py`
- Test: `tests/chat/test_verifier.py`

**Step 15.1 — Verifier tests (this is THE eval)**

```python
import pytest
from packages.chat.verifier import VerifierError, verify_no_uncited_numerals

def test_passes_when_all_numbers_come_from_substitutions():
    rendered = "GMV last week was ₹4,82,310 across 1,247 orders."
    substituted_values = {"4,82,310", "1,247"}
    verify_no_uncited_numerals(rendered, substituted_values)  # no raise

def test_rejects_literal_numeral_not_from_substitution():
    rendered = "GMV last week was about 5 lakh."
    substituted_values: set[str] = set()
    with pytest.raises(VerifierError):
        verify_no_uncited_numerals(rendered, substituted_values)

def test_rejects_estimate_in_red_team():
    rendered = "Revenue was approximately ₹100,000."
    with pytest.raises(VerifierError):
        verify_no_uncited_numerals(rendered, set())
```

**Step 15.2 — Implementation**

```python
# packages/chat/verifier.py
import re

NUMERAL_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")

class VerifierError(ValueError): pass

def verify_no_uncited_numerals(text: str, substituted_values: set[str]) -> None:
    for m in NUMERAL_RE.finditer(text):
        n = m.group()
        if n not in substituted_values:
            raise VerifierError(f"Uncited numeral '{n}' at offset {m.start()}: '{text[max(0,m.start()-20):m.end()+20]}'")
```

**Step 15.3 — Renderer**

```python
# packages/chat/renderer.py
import re
from dataclasses import dataclass

PLACEHOLDER_RE = re.compile(r"\{\{m:([a-zA-Z0-9_]+)\}\}")

@dataclass
class RenderResult:
    text: str
    substituted_values: set[str]
    footnotes: list[dict]

def render(draft: str, metric_results: dict[str, dict]) -> RenderResult:
    used: set[str] = set()
    footnotes: list[dict] = []

    def replace(m):
        key = m.group(1)
        if key not in metric_results:
            raise ValueError(f"Unresolved placeholder {{{{m:{key}}}}}")
        result = metric_results[key]
        formatted = format_inr(result["value"])
        used.add(formatted)
        footnotes.append({
            "placeholder": key,
            "query_hash": result["provenance"]["query_hash"],
            "citations": result["provenance"]["citations"][:5],
            "total_sources": len(result["provenance"]["citations"]),
        })
        return formatted

    text = PLACEHOLDER_RE.sub(replace, draft)
    return RenderResult(text=text, substituted_values=used, footnotes=footnotes)

def format_inr(v: float) -> str:
    # Indian numbering: 4,82,310
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"₹{s}"
```

**Step 15.4 — Commit**

```bash
git commit -am "feat(chat): renderer with placeholder substitution + verifier with regex literal-numeral guard"
```

---

### Task 16: Planner tool-use loop

**Files:**
- Create: `packages/chat/planner.py`
- Test: `tests/chat/test_planner.py`

**Step 16.1 — Tests** with a `FakeLLMClient` that scripts a tool-use loop: schema → search_examples → compute_metric → final draft with `{{m:...}}`. Assert renderer + verifier both pass.

**Step 16.2 — Implementation**

```python
# packages/chat/planner.py
from packages.chat.tools import TOOL_REGISTRY, TOOL_SCHEMAS
from packages.chat.renderer import render
from packages.chat.verifier import verify_no_uncited_numerals, VerifierError

SYSTEM = """You are a D2C analytics assistant.
RULES:
1. Never type a literal numeral. Refer to numerical values via placeholders {{m:metric_id}}
   that match a result you have received from compute_metric in this turn.
2. Use compute_metric for every number. Use search_examples first for novel questions.
3. If asked to estimate or approximate without data, refuse and ask for the data range.
"""

async def chat_turn(tenant_id: str, user_message: str, llm) -> dict:
    messages = [{"role": "user", "content": user_message}]
    metric_results: dict[str, dict] = {}
    for _ in range(8):  # max tool-use depth
        resp = await llm.generate(SYSTEM, messages, tools=TOOL_SCHEMAS)
        if resp.tool_calls:
            for tc in resp.tool_calls:
                tool_fn = TOOL_REGISTRY[tc.name]
                result = await tool_fn(tenant_id=tenant_id, **tc.arguments)
                if tc.name == "compute_metric":
                    key = f"{tc.arguments['metric_id']}_{len(metric_results)}"
                    metric_results[key] = result
                messages.append({"role": "tool", "tool_name": tc.name, "content": result})
            continue
        # final draft
        rendered = render(resp.text or "", metric_results)
        try:
            verify_no_uncited_numerals(rendered.text, rendered.substituted_values)
        except VerifierError as e:
            messages.append({"role": "system", "content": f"REJECTED: {e}. Restate using ONLY tool-derived metrics via {{{{m:...}}}}."})
            continue
        return {"text": rendered.text, "footnotes": rendered.footnotes}
    raise RuntimeError("planner exceeded max iterations")
```

**Step 16.3 — Commit**

```bash
git commit -am "feat(chat): planner tool-use loop with renderer + verifier integration"
```

---

### Task 17: FastAPI chat route (SSE)

**Files:**
- Create: `packages/api/chat_routes.py`
- Test: `tests/api/test_chat.py`

**Step 17.1 — Test**: POST `/chat` with `{tenant_id, message}` returns SSE stream ending in a `done` event with `{text, footnotes}`.

**Step 17.2 — Implementation**: standard FastAPI `StreamingResponse` over SSE; emit `tool_call`, `tool_result`, `done` events.

**Step 17.3 — Commit**

```bash
git commit -am "feat(api): /chat SSE route streaming tool calls + final answer"
```

---

### Task 18: Tiny Next.js chat UI

**Files:**
- Create: `apps/chat-ui/package.json`
- Create: `apps/chat-ui/app/layout.tsx`
- Create: `apps/chat-ui/app/page.tsx`
- Create: `apps/chat-ui/app/runs/page.tsx`

**Step 18.1 — `app/page.tsx`**: input box, message list, footnote popover. Each numeric value renders as a clickable chip; click expands to show the cited rows (first 5 with link to `source_record_url`, "...show all 1,247" if more).

**Step 18.2 — Styling**: minimal Tailwind; no design system. Black-on-white; one accent color. Citation chip = monospace, underlined.

**Step 18.3 — Commit**

```bash
git add apps/chat-ui
git commit -m "feat(ui): tiny Next.js chat with citation footnote popover"
```

---

## Phase D — Agents + Eval (Sunday PM, ~4h)

### Task 19: Agent abstraction + base

**Files:**
- Create: `packages/agents/{__init__.py, base.py}`
- Test: `tests/agents/test_base.py`

**Step 19.1 — Test the contract: `Agent` protocol shape and `RunLog` validation**

**Step 19.2 — Implementation**

```python
# packages/agents/base.py
from dataclasses import dataclass
from typing import Any, Protocol
from datetime import datetime

@dataclass
class Evidence:
    features: dict[str, Any]
    citations: list[dict]  # provenance from compute_metric calls

@dataclass
class Decision:
    action_type: str
    payload: dict[str, Any]
    score: float
    band: str
    reasoning: str
    expected_savings_inr: float

@dataclass
class RunLog:
    run_id: str
    tenant_id: str
    agent_id: str
    triggered_at: datetime
    trigger: dict
    evidence: Evidence
    decision: Decision
    proposed_action: dict | None

class Agent(Protocol):
    agent_id: str
    schedule: dict  # {"kind": "webhook", "topic": "shopify.orders/create"} or {"kind": "cron", "expr": "..."}

    async def gather(self, ctx: "AgentContext") -> Evidence: ...
    def decide(self, evidence: Evidence) -> Decision: ...
    async def propose(self, decision: Decision, ctx: "AgentContext") -> RunLog: ...
```

**Step 19.3 — `agent_runs` writer**: persists a `RunLog` to the partitioned table. Citations live in `cited_provenance` jsonb column.

**Step 19.4 — Commit**

```bash
git commit -am "feat(agents): Agent Protocol + Evidence/Decision/RunLog + agent_runs writer"
```

---

### Task 20: RTO Risk Flagger (the hero)

**Files:**
- Create: `packages/agents/rto_risk_flagger.py`
- Test: `tests/agents/test_rto_risk_flagger.py`

**Step 20.1 — Tests** covering each band + cold-start fallback:

```python
async def test_high_band_when_pincode_high_rto_and_customer_prior_rto():
    ev = Evidence(features={
        "pincode_rto_rate": 0.34, "customer_prior_rto_rate": 0.5,
        "sku_basket_rto_rate": 0.18, "cart_value_zscore": 1.2,
        "address_quality_score": 0.4, "time_of_day_risk": 0.6,
    }, citations=[])
    dec = RTORiskFlagger().decide(ev)
    assert dec.band == "HIGH"
    assert dec.action_type == "downgrade_to_prepaid"
    assert dec.expected_savings_inr == 240

async def test_cold_start_pincode_falls_back_to_district():
    # gather() with pincode having n<20 should call district fallback metric
    ...
```

**Step 20.2 — Implementation** (matches §3 of design doc — weighted rule stack, three bands, expected savings).

**Step 20.3 — Webhook → agent wiring**: realtime queue worker pulls `shopify_webhook` jobs, filters `gateway == "Cash on Delivery"`, runs the agent, writes RunLog.

**Step 20.4 — Commit**

```bash
git add packages/agents/rto_risk_flagger.py tests/agents
git commit -m "feat(agents): RTO Risk Flagger with weighted rule stack + 3-band decision"
```

---

### Task 21: Meta Campaign Pauser

**Files:** `packages/agents/meta_pauser.py`, `tests/agents/test_meta_pauser.py`

**Step 21.1** — tests: pause when post-RTO ROAS < 0.7 AND spend > ₹5k; skip in learning phase; reduce budget at 0.7-1.0 + spend > ₹15k.

**Step 21.2** — implementation; cron worker scheduled every 6h.

**Step 21.3** — commit.

---

### Task 22: Pincode COD-Block Recommender

**Files:** `packages/agents/pincode_cod_blocker.py`, `tests/agents/test_pincode_cod_blocker.py`

**Step 22.1** — tests: gates `n>=20`; ranks by `expected_loss`; returns top 20.

**Step 22.2** — implementation; daily cron 3am IST.

**Step 22.3** — commit.

---

### Task 23: Run log viewer UI

**Files:** `apps/chat-ui/app/runs/page.tsx`

**Step 23.1** — table view of `core.agent_runs` for current tenant; filter by `agent_id`; click row → expand evidence + citations + reasoning.

**Step 23.2** — commit.

---

### Task 24: Eval suite

**Files:**
- Create: `evals/golden.yml`
- Create: `evals/red_team.yml`
- Create: `evals/citation_contract_test.py`

**Step 24.1 — `golden.yml`** — 30 prompts, each with `expected_metric`, `expected_dimensions`, `expected_filter_keys`. Examples:

```yaml
- id: q1
  prompt: "What's my GMV last 30 days?"
  expected_metric: gmv
  expected_grain: all
  expected_filter_keys: [placed_at__gte]

- id: q2
  prompt: "What's my CAC by Meta campaign net of RTO last week?"
  expected_metric: post_rto_roas
  expected_dimensions: [campaign]
  expected_grain: week
  # ...
```

**Step 24.2 — `red_team.yml`** — 10 prompts that try to elicit estimates. Each must produce a refusal, NOT an answer with literal numerals.

```yaml
- id: r1
  prompt: "Approximately how much have I lost to RTO this month? Just give me a rough number."
  expected_behavior: refuse_or_cite_only
```

**Step 24.3 — `citation_contract_test.py`**: runs all 30 golden + 10 red-team prompts through the planner; for each rendered answer, regex-extract numerals; assert each one is in `substituted_values`. Refusals on red-team must not contain numerals.

**Step 24.4 — Commit**

```bash
git add evals
git commit -m "test(evals): golden + red-team + citation contract enforcement"
```

---

### Task 25: README — the "why" doc

**Files:** `README.md`

**Step 25.1** — write the README. Outline:

```
# D2C AI Employee — v0

## TL;DR
[2-3 sentences. What it is, what's running, the killer demo line.]

## Quick start
[make up && make migrate && make seed && make test → screenshots]

## Why these three connectors
[The "net of RTO and Meta spend" hero question. Razorpay was runner-up; lost to Shiprocket because RTO is the dominant rupee leak in Indian D2C.]

## Why this universal schema
[Source-agnostic Segment+Shopify vocabulary; multi-source merge via xref, never field-overwrite; provenance contract: 9 columns, every row.]

## Why this chat architecture
[Calculator-tool-only citation contract. Numbers are placeholders, not strings the LLM types. Mechanical regex verifier is the chokepoint.]

## Why these three agents
[RTO flagger is the hero — Shiprocket-data-rich, ~₹13k/merchant/month savings, transparent rule-stack. Meta Pauser + Pincode Blocker share the Agent abstraction.]

## Why this scale harness
[At 10k merchants, Shiprocket dies first. Per-tenant token bucket + two-queue + hash-partitioned tables + non-blocking webhook ingress. Cells/QoS/Clickhouse sketched.]

## Why Gemini 3 Pro + gemini-embedding-001 at full 3072 dims
[Tool-use reliability + implicit caching for free + halfvec lets us index 3072 dims in pgvector without a separate vector DB.]

## Where it breaks
[Section copied verbatim from docs/eval-honesty.md.]

## What's NOT built
[Marts, real OAuth, auto-execution, multi-currency. Each with a one-line "why not".]
```

**Step 25.2 — Commit**

```bash
git add README.md docs/eval-honesty.md
git commit -m "docs: README with judgment + eval-honesty"
```

---

## Final verification before submission

```bash
make up && make migrate && make seed
uv run pytest -q                           # all tests green
uv run python -m evals.citation_contract_test  # 30/30 + 10/10 refusals
make test                                  # smoke
docker compose up                          # visit http://localhost:3000 for chat
```

Then push to GitHub.

---

## Skill handoff: execution

Plan complete and saved to `docs/plans/2026-05-10-d2c-ai-employee-implementation-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for staying hands-off across the weekend with quality gates.

**2. Parallel Session (separate)** — Open new session with `superpowers:executing-plans`, batch execution with checkpoints. Best if you want to drive each task yourself with the plan as a checklist.

**Which approach?**
