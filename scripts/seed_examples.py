"""Embed the curated (question, plan) examples and INSERT into core.few_shot_examples.

Idempotent — re-running deletes prior rows by (question, embedding_version)
and re-inserts.

Requires GEMINI_API_KEY. Use --dry-run to print intended actions without
hitting Gemini.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from packages.config import settings  # noqa: E402
from packages.llm.embeddings import GeminiEmbeddings  # noqa: E402
from packages.warehouse.db import SessionLocal  # noqa: E402

log = logging.getLogger(__name__)

EXAMPLES_PATH = ROOT / "packages" / "semantic_layer" / "examples.json"


def _format_halfvec(vec: list[float]) -> str:
    """halfvec binding via psycopg accepts a literal string '[v1, v2, ...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def main_async(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not settings.gemini_api_key and not dry_run:
        log.error("GEMINI_API_KEY is empty. Set it in .env or pass --dry-run.")
        sys.exit(1)
    examples = json.loads(EXAMPLES_PATH.read_text())
    log.info("loaded %d examples from %s", len(examples), EXAMPLES_PATH)
    if dry_run:
        for e in examples:
            log.info("  would embed + insert: %r", e["question"])
        log.info("dry-run done; %d examples skipped", len(examples))
        return

    embed = GeminiEmbeddings()
    async with SessionLocal() as s:
        for example in examples:
            q = example["question"]
            log.info("embedding: %s", q[:60])
            vec = await embed.embed(q)
            assert len(vec) == 3072, f"expected 3072-dim embedding, got {len(vec)}"
            await s.execute(
                text("""
                  DELETE FROM core.few_shot_examples
                  WHERE question = :q AND embedding_version = :v
                """),
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
    log.info("done — %d examples embedded and stored", len(examples))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    main()
