from datetime import UTC, datetime

from packages.connectors.base import Record
from packages.udm.normalize.shopify_to_udm import (
    customer_from_shopify,
    order_from_shopify,
    order_line_from_shopify,
    product_from_shopify,
    refund_from_shopify,
)


def _order_record():
    return Record(
        stream="orders",
        primary_key="12345",
        payload={
            "id": 12345,
            "name": "#1001",
            "created_at": "2026-05-01T00:00:00Z",
            "updated_at": "2026-05-01T00:00:00Z",
            "total_price": "1234.50",
            "subtotal_price": "1100",
            "total_tax": "100",
            "total_discounts": "0",
            "total_shipping_price_set": {"shop_money": {"amount": "34.50"}},
            "currency": "INR",
            "gateway": "Cash on Delivery",
            "financial_status": "pending",
            "shipping_address": {"zip": "110084"},
            "customer": {"id": 7},
            "line_items": [],
            "note_attributes": [{"name": "utm_campaign", "value": "camp-3"}],
        },
        source_record_url="https://m000.myshopify.com/admin/orders/12345",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def test_order_normalizes_to_canonical_with_provenance():
    rec = _order_record()
    row = order_from_shopify(rec, tenant_id="t1", raw_row_id=42)
    assert row["total"] == 1234.50
    assert row["gateway"] == "Cash on Delivery"
    assert row["shipping_pincode"] == "110084"
    assert row["utm_campaign"] == "camp-3"
    # provenance — all 9 columns
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
    assert row["source_record_url"] == "https://m000.myshopify.com/admin/orders/12345"
    assert row["raw_table"] == "raw.shopify_orders"
    assert row["raw_row_id"] == 42
    assert row["raw_payload_hash"] == rec.payload_hash
    assert row["source_system"] == "shopify"
    assert row["connector_version"] == "shopify@0.1.0"


def test_order_canonical_id_matches_xref_for_xref_join():
    """Critical: shipment.order_canonical_id must equal order.canonical_id
    so joins resolve. Both must use canonical_id(tenant, 'order', 'shopify', source_id)."""
    from packages.udm.xref import canonical_id

    rec = _order_record()
    row = order_from_shopify(rec, tenant_id="t1", raw_row_id=42)
    expected = canonical_id("t1", "order", "shopify", "12345")
    assert row["canonical_id"] == expected


def test_order_customer_canonical_id_is_set_from_payload():
    rec = _order_record()
    row = order_from_shopify(rec, tenant_id="t1", raw_row_id=42)
    from packages.udm.xref import canonical_id

    assert row["customer_canonical_id"] == canonical_id("t1", "customer", "shopify", "7")


def test_order_line_normalizes_with_correct_order_canonical_id():
    rec = Record(
        stream="line_items",
        primary_key="12345:li-1",
        payload={"id": "li-1", "_order_id": 12345, "sku": "SKU-1", "quantity": 2, "price": "500"},
        source_record_url="https://m000.myshopify.com/admin/orders/12345",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    row = order_line_from_shopify(rec, tenant_id="t1", raw_row_id=99)
    from packages.udm.xref import canonical_id

    assert row["order_canonical_id"] == canonical_id("t1", "order", "shopify", "12345")
    assert row["sku"] == "SKU-1"
    assert row["qty"] == 2
    assert row["unit_price"] == 500.0
    assert row["line_total"] == 1000.0
    assert row["raw_table"] == "raw.shopify_line_items"


def test_customer_email_phone_hashed_not_plaintext():
    rec = Record(
        stream="customers",
        primary_key="cust-7",
        payload={
            "id": 7,
            "email": "abc@example.com",
            "phone": "+919999999999",
            "default_address": {"country_code": "IN"},
            "created_at": "2026-01-01T00:00:00Z",
        },
        source_record_url="https://m000.myshopify.com/admin/customers/7",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    row = customer_from_shopify(rec, tenant_id="t1", raw_row_id=1)
    assert row["email_hash"] != "abc@example.com"
    assert len(row["email_hash"]) == 64  # sha256
    assert len(row["phone_hash"]) == 64
    assert row["country"] == "IN"


def test_product_normalizes_with_sku_canonical_id():
    rec = Record(
        stream="products",
        primary_key="SKU-7",
        payload={"sku": "SKU-7", "title": "Cool Tee", "price": "499.00", "currency": "INR"},
        source_record_url="https://m000.myshopify.com/admin/products?sku=SKU-7",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    row = product_from_shopify(rec, tenant_id="t1", raw_row_id=5)
    from packages.udm.xref import canonical_id

    assert row["canonical_id"] == canonical_id("t1", "product", "shopify", "SKU-7")
    assert row["sku"] == "SKU-7"
    assert row["title"] == "Cool Tee"
    assert row["price"] == 499.00
    assert row["currency"] == "INR"
    assert row["raw_table"] == "raw.shopify_products"
    assert row["source_system"] == "shopify"
    assert row["raw_payload_hash"] == rec.payload_hash


def test_refund_normalizes_with_order_canonical_id():
    rec = Record(
        stream="refunds",
        primary_key="refund-12-1",
        payload={
            "id": "refund-12-1",
            "amount": "250.00",
            "reason": "damaged",
            "created_at": "2026-05-03T10:00:00Z",
            "_order_id": 12345,
        },
        source_record_url="https://m000.myshopify.com/admin/orders/12345#refund-refund-12-1",
        fetched_at=datetime(2026, 5, 3, tzinfo=UTC),
    )
    row = refund_from_shopify(rec, tenant_id="t1", raw_row_id=11)
    from packages.udm.xref import canonical_id

    assert row["canonical_id"] == canonical_id("t1", "refund", "shopify", "refund-12-1")
    assert row["order_canonical_id"] == canonical_id("t1", "order", "shopify", "12345")
    assert row["amount"] == 250.00
    assert row["reason"] == "damaged"
    assert row["refunded_at"] == "2026-05-03T10:00:00Z"
    assert row["raw_table"] == "raw.shopify_refunds"
    assert row["raw_payload_hash"] == rec.payload_hash
