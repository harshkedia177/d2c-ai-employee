"""init schema

Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


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
    """)
    op.execute("""
      CREATE TABLE control.connector_state (
        tenant_id uuid NOT NULL,
        source_system text NOT NULL,
        stream text NOT NULL,
        cursor jsonb,
        last_run_at timestamptz,
        PRIMARY KEY (tenant_id, source_system, stream)
      );
    """)
    op.execute("""
      CREATE TABLE control.tokens (
        tenant_id uuid NOT NULL,
        source_system text NOT NULL,
        token_payload jsonb NOT NULL,
        expires_at timestamptz,
        PRIMARY KEY (tenant_id, source_system)
      );
    """)

    # raw landing tables (partitioned by hash(tenant_id) % 16)
    raw_streams = [
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
    ]
    for src, stream in raw_streams:
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
            op.execute(
                f"CREATE TABLE raw.{src}_{stream}_p{i:02d} "
                f"PARTITION OF raw.{src}_{stream} "
                f"FOR VALUES WITH (MODULUS 16, REMAINDER {i});"
            )
        op.execute(f"CREATE INDEX ON raw.{src}_{stream} (tenant_id, source_id);")
        op.execute(f"CREATE INDEX ON raw.{src}_{stream} (tenant_id, fetched_at DESC);")

    # core entities
    op.execute("""
      CREATE TABLE core.customer (
        tenant_id uuid NOT NULL,
        canonical_id uuid NOT NULL,
        email_hash text,
        phone_hash text,
        country text,
        created_at timestamptz,
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
    """)
    op.execute("""
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
    """)
    op.execute("""
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
        op.execute(
            f'CREATE TABLE core.order_p{i:02d} PARTITION OF core."order" '
            f"FOR VALUES WITH (MODULUS 16, REMAINDER {i});"
        )
    op.execute('CREATE INDEX ON core."order" (tenant_id, placed_at DESC);')
    op.execute('CREATE INDEX ON core."order" (tenant_id, gateway);')
    op.execute('CREATE INDEX ON core."order" (tenant_id, shipping_pincode);')
    op.execute('CREATE INDEX ON core."order" (tenant_id, utm_campaign);')

    op.execute("""
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
    """)

    op.execute("""
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
    """)
    op.execute("CREATE INDEX ON core.shipment (tenant_id, order_canonical_id);")
    op.execute("CREATE INDEX ON core.shipment (tenant_id, status);")
    op.execute("CREATE INDEX ON core.shipment (tenant_id, is_rto, shipped_at);")

    op.execute("""
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
    """)

    op.execute("""
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
    """)

    op.execute("""
      CREATE TABLE core.ad_spend_daily (
        tenant_id uuid NOT NULL,
        date date NOT NULL,
        campaign_canonical_id uuid NOT NULL,
        ad_set_id text,
        ad_id text NOT NULL,
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
        op.execute(
            f"CREATE TABLE core.ad_spend_daily_p{i:02d} PARTITION OF core.ad_spend_daily "
            f"FOR VALUES WITH (MODULUS 16, REMAINDER {i});"
        )

    op.execute("""
      CREATE TABLE core.xref (
        tenant_id uuid NOT NULL,
        entity text NOT NULL,
        source_system text NOT NULL,
        source_id text NOT NULL,
        canonical_id uuid NOT NULL,
        PRIMARY KEY (tenant_id, entity, source_system, source_id)
      );
    """)
    op.execute("CREATE INDEX ON core.xref (tenant_id, entity, canonical_id);")

    op.execute("""
      CREATE TABLE core.agent_runs (
        run_id uuid NOT NULL DEFAULT gen_random_uuid(),
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
        outcome jsonb,
        PRIMARY KEY (run_id, tenant_id)
      ) PARTITION BY HASH (tenant_id);
    """)
    for i in range(16):
        op.execute(
            f"CREATE TABLE core.agent_runs_p{i:02d} PARTITION OF core.agent_runs "
            f"FOR VALUES WITH (MODULUS 16, REMAINDER {i});"
        )
    op.execute("CREATE INDEX ON core.agent_runs (tenant_id, agent_id, triggered_at DESC);")

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
    """)
    op.execute("""
      CREATE INDEX ON core.few_shot_examples
        USING hnsw (embedding halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    """)
    op.execute("CREATE INDEX ON core.few_shot_examples (tenant_id) WHERE tenant_id IS NOT NULL;")

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
    """)
    op.execute("""
      CREATE INDEX ON control.queue_realtime (started_at, enqueued_at)
      WHERE completed_at IS NULL;
    """)
    op.execute("CREATE TABLE control.queue_backfill (LIKE control.queue_realtime INCLUDING ALL);")


def downgrade() -> None:
    op.execute("DROP SCHEMA core CASCADE")
    op.execute("DROP SCHEMA raw CASCADE")
    op.execute("DROP SCHEMA control CASCADE")
