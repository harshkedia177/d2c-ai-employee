import httpx
import respx

from packages.connectors.shiprocket.connector import ShiprocketConnector


def test_streams_has_shipments():
    c = ShiprocketConnector()
    streams = c.streams({})
    assert len(streams) == 1
    assert streams[0].name == "shipments"
    assert streams[0].cursor_field == "shipped_date"
    assert streams[0].primary_key == "shipment_id"


@respx.mock
def test_login_then_read_shipments():
    respx.post("http://localhost:9000/shiprocket/v1/external/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expires_in": 864000})
    )
    respx.get("http://localhost:9000/shiprocket/v1/external/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "shipment_id": "sr-1",
                        "order_id": "shop-1",
                        "awb_code": "AWB123",
                        "courier_name": "Delhivery",
                        "current_status": "RTO Delivered",
                        "is_rto": True,
                        "freight_charges": 60.0,
                        "shipped_date": "2026-05-01T00:00:00+00:00",
                        "delivered_date": "2026-05-04T00:00:00+00:00",
                    }
                ],
                "meta": {"total": 1},
            },
        )
    )
    # ensure clean cache
    ShiprocketConnector._token_cache.clear()

    c = ShiprocketConnector()
    cfg = {
        "merchant": "m000-loginread",
        "base_url": "http://localhost:9000/shiprocket",
        "email": "e",
        "password": "p",
    }
    out = list(c.read("shipments", cfg, state=None))
    records = [r for r in out if hasattr(r, "primary_key")]
    checkpoints = [r for r in out if not hasattr(r, "primary_key")]
    assert len(records) == 1
    assert records[0].payload["is_rto"] is True
    assert "shiprocket" in records[0].source_record_url
    assert len(checkpoints) == 1
    assert checkpoints[0].cursor["shipped_date"] == "2026-05-01T00:00:00+00:00"


@respx.mock
def test_token_cache_avoids_repeat_login():
    login_route = respx.post("http://localhost:9000/shiprocket/v1/external/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expires_in": 864000})
    )
    respx.get("http://localhost:9000/shiprocket/v1/external/orders").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"total": 0}})
    )
    ShiprocketConnector._token_cache.clear()
    c = ShiprocketConnector()
    cfg = {
        "merchant": "m000-cache",
        "base_url": "http://localhost:9000/shiprocket",
        "email": "e",
        "password": "p",
    }
    list(c.read("shipments", cfg, None))
    list(c.read("shipments", cfg, None))
    list(c.read("shipments", cfg, None))
    assert login_route.call_count == 1


@respx.mock
def test_cursor_filter_skips_already_seen():
    respx.post("http://localhost:9000/shiprocket/v1/external/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 864000})
    )
    respx.get("http://localhost:9000/shiprocket/v1/external/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "shipment_id": "sr-old",
                        "order_id": "o1",
                        "current_status": "Delivered",
                        "is_rto": False,
                        "shipped_date": "2026-01-01T00:00:00+00:00",
                    },
                    {
                        "shipment_id": "sr-new",
                        "order_id": "o2",
                        "current_status": "Delivered",
                        "is_rto": False,
                        "shipped_date": "2026-06-01T00:00:00+00:00",
                    },
                ],
                "meta": {"total": 2},
            },
        )
    )
    ShiprocketConnector._token_cache.clear()
    c = ShiprocketConnector()
    cfg = {
        "merchant": "m000-cursor",
        "base_url": "http://localhost:9000/shiprocket",
        "email": "e",
        "password": "p",
    }
    out = list(
        c.read(
            "shipments",
            cfg,
            state={"shipped_date": "2026-03-01T00:00:00+00:00"},
        )
    )
    records = [r for r in out if hasattr(r, "primary_key")]
    assert len(records) == 1
    assert records[0].primary_key == "sr-new"


@respx.mock
def test_unauthorized_token_propagates_error():
    respx.post("http://localhost:9000/shiprocket/v1/external/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "bad", "expires_in": 864000})
    )
    respx.get("http://localhost:9000/shiprocket/v1/external/orders").mock(
        return_value=httpx.Response(401, json={"message": "bad token"})
    )
    ShiprocketConnector._token_cache.clear()
    c = ShiprocketConnector()
    cfg = {
        "merchant": "m000-401",
        "base_url": "http://localhost:9000/shiprocket",
        "email": "e",
        "password": "p",
    }
    import pytest

    with pytest.raises(httpx.HTTPStatusError):
        list(c.read("shipments", cfg, None))


def test_read_unknown_stream_returns_empty():
    c = ShiprocketConnector()
    cfg = {
        "merchant": "m000-unknown",
        "base_url": "http://localhost:9000/shiprocket",
        "email": "e",
        "password": "p",
    }
    assert list(c.read("orders", cfg, None)) == []
