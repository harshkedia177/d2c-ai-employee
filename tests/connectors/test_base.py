from datetime import UTC, datetime

import pytest

from packages.connectors.base import Checkpoint, CheckResult, ProvenanceError, Record, StreamSpec


def test_record_requires_source_record_url():
    with pytest.raises(ProvenanceError):
        Record(
            stream="orders",
            primary_key="123",
            payload={"x": 1},
            source_record_url=None,
            fetched_at=datetime.now(UTC),
        )


def test_record_rejects_empty_string_source_url():
    with pytest.raises(ProvenanceError):
        Record(
            stream="orders",
            primary_key="123",
            payload={"x": 1},
            source_record_url="",
            fetched_at=datetime.now(UTC),
        )


def test_record_requires_fetched_at():
    with pytest.raises(ProvenanceError):
        Record(
            stream="orders",
            primary_key="123",
            payload={"x": 1},
            source_record_url="https://shop.example.com/admin/orders/123",
            fetched_at=None,  # type: ignore[arg-type]
        )


def test_record_accepts_valid_provenance_and_computes_payload_hash():
    r = Record(
        stream="orders",
        primary_key="123",
        payload={"x": 1, "y": "hello"},
        source_record_url="https://shop.example.com/admin/orders/123",
        fetched_at=datetime.now(UTC),
    )
    assert r.payload_hash
    assert len(r.payload_hash) == 64  # sha256 hex
    # deterministic
    r2 = Record(
        stream="orders",
        primary_key="123",
        payload={"y": "hello", "x": 1},  # different key order, same content
        source_record_url="https://shop.example.com/admin/orders/123",
        fetched_at=r.fetched_at,
    )
    assert r.payload_hash == r2.payload_hash


def test_checkpoint_carries_cursor():
    c = Checkpoint(stream="orders", cursor={"updated_at_min": "2026-05-01T00:00:00Z"})
    assert c.cursor["updated_at_min"]


def test_stream_spec_holds_metadata():
    s = StreamSpec(
        name="orders", primary_key="id", cursor_field="updated_at", json_schema={"type": "object"}
    )
    assert s.name == "orders"
    assert s.cursor_field == "updated_at"


def test_check_result_is_simple_pair():
    ok = CheckResult(ok=True, message="reachable")
    bad = CheckResult(ok=False, message="401 unauthorized")
    assert ok.ok and not bad.ok
