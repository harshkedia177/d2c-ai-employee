"""Cross-reference: source-system primary key to canonical UUID."""

from __future__ import annotations

import uuid

# DO NOT change this namespace UUID after v0 ships.
# It is the seed for every canonical_id in core.*.
NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")


def canonical_id(
    tenant_id: str,
    entity: str,
    source_system: str,
    source_id: str,
) -> str:
    name = f"{tenant_id}|{entity}|{source_system}|{source_id}"
    return str(uuid.uuid5(NAMESPACE, name))
