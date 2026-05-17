"""Deterministic (question, plan) generator for the few-shot examples table.

Source: metrics.yml (`metrics_supported` per metric) + a small tenant-data probe.
Output: a list of {question, plan} dicts matching the shape in examples.json.

This is the auto-generated tier. Procedural / diagnostic questions stay manual
in examples.json. Both are unioned at seed time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from packages.semantic_layer.compiler import _load_config

Example = dict[str, Any]


@dataclass(frozen=True)
class TenantProbe:
    """What data the tenant actually has — gates which examples make sense."""

    has_orders: bool = True
    has_shipments_with_rto: bool = True
    has_campaigns: bool = True
    has_gateway_diversity: bool = True  # ≥2 distinct gateway values
    has_skus: bool = True
    pincodes_with_signal: int = 0  # pincodes with ≥20 shipments

    @classmethod
    def all_on(cls) -> TenantProbe:
        return cls(pincodes_with_signal=5)

    @classmethod
    def none(cls) -> TenantProbe:
        return cls(
            has_orders=False,
            has_shipments_with_rto=False,
            has_campaigns=False,
            has_gateway_diversity=False,
            has_skus=False,
            pincodes_with_signal=0,
        )


# Metrics that already encode a time window in their definition — the LLM
# should not be taught to filter them by an additional time range.
_BUILTIN_WINDOW_METRICS: frozenset[str] = frozenset(
    {"pincode_rto_rate_90d", "sku_rto_rate_90d"}
)


# Maps dimension name → tenant-probe flag that must be true.
_DIM_GATES: dict[str, str] = {
    "campaign": "has_campaigns",
    "ad_id": "has_campaigns",
    "gateway": "has_gateway_diversity",
    "pincode": "has_orders",
    "sku": "has_skus",
    "week": "has_orders",
    "month": "has_orders",
    "date": "has_orders",
}


# Stable human phrasings of each metric. The LLM matches on intent; embeddings
# pick up close paraphrasings even if the exact noun differs.
_METRIC_PHRASING: dict[str, dict[str, str]] = {
    "gmv": {"noun": "GMV", "verb": "revenue", "plain": "gross merchandise value"},
    "aov": {"noun": "AOV", "verb": "average order value", "plain": "average order value"},
    "rto_rate": {"noun": "RTO rate", "verb": "return rate", "plain": "return-to-origin rate"},
    "cac": {"noun": "CAC", "verb": "acquisition cost", "plain": "customer acquisition cost"},
    "post_rto_roas": {
        "noun": "post-RTO ROAS",
        "verb": "post-RTO ROAS",
        "plain": "post-RTO return on ad spend",
    },
    "contribution_margin_per_order": {
        "noun": "contribution margin per order",
        "verb": "contribution margin",
        "plain": "contribution margin per order",
    },
    "pincode_rto_rate_90d": {
        "noun": "pincode-level RTO rate",
        "verb": "pincode return rate",
        "plain": "90-day RTO rate per pincode",
    },
    "sku_rto_rate_90d": {
        "noun": "SKU-level RTO rate",
        "verb": "SKU return rate",
        "plain": "90-day RTO rate per SKU",
    },
}


_DIM_PLURAL: dict[str, str] = {
    "campaign": "campaigns",
    "ad_id": "ads",
    "pincode": "pincodes",
    "sku": "SKUs",
    "gateway": "payment gateways",
    "week": "weeks",
    "month": "months",
}


@dataclass(frozen=True)
class _Window:
    """A bounded time window expressed against the metric's declared time column."""

    label_short: str  # used in questions ("last 7 days")
    days_back: int


_WINDOWS: list[_Window] = [
    _Window("last 7 days", 7),
    _Window("last 30 days", 30),
    _Window("last 90 days", 90),
]


def _filter_for_window(metric_meta: dict, w: _Window, today: date) -> dict[str, str]:
    """Build the time filter dict for one window using the metric's declared time_column.

    Returns {} if the metric has no time_column (e.g. would be a no-time metric).
    """
    tc = metric_meta.get("time_column")
    if not tc:
        return {}
    since = today - timedelta(days=w.days_back)
    return {f"{tc}__gte": since.isoformat()}


def _filter_for_calendar_month(metric_meta: dict, anchor: date) -> dict[str, str]:
    """Filter covering the calendar month containing `anchor`."""
    tc = metric_meta.get("time_column")
    if not tc:
        return {}
    first = anchor.replace(day=1)
    if anchor.month == 12:
        next_first = anchor.replace(year=anchor.year + 1, month=1, day=1)
    else:
        next_first = anchor.replace(month=anchor.month + 1, day=1)
    last = next_first - timedelta(days=1)
    return {f"{tc}__gte": first.isoformat(), f"{tc}__lte": last.isoformat()}


def _plan_call(metric_id: str, args: dict) -> dict:
    return {"tool": "compute_metric", "args": {"metric_id": metric_id, **args}}


def _allowed_dims(metric_meta: dict, probe: TenantProbe) -> list[str]:
    declared = list(metric_meta.get("dimensions_supported") or [])
    out: list[str] = []
    for d in declared:
        gate = _DIM_GATES.get(d)
        if gate is None or getattr(probe, gate, False):
            out.append(d)
    return out


def _emit_basic(
    metric_id: str, meta: dict, probe: TenantProbe, today: date
) -> list[Example]:
    """Headline metric questions: 'what's my X?' and 'X for last N days?'."""
    phrasing = _METRIC_PHRASING.get(metric_id, {"noun": metric_id, "verb": metric_id})
    noun = phrasing["noun"]
    out: list[Example] = []
    out.append(
        {
            "question": f"What's my {noun}?",
            "plan": [_plan_call(metric_id, {})],
        }
    )
    out.append(
        {
            "question": f"Show me my {phrasing.get('verb', noun)}",
            "plan": [_plan_call(metric_id, {})],
        }
    )
    if meta.get("time_column") and metric_id not in _BUILTIN_WINDOW_METRICS:
        for w in _WINDOWS:
            out.append(
                {
                    "question": f"What's my {noun} for the {w.label_short}?",
                    "plan": [
                        _plan_call(metric_id, {"filters": _filter_for_window(meta, w, today)})
                    ],
                }
            )
    return out


def _emit_dim_breakdowns(
    metric_id: str, meta: dict, probe: TenantProbe, today: date
) -> list[Example]:
    """X by dimension / top-N by dimension. Skipped if probe says no signal."""
    phrasing = _METRIC_PHRASING.get(metric_id, {"noun": metric_id})
    noun = phrasing["noun"]
    out: list[Example] = []
    for dim in _allowed_dims(meta, probe):
        plural = _DIM_PLURAL.get(dim, dim)
        out.append(
            {
                "question": f"What's my {noun} by {dim}?",
                "plan": [_plan_call(metric_id, {"dimensions": [dim]})],
            }
        )
        # "Top N" framing — only for dims where ranking is meaningful (skip week/month).
        if dim not in ("week", "month", "date"):
            out.append(
                {
                    "question": f"Top 5 {plural} by {noun}",
                    "plan": [_plan_call(metric_id, {"dimensions": [dim]})],
                }
            )
    return out


def _emit_mom_compare(
    metric_id: str, meta: dict, probe: TenantProbe, today: date
) -> list[Example]:
    """Month-over-month comparison — two compute_metric calls with bracketed filters."""
    if not meta.get("time_column") or metric_id in _BUILTIN_WINDOW_METRICS:
        return []
    this_month_first = today.replace(day=1)
    if this_month_first.month == 1:
        last_month_anchor = this_month_first.replace(year=this_month_first.year - 1, month=12)
    else:
        last_month_anchor = this_month_first.replace(month=this_month_first.month - 1)
    phrasing = _METRIC_PHRASING.get(metric_id, {"noun": metric_id})
    noun = phrasing["noun"]
    return [
        {
            "question": f"Compare my {noun} this month vs last month",
            "plan": [
                _plan_call(metric_id, {"filters": _filter_for_calendar_month(meta, today)}),
                _plan_call(
                    metric_id,
                    {"filters": _filter_for_calendar_month(meta, last_month_anchor)},
                ),
            ],
        }
    ]


def _emit_agent_mirrors(probe: TenantProbe, today: date, config: dict) -> list[Example]:
    """Questions that mirror the autonomous agents' decision inputs — same metrics, same
    filters — so a chat answer triangulates with what the agent saw."""
    out: list[Example] = []
    metrics = config["metrics"]

    if probe.pincodes_with_signal > 0 and "pincode_rto_rate_90d" in metrics:
        cm_filters = _filter_for_window(
            metrics["contribution_margin_per_order"], _Window("last 90 days", 90), today
        )
        out.append(
            {
                "question": "Should I block COD for any pincodes? Walk me through the decision.",
                "plan": [
                    _plan_call("pincode_rto_rate_90d", {"dimensions": ["pincode"]}),
                    _plan_call("contribution_margin_per_order", {"filters": cm_filters}),
                ],
            }
        )

    if probe.has_campaigns and "post_rto_roas" in metrics and "cac" in metrics:
        roas_filters = _filter_for_window(metrics["post_rto_roas"], _Window("last 14 days", 14), today)
        cac_filters = _filter_for_window(metrics["cac"], _Window("last 14 days", 14), today)
        out.append(
            {
                "question": "Which campaigns should I pause — underperforming with high spend?",
                "plan": [
                    _plan_call(
                        "post_rto_roas",
                        {"dimensions": ["campaign"], "filters": roas_filters},
                    ),
                    _plan_call("cac", {"dimensions": ["campaign"], "filters": cac_filters}),
                ],
            }
        )

    return out


def _emit_pulse(probe: TenantProbe, today: date, config: dict) -> list[Example]:
    """Weekly business pulse — four core metrics for the last 7 days, one query each."""
    metrics = config["metrics"]
    metric_gates: dict[str, bool] = {
        "gmv": probe.has_orders,
        "aov": probe.has_orders,
        "rto_rate": probe.has_shipments_with_rto,
        "cac": probe.has_campaigns,
    }
    plan: list[dict] = []
    for mid, ok in metric_gates.items():
        if not ok or mid not in metrics:
            continue
        filt = _filter_for_window(metrics[mid], _Window("last 7 days", 7), today)
        if filt:
            plan.append(_plan_call(mid, {"filters": filt}))
    if len(plan) < 2:
        return []
    return [
        {
            "question": "What's my weekly business pulse — give me the top KPIs",
            "plan": plan,
        }
    ]


def generate(probe: TenantProbe | None = None, today: date | None = None) -> list[Example]:
    """Generate the full deterministic example set for one tenant.

    `today` is injected so tests can pin filter dates. Defaults to date.today().
    `probe` defaults to all-on (every metric+dim emitted).
    """
    probe = probe or TenantProbe.all_on()
    today = today or date.today()
    config = _load_config()
    metrics: dict[str, dict] = config["metrics"]

    out: list[Example] = []
    for metric_id, meta in metrics.items():
        # Skip metrics whose primary join requires data the tenant lacks.
        if metric_id in ("cac", "post_rto_roas") and not probe.has_campaigns:
            continue
        if metric_id in ("rto_rate", "pincode_rto_rate_90d", "sku_rto_rate_90d") and not probe.has_shipments_with_rto:
            continue
        if metric_id in ("sku_rto_rate_90d",) and not probe.has_skus:
            continue
        if metric_id in ("pincode_rto_rate_90d",) and probe.pincodes_with_signal == 0:
            continue
        out.extend(_emit_basic(metric_id, meta, probe, today))
        out.extend(_emit_dim_breakdowns(metric_id, meta, probe, today))
        out.extend(_emit_mom_compare(metric_id, meta, probe, today))

    out.extend(_emit_agent_mirrors(probe, today, config))
    out.extend(_emit_pulse(probe, today, config))

    return _dedupe(out)


def _dedupe(examples: list[Example]) -> list[Example]:
    """Drop duplicate questions (case-insensitive, whitespace-collapsed)."""
    seen: set[str] = set()
    out: list[Example] = []
    for e in examples:
        key = " ".join(e["question"].lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
