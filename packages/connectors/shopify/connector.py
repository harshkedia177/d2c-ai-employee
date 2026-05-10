from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from packages.connectors.base import (
    Checkpoint,
    CheckResult,
    Record,
    StreamSpec,
)
from packages.connectors.shopify.schemas import SCHEMAS

if TYPE_CHECKING:
    from collections.abc import Iterator


class ShopifyConnector:
    source_system = "shopify"
    connector_version = "shopify@0.1.0"

    def check(self, config: dict[str, Any]) -> CheckResult:
        try:
            r = httpx.get(
                f"{config['base_url']}/{config['merchant']}/admin/api/2026-01/orders.json",
                params={"limit": 1},
                timeout=5.0,
            )
            return CheckResult(ok=r.status_code == 200, message=str(r.status_code))
        except Exception as e:  # noqa: BLE001
            return CheckResult(ok=False, message=str(e))

    def streams(self, config: dict[str, Any]) -> list[StreamSpec]:
        return [
            StreamSpec(
                name="orders",
                primary_key="id",
                cursor_field="updated_at",
                json_schema=SCHEMAS["orders"],
            ),
            StreamSpec(
                name="line_items",
                primary_key="id",
                cursor_field=None,
                json_schema=SCHEMAS["line_items"],
            ),
            StreamSpec(
                name="products",
                primary_key="id",
                cursor_field="updated_at",
                json_schema=SCHEMAS["products"],
            ),
            StreamSpec(
                name="customers",
                primary_key="id",
                cursor_field="updated_at",
                json_schema=SCHEMAS["customers"],
            ),
            StreamSpec(
                name="refunds",
                primary_key="id",
                cursor_field="created_at",
                json_schema=SCHEMAS["refunds"],
            ),
            StreamSpec(
                name="fulfillments",
                primary_key="id",
                cursor_field="updated_at",
                json_schema=SCHEMAS["fulfillments"],
            ),
        ]

    def read(
        self,
        stream: str,
        config: dict[str, Any],
        state: dict[str, Any] | None,
    ) -> Iterator[Record | Checkpoint]:
        if stream == "orders":
            yield from self._read_orders(config, state or {})
        elif stream == "line_items":
            # line_items are emitted while reading orders; standalone read is a no-op.
            return
        else:
            # other streams not yet implemented for v0
            return

    def _read_orders(
        self, config: dict[str, Any], state: dict[str, Any]
    ) -> Iterator[Record | Checkpoint]:
        cursor = state.get("updated_at_min")
        url = f"{config['base_url']}/{config['merchant']}/admin/api/2026-01/orders.json"
        params: dict[str, Any] = {"limit": 250}
        if cursor:
            params["updated_at_min"] = cursor
        max_seen = cursor

        while True:
            r = httpx.get(url, params=params, timeout=10.0)
            r.raise_for_status()
            orders = r.json().get("orders", [])
            if not orders:
                break

            for o in orders:
                yield Record(
                    stream="orders",
                    primary_key=str(o["id"]),
                    payload=o,
                    source_record_url=(f"https://{config['shop_domain']}/admin/orders/{o['id']}"),
                    fetched_at=datetime.now(UTC),
                )
                # emit each line item as its own Record on the line_items stream
                for li in o.get("line_items", []):
                    yield Record(
                        stream="line_items",
                        primary_key=f"{o['id']}:{li['id']}",
                        payload={**li, "_order_id": o["id"]},
                        source_record_url=(
                            f"https://{config['shop_domain']}/admin/orders/{o['id']}"
                        ),
                        fetched_at=datetime.now(UTC),
                    )
                if max_seen is None or o["updated_at"] > max_seen:
                    max_seen = o["updated_at"]

            yield Checkpoint(stream="orders", cursor={"updated_at_min": max_seen})

            if len(orders) < 250:
                break
            params["updated_at_min"] = max_seen
