from datetime import UTC, datetime

from packages.connectors.base import Record
from packages.udm.normalize.shiprocket_to_udm import shipment_from_shiprocket
from packages.udm.xref import canonical_id


def test_rto_shipment_sets_is_rto_and_rto_at():
    rec = Record(
        stream="shipments",
        primary_key="sr-1",
        payload={
            "shipment_id": "sr-1",
            "order_id": "shopify-m000-000123",
            "awb_code": "AWB123",
            "courier_name": "Delhivery",
            "current_status": "RTO Delivered",
            "is_rto": True,
            "freight_charges": 60.0,
            "shipped_date": "2026-05-01T00:00:00+00:00",
            "delivered_date": "2026-05-04T00:00:00+00:00",
        },
        source_record_url="https://app.shiprocket.in/orders/sr-1",
        fetched_at=datetime(2026, 5, 4, tzinfo=UTC),
    )
    row = shipment_from_shiprocket(rec, tenant_id="t1", raw_row_id=5)
    assert row["is_rto"] is True
    assert row["rto_at"] == "2026-05-04T00:00:00+00:00"
    assert row["delivered_at"] is None
    assert row["carrier"] == "Delhivery"
    assert row["status"] == "RTO Delivered"


def test_shipment_order_canonical_id_matches_shopify_order_canonical_id():
    rec = Record(
        stream="shipments",
        primary_key="sr-1",
        payload={
            "shipment_id": "sr-1",
            "order_id": "shopify-m000-000123",
            "current_status": "Delivered",
            "is_rto": False,
            "shipped_date": "2026-05-01T00:00:00+00:00",
        },
        source_record_url="https://app.shiprocket.in/orders/sr-1",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    row = shipment_from_shiprocket(rec, tenant_id="t1", raw_row_id=5)
    expected = canonical_id("t1", "order", "shopify", "shopify-m000-000123")
    assert row["order_canonical_id"] == expected


def test_shipment_provenance_columns_present():
    rec = Record(
        stream="shipments",
        primary_key="sr-1",
        payload={
            "shipment_id": "sr-1",
            "order_id": "shopify-m000-000123",
            "current_status": "Delivered",
            "is_rto": False,
            "shipped_date": "2026-05-01T00:00:00+00:00",
        },
        source_record_url="https://app.shiprocket.in/orders/sr-1",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    row = shipment_from_shiprocket(rec, tenant_id="t1", raw_row_id=5)
    for col in [
        "source_system",
        "source_id",
        "source_record_url",
        "raw_table",
        "raw_row_id",
        "raw_payload_hash",
        "fetched_at",
        "ingested_at",
        "connector_version",
    ]:
        assert col in row
    assert row["source_system"] == "shiprocket"
    assert row["raw_table"] == "raw.shiprocket_shipments"
