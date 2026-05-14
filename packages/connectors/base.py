from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


class ProvenanceError(ValueError):
    """Raised when a Record is missing required provenance fields."""


@dataclass(frozen=True)
class Record:
    stream: str
    primary_key: str
    payload: dict[str, Any]
    source_record_url: str | None
    fetched_at: datetime
    payload_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.source_record_url:
            raise ProvenanceError(
                f"Record({self.stream}/{self.primary_key}) missing source_record_url"
            )
        if not self.fetched_at:
            raise ProvenanceError(f"Record({self.stream}/{self.primary_key}) missing fetched_at")
        h = hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        object.__setattr__(self, "payload_hash", h)


@dataclass(frozen=True)
class Checkpoint:
    stream: str
    cursor: dict[str, Any]


@dataclass(frozen=True)
class StreamSpec:
    name: str
    primary_key: str
    cursor_field: str | None
    json_schema: dict[str, Any]


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str = ""


def acquire(config: dict[str, Any]) -> None:
    rl = config.get("rate_limiter")
    if rl is not None:
        rl.acquire_sync()


def is_real_mode(config: dict[str, Any]) -> bool:
    return config.get("mode") == "real"


@runtime_checkable
class Connector(Protocol):
    source_system: str
    connector_version: str

    def check(self, config: dict[str, Any]) -> CheckResult: ...

    def streams(self, config: dict[str, Any]) -> list[StreamSpec]: ...

    def read(
        self,
        stream: str,
        config: dict[str, Any],
        state: dict[str, Any] | None,
    ) -> Iterator[Record | Checkpoint]: ...
