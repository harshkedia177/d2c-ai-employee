from unittest.mock import MagicMock

import httpx
import respx

from packages.connectors.meta_ads.connector import MetaAdsConnector
from packages.connectors.shiprocket.connector import ShiprocketConnector
from packages.connectors.shopify.connector import ShopifyConnector


@respx.mock
def test_shopify_connector_acquires_before_http():
    respx.get("http://localhost:9000/shopify/m000/admin/api/2026-01/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": []})
    )
    fake_bucket = MagicMock()
    fake_bucket.acquire_sync = MagicMock(return_value=None)
    c = ShopifyConnector()
    cfg = {
        "merchant": "m000",
        "base_url": "http://localhost:9000/shopify",
        "shop_domain": "m000.myshopify.com",
        "rate_limiter": fake_bucket,
    }
    list(c.read("orders", cfg, state=None))
    assert fake_bucket.acquire_sync.call_count >= 1


@respx.mock
def test_shopify_connector_works_without_rate_limiter():
    respx.get("http://localhost:9000/shopify/m000/admin/api/2026-01/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": []})
    )
    c = ShopifyConnector()
    cfg = {
        "merchant": "m000",
        "base_url": "http://localhost:9000/shopify",
        "shop_domain": "m000.myshopify.com",
    }
    out = list(c.read("orders", cfg, state=None))
    assert isinstance(out, list)


@respx.mock
def test_shiprocket_connector_acquires_before_http():
    respx.post("http://localhost:9000/shiprocket/v1/external/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "fake-token", "expires_in": 240 * 3600})
    )
    respx.get("http://localhost:9000/shiprocket/v1/external/orders").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    fake_bucket = MagicMock()
    fake_bucket.acquire_sync = MagicMock(return_value=None)
    ShiprocketConnector._token_cache.clear()
    c = ShiprocketConnector()
    cfg = {
        "merchant": "m000",
        "base_url": "http://localhost:9000/shiprocket",
        "email": "demo@x.com",
        "password": "demo",
        "rate_limiter": fake_bucket,
    }
    list(c.read("shipments", cfg, state=None))
    assert fake_bucket.acquire_sync.call_count >= 2


@respx.mock
def test_shiprocket_connector_works_without_rate_limiter():
    respx.post("http://localhost:9000/shiprocket/v1/external/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "fake-token-2", "expires_in": 240 * 3600})
    )
    respx.get("http://localhost:9000/shiprocket/v1/external/orders").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    ShiprocketConnector._token_cache.clear()
    c = ShiprocketConnector()
    cfg = {
        "merchant": "m000",
        "base_url": "http://localhost:9000/shiprocket",
        "email": "demo@x.com",
        "password": "demo",
    }
    out = list(c.read("shipments", cfg, state=None))
    assert isinstance(out, list)


@respx.mock
def test_meta_connector_acquires_before_http():
    respx.get("http://localhost:9000/meta/v19.0/act_m000/campaigns").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    fake_bucket = MagicMock()
    fake_bucket.acquire_sync = MagicMock(return_value=None)
    c = MetaAdsConnector()
    cfg = {
        "base_url": "http://localhost:9000/meta",
        "ad_account": "m000",
        "access_token": "tok",
        "rate_limiter": fake_bucket,
    }
    list(c.read("campaigns", cfg, state=None))
    assert fake_bucket.acquire_sync.call_count >= 1


@respx.mock
def test_meta_connector_works_without_rate_limiter():
    respx.get("http://localhost:9000/meta/v19.0/act_m000/campaigns").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    c = MetaAdsConnector()
    cfg = {
        "base_url": "http://localhost:9000/meta",
        "ad_account": "m000",
        "access_token": "tok",
    }
    out = list(c.read("campaigns", cfg, state=None))
    assert isinstance(out, list)
