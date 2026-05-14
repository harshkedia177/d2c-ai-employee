from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from packages.connectors.base import (
    Checkpoint,
    CheckResult,
    Record,
    StreamSpec,
    acquire,
    is_real_mode,
)
from packages.connectors.shiprocket.schemas import SCHEMAS


class ShiprocketConnector:
    source_system = "shiprocket"
    connector_version = "shiprocket@0.1.0"

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
        key = f"{config['base_url']}::{config['email']}"
        cached = self._token_cache.get(key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        acquire(config)
        r = httpx.post(
            f"{config['base_url']}/v1/external/auth/login",
            json={"email": config["email"], "password": config["password"]},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
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
        per_page = 50
        last_shipped = (state or {}).get("shipped_date") or ""
        real = is_real_mode(config)

        while True:
            acquire(config)
            params: dict[str, Any] = {"page": page, "per_page": per_page}
            if not real:
                # mock_saas routes by merchant id; the real API has no such param.
                params["merchant"] = config["merchant"]
            r = httpx.get(
                f"{config['base_url']}/v1/external/orders",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15.0,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            if not data:
                break

            now_ts = datetime.now(UTC)
            page_max = last_shipped
            yielded_any = False
            for s in data:
                shipped = s.get("shipped_date") or ""
                if shipped and shipped <= last_shipped:
                    continue
                yield Record(
                    stream="shipments",
                    primary_key=str(s["shipment_id"]),
                    payload=s,
                    source_record_url=(f"https://app.shiprocket.in/orders/{s['shipment_id']}"),
                    fetched_at=now_ts,
                )
                yielded_any = True
                if shipped > page_max:
                    page_max = shipped

            if yielded_any:
                last_shipped = page_max
                yield Checkpoint(stream="shipments", cursor={"shipped_date": last_shipped})

            if len(data) < per_page:
                break
            page += 1
