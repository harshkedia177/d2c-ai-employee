from __future__ import annotations

import hashlib
from typing import Any

from packages.connectors.base import Record
from packages.udm.normalize._provenance import provenance_columns
from packages.udm.xref import canonical_id

CONNECTOR_VERSION = "shopify@0.1.0"


def _hash(s: str | None) -> str | None:
    if not s:
        return None
    return hashlib.sha256(s.encode()).hexdigest()


def _utm(payload: dict, name: str) -> str | None:
    for attr in payload.get("note_attributes", []) or []:
        if attr.get("name") == name:
            return attr.get("value")
    return None


def order_from_shopify(
    record: Record,
    tenant_id: str,
    raw_row_id: int,
) -> dict[str, Any]:
    p = record.payload
    cust_id_src = str(p.get("customer", {}).get("id", "")) or None
    return {
        "tenant_id": tenant_id,
        "canonical_id": canonical_id(tenant_id, "order", "shopify", record.primary_key),
        "customer_canonical_id": (
            canonical_id(tenant_id, "customer", "shopify", cust_id_src) if cust_id_src else None
        ),
        "placed_at": p.get("created_at"),
        "status": p.get("financial_status") or "unknown",
        "gateway": p.get("gateway"),
        "subtotal": float(p.get("subtotal_price")) if p.get("subtotal_price") else None,
        "tax": float(p.get("total_tax")) if p.get("total_tax") else None,
        "shipping_amount": (
            float(p["total_shipping_price_set"]["shop_money"]["amount"])
            if p.get("total_shipping_price_set")
            else None
        ),
        "discount": (float(p.get("total_discounts")) if p.get("total_discounts") else None),
        "total": float(p.get("total_price")) if p.get("total_price") else None,
        "currency": p.get("currency"),
        "shipping_pincode": (p.get("shipping_address") or {}).get("zip"),
        "utm_campaign": _utm(p, "utm_campaign"),
        "utm_source": _utm(p, "utm_source"),
        **provenance_columns(
            record=record,
            raw_table="raw.shopify_orders",
            raw_row_id=raw_row_id,
            connector_version=CONNECTOR_VERSION,
            source_system="shopify",
        ),
    }


def order_line_from_shopify(
    record: Record,
    tenant_id: str,
    raw_row_id: int,
) -> dict[str, Any]:
    p = record.payload
    order_src = str(p["_order_id"])
    return {
        "tenant_id": tenant_id,
        "order_canonical_id": canonical_id(tenant_id, "order", "shopify", order_src),
        "line_id": str(p["id"]),
        "product_canonical_id": None,
        "sku": p.get("sku"),
        "qty": int(p.get("quantity", 0)),
        "unit_price": float(p.get("price")) if p.get("price") else None,
        "line_total": (float(p["price"]) * int(p.get("quantity", 0)) if p.get("price") else None),
        "discount": None,
        **provenance_columns(
            record=record,
            raw_table="raw.shopify_line_items",
            raw_row_id=raw_row_id,
            connector_version=CONNECTOR_VERSION,
            source_system="shopify",
        ),
    }


def customer_from_shopify(
    record: Record,
    tenant_id: str,
    raw_row_id: int,
) -> dict[str, Any]:
    p = record.payload
    return {
        "tenant_id": tenant_id,
        "canonical_id": canonical_id(tenant_id, "customer", "shopify", record.primary_key),
        "email_hash": _hash(p.get("email")),
        "phone_hash": _hash(p.get("phone")),
        "country": (p.get("default_address") or {}).get("country_code"),
        "created_at": p.get("created_at"),
        **provenance_columns(
            record=record,
            raw_table="raw.shopify_customers",
            raw_row_id=raw_row_id,
            connector_version=CONNECTOR_VERSION,
            source_system="shopify",
        ),
    }


def product_from_shopify(
    record: Record,
    tenant_id: str,
    raw_row_id: int,
) -> dict[str, Any]:
    p = record.payload
    sku = str(p["sku"])
    return {
        "tenant_id": tenant_id,
        "canonical_id": canonical_id(tenant_id, "product", "shopify", sku),
        "sku": sku,
        "title": p.get("title"),
        "price": float(p["price"]) if p.get("price") else None,
        "currency": p.get("currency", "INR"),
        "cost_per_item": None,
        "vendor": p.get("vendor"),
        **provenance_columns(
            record=record,
            raw_table="raw.shopify_products",
            raw_row_id=raw_row_id,
            connector_version=CONNECTOR_VERSION,
            source_system="shopify",
        ),
    }


def refund_from_shopify(
    record: Record,
    tenant_id: str,
    raw_row_id: int,
) -> dict[str, Any]:
    p = record.payload
    order_src = str(p["_order_id"])
    return {
        "tenant_id": tenant_id,
        "canonical_id": canonical_id(tenant_id, "refund", "shopify", record.primary_key),
        "order_canonical_id": canonical_id(tenant_id, "order", "shopify", order_src),
        "amount": float(p["amount"]) if p.get("amount") else None,
        "reason": p.get("reason"),
        "refunded_at": p.get("created_at"),
        **provenance_columns(
            record=record,
            raw_table="raw.shopify_refunds",
            raw_row_id=raw_row_id,
            connector_version=CONNECTOR_VERSION,
            source_system="shopify",
        ),
    }
