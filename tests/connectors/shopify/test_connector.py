import httpx
import respx

from packages.connectors.shopify.connector import ShopifyConnector


def test_streams_returns_expected_streams():
    c = ShopifyConnector()
    names = {s.name for s in c.streams({})}
    assert names == {
        "orders",
        "line_items",
        "products",
        "customers",
        "refunds",
        "fulfillments",
    }


def test_orders_stream_has_updated_at_cursor():
    c = ShopifyConnector()
    by_name = {s.name: s for s in c.streams({})}
    assert by_name["orders"].cursor_field == "updated_at"
    assert by_name["orders"].primary_key == "id"


@respx.mock
def test_read_orders_yields_records_with_provenance():
    respx.get("http://localhost:9000/shopify/m000/admin/api/2026-01/orders.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "orders": [
                    {
                        "id": 12345,
                        "name": "#1001",
                        "updated_at": "2026-05-01T10:00:00Z",
                        "created_at": "2026-05-01T10:00:00Z",
                        "total_price": "1000",
                        "currency": "INR",
                        "gateway": "razorpay",
                        "shipping_address": {"zip": "560001"},
                        "line_items": [],
                        "customer": {"id": 1},
                    }
                ]
            },
        )
    )
    c = ShopifyConnector()
    cfg = {
        "merchant": "m000",
        "base_url": "http://localhost:9000/shopify",
        "shop_domain": "m000.myshopify.com",
    }
    out = list(c.read("orders", cfg, state=None))
    records = [r for r in out if hasattr(r, "primary_key")]
    checkpoints = [r for r in out if not hasattr(r, "primary_key")]
    assert len(records) == 1
    assert records[0].primary_key == "12345"
    assert records[0].source_record_url == ("https://m000.myshopify.com/admin/orders/12345")
    assert records[0].payload_hash
    # one Checkpoint emitted for the page
    assert len(checkpoints) == 1
    assert checkpoints[0].cursor["updated_at_min"] == "2026-05-01T10:00:00Z"


@respx.mock
def test_read_orders_emits_line_items_inline():
    respx.get("http://localhost:9000/shopify/m000/admin/api/2026-01/orders.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "orders": [
                    {
                        "id": 999,
                        "updated_at": "2026-05-02T00:00:00Z",
                        "total_price": "500",
                        "line_items": [
                            {"id": "li-1", "sku": "SKU-1", "quantity": 1, "price": "500"},
                            {"id": "li-2", "sku": "SKU-2", "quantity": 2, "price": "250"},
                        ],
                    }
                ]
            },
        )
    )
    c = ShopifyConnector()
    cfg = {
        "merchant": "m000",
        "base_url": "http://localhost:9000/shopify",
        "shop_domain": "m000.myshopify.com",
    }
    out = list(c.read("orders", cfg, state=None))
    records = [r for r in out if hasattr(r, "primary_key")]
    streams = [r.stream for r in records]
    assert streams.count("orders") == 1
    assert streams.count("line_items") == 2
    line = next(r for r in records if r.stream == "line_items" and r.primary_key == "999:li-1")
    assert line.payload["_order_id"] == 999


@respx.mock
def test_read_orders_uses_cursor_in_query():
    route = respx.get("http://localhost:9000/shopify/m000/admin/api/2026-01/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": []})
    )
    c = ShopifyConnector()
    cfg = {
        "merchant": "m000",
        "base_url": "http://localhost:9000/shopify",
        "shop_domain": "m000.myshopify.com",
    }
    list(c.read("orders", cfg, state={"updated_at_min": "2026-04-01T00:00:00Z"}))
    assert route.called
    request = route.calls.last.request
    assert "updated_at_min=2026-04-01T00%3A00%3A00Z" in str(request.url)


def test_read_unknown_stream_returns_empty():
    c = ShopifyConnector()
    cfg = {
        "merchant": "m000",
        "base_url": "http://localhost:9000/shopify",
        "shop_domain": "m000.myshopify.com",
    }
    assert list(c.read("products", cfg, None)) == []
    assert list(c.read("customers", cfg, None)) == []
    assert list(c.read("line_items", cfg, None)) == []
