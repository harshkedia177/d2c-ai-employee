from __future__ import annotations

from typing import Any

from packages.connectors.base import Record
from packages.udm.normalize._provenance import provenance_columns
from packages.udm.xref import canonical_id

CONNECTOR_VERSION = "shiprocket@0.1.0"


def shipment_from_shiprocket(
    record: Record,
    tenant_id: str,
    raw_row_id: int,
    shopify_order_id_for_xref: str | None = None,
) -> dict[str, Any]:
    p = record.payload
    src_order_id = shopify_order_id_for_xref or str(p["order_id"])
    is_rto = bool(p.get("is_rto"))
    return {
        "tenant_id": tenant_id,
        "canonical_id": canonical_id(tenant_id, "shipment", "shiprocket", record.primary_key),
        "order_canonical_id": canonical_id(tenant_id, "order", "shopify", src_order_id),
        "carrier": p.get("courier_name"),
        "tracking_number": p.get("awb_code"),
        "status": p.get("current_status") or "unknown",
        "is_rto": is_rto,
        "freight_amount": (
            float(p["freight_charges"]) if p.get("freight_charges") is not None else None
        ),
        "shipped_at": p.get("shipped_date"),
        "delivered_at": p.get("delivered_date") if not is_rto else None,
        "rto_at": p.get("delivered_date") if is_rto else None,
        **provenance_columns(
            record=record,
            raw_table="raw.shiprocket_shipments",
            raw_row_id=raw_row_id,
            connector_version=CONNECTOR_VERSION,
            source_system="shiprocket",
        ),
    }
