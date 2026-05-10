"""Cross-reference: source-system primary key → canonical UUID.

For v0, canonical_id is a deterministic UUIDv5 derived from
(tenant_id, entity, source_system, source_id). This avoids a DB round-trip
per record and makes joins reproducible across re-ingests.

Tradeoff: changing the namespace below breaks all canonical_id continuity.
Treat it as a versioned constant.
"""

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
    """Stable UUIDv5 — same inputs → same output, forever."""
    name = f"{tenant_id}|{entity}|{source_system}|{source_id}"
    return str(uuid.uuid5(NAMESPACE, name))
