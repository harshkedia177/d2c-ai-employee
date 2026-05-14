"""Helpers shared by all normalizers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from packages.connectors.base import Record


def provenance_columns(
    record: Record,
    raw_table: str,
    raw_row_id: int,
    connector_version: str,
    source_system: str,
) -> dict[str, Any]:
    if not record.source_record_url:
        raise ValueError(
            f"Cannot normalize {record.stream}/{record.primary_key}: "
            "missing source_record_url (provenance contract violation)"
        )
    return {
        "source_system": source_system,
        "source_id": record.primary_key,
        "source_record_url": record.source_record_url,
        "raw_table": raw_table,
        "raw_row_id": raw_row_id,
        "raw_payload_hash": record.payload_hash,
        "fetched_at": record.fetched_at,
        "ingested_at": datetime.now(UTC),
        "connector_version": connector_version,
    }
