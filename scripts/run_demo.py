"""One-button demo orchestrator.

Spawns the worker, runs the connector pull, waits for the queue to drain,
then runs the cron agents. Prints a summary of what landed.

Prerequisites: docker compose up -d postgres redis mock_saas; alembic upgrade head.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import text

from packages.agents.scheduler import (
    run_meta_pauser_for_tenant,
    run_pincode_blocker_for_tenant,
)
from packages.warehouse.db import SessionLocal

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _ensure_mock_seed() -> None:
    """Generate mock_saas seed JSON files if they don't exist."""
    seed_dir = ROOT / "mock_saas" / "seed" / "data"
    expected = seed_dir / "m000_shopify_orders.json"
    if expected.exists():
        log.info("mock_saas seed already present")
        return
    log.info("generating mock_saas seed (1 merchant, 2000 orders)")
    subprocess.check_call(
        [
            "uv",
            "run",
            "python",
            "-m",
            "mock_saas.seed.generate",
            "--merchants=1",
            "--orders-per-merchant=2000",
        ],
        cwd=ROOT,
    )


def _spawn_worker() -> subprocess.Popen:
    log.info("spawning worker subprocess")
    return subprocess.Popen(
        ["uv", "run", "python", "-m", "packages.ingestion.worker"],
        cwd=ROOT,
        env={**os.environ},
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


async def _pull_data() -> None:
    log.info("running connector pull")
    subprocess.check_call(
        ["uv", "run", "python", "scripts/pull_demo_data.py", "--reset"],
        cwd=ROOT,
    )


async def _wait_for_queue_drain(timeout_s: int = 180) -> None:
    """Poll control.queue_realtime until count of unfinished jobs == 0."""
    log.info("waiting for queue to drain (timeout %ds)", timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        async with SessionLocal() as s:
            r = await s.execute(
                text("SELECT COUNT(*) FROM control.queue_realtime WHERE completed_at IS NULL")
            )
            remaining = r.scalar_one()
        if remaining == 0:
            log.info("queue drained")
            return
        log.info("  %d jobs remaining…", remaining)
        await asyncio.sleep(2)
    raise TimeoutError(f"queue did not drain within {timeout_s}s")


async def _print_summary(tenant_id: str) -> None:
    queries = {
        "raw.shopify_orders": "SELECT COUNT(*) FROM raw.shopify_orders WHERE tenant_id = :t",
        "raw.shopify_line_items": (
            "SELECT COUNT(*) FROM raw.shopify_line_items WHERE tenant_id = :t"
        ),
        "raw.shiprocket_shipments": (
            "SELECT COUNT(*) FROM raw.shiprocket_shipments WHERE tenant_id = :t"
        ),
        "raw.meta_campaigns": "SELECT COUNT(*) FROM raw.meta_campaigns WHERE tenant_id = :t",
        "raw.meta_ad_insights": "SELECT COUNT(*) FROM raw.meta_ad_insights WHERE tenant_id = :t",
        "core.order": 'SELECT COUNT(*) FROM core."order" WHERE tenant_id = :t',
        "core.order_line": "SELECT COUNT(*) FROM core.order_line WHERE tenant_id = :t",
        "core.shipment": "SELECT COUNT(*) FROM core.shipment WHERE tenant_id = :t",
        "core.campaign": "SELECT COUNT(*) FROM core.campaign WHERE tenant_id = :t",
        "core.ad_spend_daily": "SELECT COUNT(*) FROM core.ad_spend_daily WHERE tenant_id = :t",
        "core.agent_runs": "SELECT COUNT(*) FROM core.agent_runs WHERE tenant_id = :t",
    }
    print()
    print("=" * 60)
    print(f"Tenant: {tenant_id} (demo)")
    print("=" * 60)
    async with SessionLocal() as s:
        for label, sql in queries.items():
            r = await s.execute(text(sql), {"t": tenant_id})
            count = r.scalar_one()
            print(f"  {label:32s} {count:>8d} rows")
    print("=" * 60)
    print("Demo is ready. Start the API + UI:")
    print("  uv run uvicorn packages.api.main:app --port 8000 &")
    print("  cd apps/chat-ui && npm run dev")
    print("Then open http://localhost:3000 and pick the demo tenant.")


async def main_async() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _ensure_mock_seed()

    worker = _spawn_worker()
    try:
        await _pull_data()
        await _wait_for_queue_drain()

        log.info("running cron agent: pincode_cod_blocker")
        await run_pincode_blocker_for_tenant(DEMO_TENANT_ID)

        log.info("running cron agent: meta_pauser")
        await run_meta_pauser_for_tenant(DEMO_TENANT_ID)

        await _print_summary(DEMO_TENANT_ID)
    finally:
        log.info("shutting down worker")
        worker.send_signal(signal.SIGTERM)
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()


if __name__ == "__main__":
    asyncio.run(main_async())
