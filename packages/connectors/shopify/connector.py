from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from packages.connectors.base import (
    Checkpoint,
    CheckResult,
    Record,
    StreamSpec,
)
from packages.connectors.shopify.schemas import SCHEMAS


class ShopifyConnector:
    source_system = "shopify"
    connector_version = "shopify@0.1.0"

    def check(self, config: dict[str, Any]) -> CheckResult:
        try:
            rl = config.get("rate_limiter")
            if rl is not None:
                rl.acquire_sync()
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
            return
        else:
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

        seen_customer_ids: set[str] = set()
        seen_skus: set[str] = set()

        while True:
            rl = config.get("rate_limiter")
            if rl is not None:
                rl.acquire_sync()
            r = httpx.get(url, params=params, timeout=10.0)
            r.raise_for_status()
            orders = r.json().get("orders", [])
            if not orders:
                break

            for o in orders:
                now_ts = datetime.now(UTC)
                shop_domain = config["shop_domain"]

                yield Record(
                    stream="orders",
                    primary_key=str(o["id"]),
                    payload=o,
                    source_record_url=f"https://{shop_domain}/admin/orders/{o['id']}",
                    fetched_at=now_ts,
                )

                cust = o.get("customer") or {}
                cust_id = cust.get("id")
                if cust_id is not None and str(cust_id) not in seen_customer_ids:
                    seen_customer_ids.add(str(cust_id))
                    yield Record(
                        stream="customers",
                        primary_key=str(cust_id),
                        payload=cust,
                        source_record_url=(f"https://{shop_domain}/admin/customers/{cust_id}"),
                        fetched_at=now_ts,
                    )

                for li in o.get("line_items", []):
                    yield Record(
                        stream="line_items",
                        primary_key=f"{o['id']}:{li['id']}",
                        payload={**li, "_order_id": o["id"]},
                        source_record_url=f"https://{shop_domain}/admin/orders/{o['id']}",
                        fetched_at=now_ts,
                    )
                    sku = li.get("sku")
                    if sku and sku not in seen_skus:
                        seen_skus.add(sku)
                        yield Record(
                            stream="products",
                            primary_key=sku,
                            payload={
                                "sku": sku,
                                "title": li.get("title"),
                                "price": li.get("price"),
                                "currency": o.get("currency", "INR"),
                            },
                            source_record_url=(f"https://{shop_domain}/admin/products?sku={sku}"),
                            fetched_at=now_ts,
                        )

                for ref in o.get("refunds", []) or []:
                    yield Record(
                        stream="refunds",
                        primary_key=str(ref["id"]),
                        payload={**ref, "_order_id": o["id"]},
                        source_record_url=(
                            f"https://{shop_domain}/admin/orders/{o['id']}#refund-{ref['id']}"
                        ),
                        fetched_at=now_ts,
                    )

                if max_seen is None or o["updated_at"] > max_seen:
                    max_seen = o["updated_at"]

            yield Checkpoint(stream="orders", cursor={"updated_at_min": max_seen})

            if len(orders) < 250:
                break
            params["updated_at_min"] = max_seen
