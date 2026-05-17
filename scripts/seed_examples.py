"""Embed `(question, plan)` few-shot examples and INSERT into core.few_shot_examples.

Two tiers, both stored in the same table with tenant_id=NULL:

  1. Manual / procedural — read from packages/semantic_layer/examples.json.
     Curated by hand. Multi-step diagnostic plans the generator can't synthesize.

  2. Auto / templatey  — emitted by packages/semantic_layer/example_generator from
     metrics.yml × supported dimensions × a tenant-data probe. Opt-in via --auto.

The two tiers are unioned + deduped by question (case-insensitive) before embedding.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from packages.config import settings  # noqa: E402
from packages.llm.embeddings import GeminiEmbeddings  # noqa: E402
from packages.semantic_layer.example_generator import (  # noqa: E402
    TenantProbe,
    generate,
)
from packages.warehouse.db import SessionLocal  # noqa: E402

log = logging.getLogger(__name__)

EXAMPLES_PATH = ROOT / "packages" / "semantic_layer" / "examples.json"


def _format_halfvec(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def _probe_tenant(tenant_id: str | None) -> TenantProbe:
    """Quick read against the warehouse to figure out which generated examples
    will actually have data to back them. If tenant_id is None, returns all-on
    (generates the maximal example set — useful at first bring-up)."""
    if tenant_id is None:
        return TenantProbe.all_on()

    async with SessionLocal() as s:
        async def scalar(q: str, **params: Any) -> Any:
            r = await s.execute(text(q), {"t": tenant_id, **params})
            return r.scalar()

        has_orders = bool(await scalar("SELECT EXISTS(SELECT 1 FROM core.\"order\" WHERE tenant_id = :t)"))
        has_shipments_with_rto = bool(
            await scalar("SELECT EXISTS(SELECT 1 FROM core.shipment WHERE tenant_id = :t AND is_rto)")
        )
        has_campaigns = bool(await scalar("SELECT EXISTS(SELECT 1 FROM core.campaign WHERE tenant_id = :t)"))
        gateway_diversity = int(
            await scalar(
                "SELECT COUNT(DISTINCT gateway) FROM core.\"order\" WHERE tenant_id = :t AND gateway IS NOT NULL"
            )
            or 0
        )
        has_skus = bool(
            await scalar("SELECT EXISTS(SELECT 1 FROM core.order_line WHERE tenant_id = :t AND sku IS NOT NULL)")
        )
        pincodes_with_signal = int(
            await scalar(
                "SELECT COUNT(*) FROM ("
                "  SELECT 1 FROM core.\"order\" WHERE tenant_id = :t AND shipping_pincode IS NOT NULL "
                "  GROUP BY shipping_pincode HAVING COUNT(*) >= 20"
                ") AS p"
            )
            or 0
        )

    probe = TenantProbe(
        has_orders=has_orders,
        has_shipments_with_rto=has_shipments_with_rto,
        has_campaigns=has_campaigns,
        has_gateway_diversity=gateway_diversity >= 2,
        has_skus=has_skus,
        pincodes_with_signal=pincodes_with_signal,
    )
    log.info("tenant probe %s: %s", tenant_id[:8], probe)
    return probe


def _load_manual_examples() -> list[dict[str, Any]]:
    return json.loads(EXAMPLES_PATH.read_text())


def _dedupe_by_question(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = " ".join(r["question"].lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


async def main_async(*, dry_run: bool, auto: bool, tenant_id: str | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not settings.gemini_api_key and not dry_run:
        log.error("GEMINI_API_KEY is empty. Set it in .env or pass --dry-run.")
        sys.exit(1)

    manual = _load_manual_examples()
    log.info("loaded %d manual examples from %s", len(manual), EXAMPLES_PATH)

    if auto:
        probe = await _probe_tenant(tenant_id)
        auto_examples = generate(probe, today=date.today())
        log.info("generated %d examples from schema + tenant probe", len(auto_examples))
        all_examples = _dedupe_by_question(manual + auto_examples)
        log.info(
            "after dedupe: %d total (manual wins on conflict, since manual goes first)",
            len(all_examples),
        )
    else:
        all_examples = manual

    if dry_run:
        for e in all_examples:
            log.info("  would embed + insert: %r", e["question"])
        log.info("dry-run done; %d examples skipped", len(all_examples))
        return

    embed = GeminiEmbeddings()
    async with SessionLocal() as s:
        for example in all_examples:
            q = example["question"]
            log.info("embedding: %s", q[:60])
            vec = await embed.embed(q)
            assert len(vec) == 3072, f"expected 3072-dim embedding, got {len(vec)}"
            await s.execute(
                text(
                    "DELETE FROM core.few_shot_examples "
                    "WHERE question = :q AND embedding_version = :v"
                ),
                {"q": q, "v": "v1"},
            )
            await s.execute(
                text("""
                  INSERT INTO core.few_shot_examples (
                    tenant_id, question, plan, embedding,
                    source_record_url, fetched_at, ingested_at,
                    embedding_model, embedding_version
                  ) VALUES (
                    NULL, :q, CAST(:p AS jsonb), CAST(:e AS halfvec),
                    :url, :ts, now(),
                    'gemini-embedding-001', 'v1'
                  )
                """),
                {
                    "q": q,
                    "p": json.dumps(example["plan"]),
                    "e": _format_halfvec(vec),
                    "url": (
                        "internal://semantic_layer/examples.json#"
                        + hashlib.sha256(q.encode()).hexdigest()[:8]
                    ),
                    "ts": datetime.now(UTC),
                },
            )
        await s.commit()
    log.info("done — %d examples embedded and stored", len(all_examples))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="log what would be embedded, write nothing")
    ap.add_argument(
        "--auto",
        action="store_true",
        help="union the manual examples with auto-generated ones from metrics.yml",
    )
    ap.add_argument(
        "--tenant-id",
        default=None,
        help="if set with --auto, probe this tenant's data to gate which examples are generated",
    )
    args = ap.parse_args()
    asyncio.run(main_async(dry_run=args.dry_run, auto=args.auto, tenant_id=args.tenant_id))


if __name__ == "__main__":
    main()
