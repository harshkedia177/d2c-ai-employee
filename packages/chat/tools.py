"""Chat tools. Every numerical-value tool returns a `provenance` field
with `query_hash` and `citations` — the citation-contract chokepoint."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from packages.semantic_layer.compiler import (
    compile_metric,
    list_dimensions,
    list_metrics,
)
from packages.warehouse.db import SessionLocal

log = logging.getLogger(__name__)

# query_hash → CompiledQuery, used by get_provenance.
_QUERY_CACHE: dict[str, dict[str, Any]] = {}

# Tests may monkeypatch _get_embeddings_client to inject a fake.
_embeddings_client: Any | None = None


def _get_embeddings_client() -> Any | None:
    global _embeddings_client
    if _embeddings_client is not None:
        return _embeddings_client
    from packages.config import settings

    if not settings.gemini_api_key:
        return None
    from packages.llm.embeddings import GeminiEmbeddings

    _embeddings_client = GeminiEmbeddings()
    return _embeddings_client


def _coerce_date_filter(filters: dict[str, Any]) -> dict[str, Any]:
    """Convert ISO-string date values to datetime.date so asyncpg can bind them."""
    out = dict(filters)
    DATE_FIELDS = (  # noqa: N806
        "placed_at",
        "shipped_at",
        "shipped_date",
        "delivered_at",
        "delivered_date",
        "rto_at",
        "date",
        "created_at",
        "timestamp",
        "ingested_at",
        "fetched_at",
    )
    for k, v in list(out.items()):
        if not isinstance(v, str):
            continue
        field = k.split("__")[0]
        if field in DATE_FIELDS:
            try:
                out[k] = date.fromisoformat(v[:10])
            except ValueError:
                with contextlib.suppress(ValueError):
                    out[k] = datetime.fromisoformat(v)
                # If both fail, leave as string; compiler may still accept.
    return out


async def get_schema(tenant_id: str, entity: str | None = None) -> dict[str, Any]:
    return {
        "metrics": list_metrics(),
        "dimensions": list_dimensions(),
    }


async def search_examples(tenant_id: str, question: str, k: int = 5) -> dict[str, Any]:
    """Find curated (question, plan) examples similar to a question.

    Primary: halfvec cosine NN against core.few_shot_examples (HNSW index).
    Fallback (no API key OR empty table): substring overlap on examples.json.
    """
    client = _get_embeddings_client()
    if client is not None:
        try:
            q_vec = await client.embed(question)
            q_literal = "[" + ",".join(f"{v:.6f}" for v in q_vec) + "]"
            async with SessionLocal() as s:
                result = await s.execute(
                    text("""
                      SELECT question, plan, source_record_url,
                             (embedding <=> CAST(:q AS halfvec)) AS distance
                      FROM core.few_shot_examples
                      WHERE embedding_version = 'v1'
                      ORDER BY embedding <=> CAST(:q AS halfvec)
                      LIMIT :k
                    """),
                    {"q": q_literal, "k": k},
                )
                rows = list(result.mappings())
            if rows:
                return {
                    "examples": [
                        {
                            "question": r["question"],
                            "plan": r["plan"],
                            "distance": (
                                float(r["distance"]) if r["distance"] is not None else None
                            ),
                            "source_record_url": r["source_record_url"],
                        }
                        for r in rows
                    ],
                    "retrieval": "halfvec_cosine_nn",
                }
            log.info("few_shot_examples is empty; falling back to substring search")
        except Exception as e:
            log.warning("embedding-based search failed: %s; falling back", e)

    examples_path = Path(__file__).parent.parent / "semantic_layer" / "examples.json"
    examples = json.loads(examples_path.read_text())
    qtokens = set(question.lower().split())

    def score(ex: dict) -> int:
        return len(qtokens & set(ex["question"].lower().split()))

    ranked = sorted(examples, key=score, reverse=True)[:k]
    return {"examples": ranked, "retrieval": "substring_fallback"}


async def compute_metric(
    tenant_id: str,
    metric_id: str,
    dimensions: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    grain: str | None = None,
) -> dict[str, Any]:
    """Compute a metric. Returns {value, provenance} for 0-dim or
    {rows, provenance} for ≥1-dim. `provenance.query_hash` is cached
    in _QUERY_CACHE and re-executable via get_provenance."""
    coerced = _coerce_date_filter(filters or {})
    cq = compile_metric(
        metric_id=metric_id,
        tenant_id=tenant_id,
        dimensions=dimensions or [],
        filters=coerced,
        grain=grain,
    )
    _QUERY_CACHE[cq.query_hash] = {
        "sql": cq.sql,
        "params": cq.params,
        "metric_id": metric_id,
        "dimensions": dimensions or [],
        "filters": coerced,
    }

    async with SessionLocal() as s:
        result = await s.execute(text(cq.sql), cq.params)
        rows = list(result.mappings())

    if not (dimensions or []):
        if not rows:
            return {
                "value": None,
                "provenance": {
                    "query_hash": cq.query_hash,
                    "metric_id": metric_id,
                    "grain": cq.grain,
                    "filters_applied": coerced,
                    "citations": [],
                    "sample_size": 0,
                },
            }
        row = rows[0]
        return {
            "value": float(row["value"]) if row["value"] is not None else None,
            "provenance": {
                "query_hash": cq.query_hash,
                "metric_id": metric_id,
                "grain": cq.grain,
                "filters_applied": coerced,
                "citations": list(row.get("citations") or []),
                "sample_size": int(row.get("sample_size") or 0),
                "min_sample_size": cq.min_sample_size,
            },
        }

    rendered_rows = []
    for r in rows:
        rendered_rows.append(
            {
                **{d: r[d] for d in (dimensions or []) if d in r},
                "value": float(r["value"]) if r["value"] is not None else None,
                "citations": list(r.get("citations") or []),
                "sample_size": int(r.get("sample_size") or 0),
                "below_min_sample": (
                    cq.min_sample_size > 0 and int(r.get("sample_size") or 0) < cq.min_sample_size
                ),
            }
        )
    return {
        "rows": rendered_rows,
        "provenance": {
            "query_hash": cq.query_hash,
            "metric_id": metric_id,
            "grain": cq.grain,
            "filters_applied": coerced,
            "dimensions": dimensions or [],
            "min_sample_size": cq.min_sample_size,
        },
    }


async def search_rows(
    tenant_id: str,
    entity: str,
    filter: dict[str, Any] | None = None,  # noqa: A002
    limit: int = 20,
) -> dict[str, Any]:
    """Inspect rows from a core.* table. Allowlisted entities only."""
    ALLOWED = {  # noqa: N806
        "order": 'core."order"',
        "shipment": "core.shipment",
        "refund": "core.refund",
        "campaign": "core.campaign",
        "ad_spend_daily": "core.ad_spend_daily",
        "agent_runs": "core.agent_runs",
    }
    if entity not in ALLOWED:
        raise ValueError(f"unknown entity: {entity}")
    table = ALLOWED[entity]

    where = "tenant_id = :tenant_id"
    params: dict[str, Any] = {"tenant_id": tenant_id, "limit": min(limit, 100)}
    for raw_key, value in (filter or {}).items():
        if "__" in raw_key:
            field, op = raw_key.rsplit("__", 1)
        else:
            field, op = raw_key, "eq"
        pname = raw_key.replace("__", "_")
        if op == "eq":
            where += f" AND {field} = :{pname}"
            params[pname] = value
        elif op == "gte":
            where += f" AND {field} >= :{pname}"
            params[pname] = value
        elif op == "lte":
            where += f" AND {field} <= :{pname}"
            params[pname] = value
        else:
            raise ValueError(f"unsupported op {op} for search_rows")

    sql = f"SELECT * FROM {table} WHERE {where} LIMIT :limit"
    async with SessionLocal() as s:
        result = await s.execute(text(sql), params)
        rows = [dict(r) for r in result.mappings()]

    return {
        "rows": rows,
        "provenance": {
            "entity": entity,
            "table": table,
            "filter": filter or {},
            "row_pks": [r.get("canonical_id") or r.get("run_id") for r in rows],
        },
    }


async def get_provenance(tenant_id: str, query_hash: str) -> dict[str, Any]:
    """Re-execute a previously compiled query (for footnote click-through)."""
    cached = _QUERY_CACHE.get(query_hash)
    if not cached:
        return {"error": f"no cached query for hash {query_hash}"}
    if cached["params"].get("tenant_id") != tenant_id:
        return {"error": "tenant mismatch"}
    async with SessionLocal() as s:
        result = await s.execute(text(cached["sql"]), cached["params"])
        rows = [dict(r) for r in result.mappings()]
    return {
        "rows": rows,
        "metric_id": cached["metric_id"],
        "filters": cached["filters"],
    }


async def run_sql(tenant_id: str, sql: str, enable: bool = False) -> dict[str, Any]:
    """Read-only SQL escape hatch. Disabled by default."""
    if not enable:
        return {
            "error": "run_sql disabled. Pass enable=True after operator review.",
        }
    if any(
        kw in sql.lower()
        for kw in ("insert ", "update ", "delete ", "drop ", "truncate ", "alter ")
    ):
        raise ValueError("run_sql is read-only")
    async with SessionLocal() as s:
        result = await s.execute(text(sql))
        rows = [dict(r) for r in result.mappings()]
    return {"rows": rows, "row_count": len(rows)}


async def propose_write(
    tenant_id: str,
    action_type: str,
    payload: dict[str, Any],
    dry_run: bool = True,
) -> dict[str, Any]:
    """Stage an action. Never executes — returns a structured diff."""
    KNOWN = {  # noqa: N806
        "downgrade_to_prepaid",
        "pause_campaign",
        "block_cod_pincode",
        "tag_order",
        "create_segment",
        "write_note",
        "reduce_budget",
    }
    if action_type not in KNOWN:
        return {
            "error": f"unknown action_type {action_type}. allowed: {sorted(KNOWN)}",
        }
    if not dry_run:
        return {
            "error": "dry_run=True only in v0. Real execution arrives in v1.",
        }
    return {
        "dry_run": True,
        "action_type": action_type,
        "payload": payload,
        "would_affect_row_pks": payload.get("row_pks", []),
        "summary": payload.get("summary") or f"would {action_type} with {payload}",
    }


TOOL_REGISTRY: dict[str, Any] = {
    "get_schema": get_schema,
    "search_examples": search_examples,
    "compute_metric": compute_metric,
    "search_rows": search_rows,
    "get_provenance": get_provenance,
    "run_sql": run_sql,
    "propose_write": propose_write,
}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_schema",
        "description": "Return semantic-layer metric and dimension definitions.",
        "parameters": {
            "type": "object",
            "properties": {"entity": {"type": "string"}},
        },
    },
    {
        "name": "search_examples",
        "description": "Find curated (question, plan) examples similar to a question.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "k": {"type": "integer", "default": 5},
            },
            "required": ["question"],
        },
    },
    {
        "name": "compute_metric",
        "description": (
            "Compute a single business metric. The ONLY way to get numbers in"
            " an answer. Returns {value | rows, provenance: {query_hash, "
            "citations}}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "metric_id": {"type": "string"},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "filters": {"type": "object"},
                "grain": {"type": "string"},
            },
            "required": ["metric_id"],
        },
    },
    {
        "name": "search_rows",
        "description": "Inspect rows from a canonical entity for qualitative grounding.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "enum": [
                        "order",
                        "shipment",
                        "refund",
                        "campaign",
                        "ad_spend_daily",
                        "agent_runs",
                    ],
                },
                "filter": {"type": "object"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "get_provenance",
        "description": "Re-execute a prior compute_metric query by query_hash.",
        "parameters": {
            "type": "object",
            "properties": {"query_hash": {"type": "string"}},
            "required": ["query_hash"],
        },
    },
    {
        "name": "run_sql",
        "description": "Read-only SQL escape hatch. Disabled by default.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "enable": {"type": "boolean", "default": False},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "propose_write",
        "description": "Stage an action diff. Always dry_run; never executes.",
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string"},
                "payload": {"type": "object"},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["action_type", "payload"],
        },
    },
]
