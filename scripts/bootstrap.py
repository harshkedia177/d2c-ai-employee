"""Idempotent end-to-end bootstrap for the docker compose path."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import text  # noqa: E402

from packages.warehouse.db import SessionLocal  # noqa: E402

log = logging.getLogger("bootstrap")

DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000001"
QUEUE_DRAIN_TIMEOUT_S = int(os.environ.get("BOOTSTRAP_QUEUE_DRAIN_TIMEOUT_S", "600"))


async def _connector_state_count(tenant_id: str) -> int:
    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT COUNT(*) FROM control.connector_state WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
        return r.scalar_one()


async def _few_shot_count() -> int:
    async with SessionLocal() as s:
        r = await s.execute(
            text(
                "SELECT COUNT(*) FROM core.few_shot_examples WHERE embedding_version = 'v1'"
            )
        )
        return r.scalar_one()


async def _wait_for_queue_drain() -> None:
    deadline = time.time() + QUEUE_DRAIN_TIMEOUT_S
    last_remaining = -1
    while time.time() < deadline:
        async with SessionLocal() as s:
            r = await s.execute(
                text(
                    "SELECT COUNT(*) FROM control.queue_realtime "
                    "WHERE completed_at IS NULL"
                )
            )
            remaining = r.scalar_one()
        if remaining == 0:
            log.info("queue drained")
            return
        if remaining != last_remaining:
            log.info("queue: %d jobs remaining", remaining)
            last_remaining = remaining
        await asyncio.sleep(2)
    raise TimeoutError(
        f"queue did not drain within {QUEUE_DRAIN_TIMEOUT_S}s — "
        "is the worker container running?"
    )


async def _step_pull() -> None:
    existing = await _connector_state_count(DEMO_TENANT_ID)
    if existing > 0:
        log.info(
            "skip pull: control.connector_state already has %d rows for tenant", existing
        )
        return
    log.info("pulling connector data (shopify, shiprocket, meta_ads)")
    # Import here so module-level config evaluation happens after DB env is wired.
    from scripts.pull_demo_data import main_async as pull_main

    await pull_main(tenant_id=DEMO_TENANT_ID, reset=False)


async def _step_agents() -> None:
    from packages.agents.scheduler import (
        run_meta_pauser_for_tenant,
        run_pincode_blocker_for_tenant,
    )

    log.info("running pincode_cod_blocker")
    await run_pincode_blocker_for_tenant(DEMO_TENANT_ID)
    log.info("running meta_pauser")
    await run_meta_pauser_for_tenant(DEMO_TENANT_ID)


async def _step_embed() -> None:
    existing = await _few_shot_count()
    if existing > 0:
        log.info("skip embed: core.few_shot_examples already has %d rows under v1", existing)
        return
    if not os.environ.get("GEMINI_API_KEY"):
        log.warning(
            "GEMINI_API_KEY is empty; skipping embedding seed. "
            "search_examples will fall back to substring overlap."
        )
        return
    from scripts.seed_examples import main_async as embed_main

    # --auto: union manual curated examples with deterministically-generated
    # ones from metrics.yml. --tenant-id: probe DB to skip examples for
    # metrics the tenant has no signal on.
    await embed_main(dry_run=False, auto=True, tenant_id=DEMO_TENANT_ID)


async def _print_summary() -> None:
    queries = {
        "raw.shopify_orders": "SELECT COUNT(*) FROM raw.shopify_orders WHERE tenant_id = :t",
        "raw.shiprocket_shipments": "SELECT COUNT(*) FROM raw.shiprocket_shipments WHERE tenant_id = :t",
        "raw.meta_ad_insights": "SELECT COUNT(*) FROM raw.meta_ad_insights WHERE tenant_id = :t",
        "core.order": 'SELECT COUNT(*) FROM core."order" WHERE tenant_id = :t',
        "core.shipment": "SELECT COUNT(*) FROM core.shipment WHERE tenant_id = :t",
        "core.ad_spend_daily": "SELECT COUNT(*) FROM core.ad_spend_daily WHERE tenant_id = :t",
        "core.agent_runs": "SELECT COUNT(*) FROM core.agent_runs WHERE tenant_id = :t",
        "core.few_shot_examples": "SELECT COUNT(*) FROM core.few_shot_examples WHERE embedding_version = 'v1'",
    }
    log.info("bootstrap summary:")
    async with SessionLocal() as s:
        for label, sql in queries.items():
            r = await s.execute(text(sql), {"t": DEMO_TENANT_ID})
            log.info("  %-28s %d rows", label, r.scalar_one())


async def main_async() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info("bootstrap start")
    await _step_pull()
    log.info("waiting for worker to drain the realtime queue")
    await _wait_for_queue_drain()
    await _step_agents()
    await _step_embed()
    await _print_summary()
    log.info("bootstrap done — api + chat-ui can now serve")


if __name__ == "__main__":
    asyncio.run(main_async())
