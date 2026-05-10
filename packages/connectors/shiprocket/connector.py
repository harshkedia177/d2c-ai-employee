from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from packages.connectors.base import (
    Checkpoint,
    CheckResult,
    Record,
    StreamSpec,
)
from packages.connectors.shiprocket.schemas import SCHEMAS

if TYPE_CHECKING:
    from collections.abc import Iterator


class ShiprocketConnector:
    source_system = "shiprocket"
    connector_version = "shiprocket@0.1.0"

    # class-level token cache: {merchant_id: (token, expires_at_unix)}
    _token_cache: ClassVar[dict[str, tuple[str, float]]] = {}

    def check(self, config: dict[str, Any]) -> CheckResult:
        try:
            self._token(config)
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, message=str(e))

    def streams(self, config: dict[str, Any]) -> list[StreamSpec]:
        return [
            StreamSpec(
                name="shipments",
                primary_key="shipment_id",
                cursor_field="shipped_date",
                json_schema=SCHEMAS["shipments"],
            )
        ]

    def _token(self, config: dict[str, Any]) -> str:
        key = config["merchant"]
        cached = self._token_cache.get(key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        r = httpx.post(
            f"{config['base_url']}/v1/external/auth/login",
            json={"email": config["email"], "password": config["password"]},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        # API returns expires_in seconds (240h = 864000). Be defensive if missing.
        ttl = float(data.get("expires_in", 240 * 3600))
        self._token_cache[key] = (data["token"], time.time() + ttl)
        return data["token"]

    def read(
        self,
        stream: str,
        config: dict[str, Any],
        state: dict[str, Any] | None,
    ) -> Iterator[Record | Checkpoint]:
        if stream != "shipments":
            return

        token = self._token(config)
        page = 1
        last_shipped = (state or {}).get("shipped_date") or ""

        while True:
            r = httpx.get(
                f"{config['base_url']}/v1/external/orders",
                params={
                    "merchant": config["merchant"],
                    "page": page,
                    "per_page": 50,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=15.0,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            if not data:
                break

            page_max = last_shipped
            yielded_any = False
            for s in data:
                if s["shipped_date"] <= last_shipped:
                    continue
                yield Record(
                    stream="shipments",
                    primary_key=str(s["shipment_id"]),
                    payload=s,
                    source_record_url=(f"https://app.shiprocket.in/orders/{s['shipment_id']}"),
                    fetched_at=datetime.now(UTC),
                )
                yielded_any = True
                if s["shipped_date"] > page_max:
                    page_max = s["shipped_date"]

            if yielded_any:
                last_shipped = page_max
                yield Checkpoint(stream="shipments", cursor={"shipped_date": last_shipped})

            if len(data) < 50:
                break
            page += 1
