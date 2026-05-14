"""Compile a MetricRequest into SQL with mandatory citation projection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).parent / "metrics.yml"


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: dict[str, Any]
    metric_id: str
    grain: str
    dimensions: list[str]
    filters: dict[str, Any]
    query_hash: str
    min_sample_size: int = 0


def _load_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _parse_citation_select(citation_select: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in citation_select.split(","):
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        idx = upper.find(" AS ")
        if idx == -1:
            raise ValueError(f"citation_select entry missing AS: {line!r}")
        expr = line[:idx].strip()
        alias = line[idx + 4 :].strip()
        out.append((expr, alias))
    return out


# Field names the LLM commonly emits as "the time filter" regardless of the
# metric's actual time column. Compiler rewrites these to the metric's
# canonical `time_column` when one is declared in metrics.yml.
_TIME_ALIASES = frozenset(
    {"date", "placed_at", "shipped_at", "delivered_at", "rto_at", "created_at", "timestamp"}
)


def _rewrite_time_filters(
    filters: dict[str, Any], time_column: str | None
) -> dict[str, Any]:
    if not time_column:
        return filters
    out: dict[str, Any] = {}
    for raw_key, value in filters.items():
        if "__" in raw_key:
            field, op = raw_key.rsplit("__", 1)
        else:
            field, op = raw_key, "eq"
        if field in _TIME_ALIASES and field != time_column:
            new_key = f"{time_column}__{op}" if op != "eq" else time_column
            out[new_key] = value
        else:
            out[raw_key] = value
    return out


def _filter_clause(filters: dict[str, Any], base_alias: str) -> tuple[str, dict[str, Any]]:
    parts: list[str] = []
    params: dict[str, Any] = {}
    for raw_key, value in filters.items():
        if "__" in raw_key:
            field, op = raw_key.rsplit("__", 1)
        else:
            field, op = raw_key, "eq"
        qualified = f"{base_alias}.{field}" if "." not in field else field
        param_name = raw_key.replace(".", "_").replace("__", "_")
        if op == "gte":
            parts.append(f"{qualified} >= :{param_name}")
            params[param_name] = value
        elif op == "lte":
            parts.append(f"{qualified} <= :{param_name}")
            params[param_name] = value
        elif op == "eq":
            parts.append(f"{qualified} = :{param_name}")
            params[param_name] = value
        elif op == "in":
            if not isinstance(value, list | tuple) or not value:
                raise ValueError(f"`in` filter requires non-empty list: {raw_key}")
            placeholders = []
            for i, v in enumerate(value):
                pname = f"{param_name}_{i}"
                placeholders.append(f":{pname}")
                params[pname] = v
            parts.append(f"{qualified} IN ({', '.join(placeholders)})")
        else:
            raise ValueError(f"unknown filter operator: {op}")
    if not parts:
        return "", params
    return " AND " + " AND ".join(parts), params


def compile_metric(
    metric_id: str,
    tenant_id: str,
    dimensions: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    grain: str | None = None,
    citation_limit: int = 1000,
) -> CompiledQuery:
    config = _load_config()
    metrics = config["metrics"]
    if metric_id not in metrics:
        raise ValueError(f"unknown metric: {metric_id}")
    meta = metrics[metric_id]
    base_alias = meta["base_alias"]
    base_table = meta["base_table"]
    aggregation = meta["sql_aggregation"].strip()
    joins_sql = (meta.get("joins") or "").strip()
    citation_select = meta["citation_select"]
    min_sample = meta.get("min_sample_size", 0)

    citation_parts = _parse_citation_select(citation_select)
    cite_map = {alias: expr for expr, alias in citation_parts}
    required_aliases = [
        "_source_system",
        "_source_id",
        "_source_record_url",
        "_raw_table",
        "_raw_row_id",
    ]
    for a in required_aliases:
        if a not in cite_map:
            raise ValueError(f"metric {metric_id} citation_select missing alias {a}")

    dim_cfg = config.get("dimensions", {}) or {}
    dimensions = dimensions or []
    dim_select_parts: list[str] = []
    dim_group_parts: list[str] = []
    for d in dimensions:
        if d not in dim_cfg:
            raise ValueError(f"unknown dimension: {d}")
        dim_sql = dim_cfg[d]["sql"]
        dim_select_parts.append(f"{dim_sql} AS {d}")
        dim_group_parts.append(dim_sql)

    filters = _rewrite_time_filters(filters or {}, meta.get("time_column"))
    where_sql, params = _filter_clause(filters, base_alias)
    params["tenant_id"] = tenant_id
    params["citation_limit"] = citation_limit

    citations_expr = (
        "(ARRAY_AGG(jsonb_build_object("
        f"'source_system', {cite_map['_source_system']}, "
        f"'source_id', {cite_map['_source_id']}, "
        f"'url', {cite_map['_source_record_url']}, "
        f"'raw_table', {cite_map['_raw_table']}, "
        f"'raw_row_id', {cite_map['_raw_row_id']}"
        ")))[1:CAST(:citation_limit AS int)] AS citations"
    )

    select_lines: list[str] = []
    if dim_select_parts:
        select_lines.extend(dim_select_parts)
    select_lines.append(f"({aggregation}) AS value")
    select_lines.append(citations_expr)
    select_lines.append("COUNT(*) AS sample_size")

    select_sql = ",\n  ".join(select_lines)

    group_by_sql = "GROUP BY " + ", ".join(dim_group_parts) if dim_group_parts else ""

    sql = (
        f"SELECT\n  {select_sql}\n"
        f"FROM {base_table} {base_alias}\n"
        f"{joins_sql}\n"
        f"WHERE {base_alias}.tenant_id = :tenant_id{where_sql}\n"
        f"{group_by_sql}"
    ).strip()

    h = hashlib.sha256(sql.encode() + repr(sorted(params.items())).encode()).hexdigest()
    return CompiledQuery(
        sql=sql,
        params=params,
        metric_id=metric_id,
        grain=grain or meta["grain"],
        dimensions=dimensions,
        filters=filters or {},
        query_hash=h,
        min_sample_size=min_sample,
    )


def list_metrics() -> list[dict[str, Any]]:
    config = _load_config()
    out = []
    for name, meta in config["metrics"].items():
        time_col = meta.get("time_column")
        out.append(
            {
                "id": name,
                "description": meta["description"],
                "grain": meta["grain"],
                "min_sample_size": meta.get("min_sample_size", 0),
                "time_column": time_col,
                "filter_examples": (
                    [f"{time_col}__gte", f"{time_col}__lte"] if time_col else []
                ),
            }
        )
    return out


def list_dimensions() -> list[dict[str, str]]:
    config = _load_config()
    return [{"id": k, "sql": v["sql"]} for k, v in (config.get("dimensions") or {}).items()]
