from __future__ import annotations

from typing import TYPE_CHECKING, Any

from packages.udm.normalize._provenance import provenance_columns
from packages.udm.xref import canonical_id

if TYPE_CHECKING:
    from packages.connectors.base import Record

CONNECTOR_VERSION = "meta_ads@0.1.0"


def campaign_from_meta(
    record: Record,
    tenant_id: str,
    raw_row_id: int,
) -> dict[str, Any]:
    p = record.payload
    return {
        "tenant_id": tenant_id,
        "canonical_id": canonical_id(tenant_id, "campaign", "meta_ads", record.primary_key),
        "platform": "meta",
        "name": p.get("name"),
        "objective": p.get("objective"),
        "status": p.get("status"),
        **provenance_columns(
            record=record,
            raw_table="raw.meta_campaigns",
            raw_row_id=raw_row_id,
            connector_version=CONNECTOR_VERSION,
            source_system="meta_ads",
        ),
    }


def ad_spend_daily_from_meta(
    record: Record,
    tenant_id: str,
    raw_row_id: int,
) -> dict[str, Any]:
    p = record.payload
    return {
        "tenant_id": tenant_id,
        "date": p["date_start"],
        "campaign_canonical_id": canonical_id(
            tenant_id, "campaign", "meta_ads", str(p["campaign_id"])
        ),
        "ad_set_id": p.get("ad_set_id"),
        "ad_id": str(p["ad_id"]),
        "impressions": int(p.get("impressions", 0)) if p.get("impressions") else None,
        "clicks": int(p.get("clicks", 0)) if p.get("clicks") else None,
        "spend": float(p["spend"]),
        "currency": p.get("currency", "INR"),
        "conversions": (int(p.get("conversions", 0)) if p.get("conversions") is not None else None),
        "revenue_attributed": None,  # derived via roas × spend; left as None for v0
        **provenance_columns(
            record=record,
            raw_table="raw.meta_ad_insights",
            raw_row_id=raw_row_id,
            connector_version=CONNECTOR_VERSION,
            source_system="meta_ads",
        ),
    }
