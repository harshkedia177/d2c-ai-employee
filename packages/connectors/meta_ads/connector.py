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
from packages.connectors.meta_ads.schemas import SCHEMAS

if TYPE_CHECKING:
    from collections.abc import Iterator


class MetaAdsConnector:
    source_system = "meta_ads"
    connector_version = "meta_ads@0.1.0"

    def check(self, config: dict[str, Any]) -> CheckResult:
        try:
            r = httpx.get(
                f"{config['base_url']}/v19.0/act_{config['ad_account']}/campaigns",
                params={"access_token": config.get("access_token", "")},
                timeout=5.0,
            )
            return CheckResult(ok=r.status_code == 200, message=str(r.status_code))
        except Exception as e:  # noqa: BLE001
            return CheckResult(ok=False, message=str(e))

    def streams(self, config: dict[str, Any]) -> list[StreamSpec]:
        return [
            StreamSpec(
                name="campaigns",
                primary_key="id",
                cursor_field=None,
                json_schema=SCHEMAS["campaigns"],
            ),
            StreamSpec(
                name="ad_insights",
                primary_key="ad_id_date",
                cursor_field="date_start",
                json_schema=SCHEMAS["ad_insights"],
            ),
        ]

    def read(
        self,
        stream: str,
        config: dict[str, Any],
        state: dict[str, Any] | None,
    ) -> Iterator[Record | Checkpoint]:
        if stream == "campaigns":
            yield from self._read_campaigns(config)
        elif stream == "ad_insights":
            yield from self._read_insights(config, state or {})
        else:
            return

    def _read_campaigns(self, config: dict[str, Any]) -> Iterator[Record | Checkpoint]:
        url = f"{config['base_url']}/v19.0/act_{config['ad_account']}/campaigns"
        r = httpx.get(
            url,
            params={"access_token": config["access_token"]},
            timeout=10.0,
        )
        r.raise_for_status()
        for c in r.json().get("data", []):
            yield Record(
                stream="campaigns",
                primary_key=str(c["id"]),
                payload=c,
                source_record_url=(
                    f"https://business.facebook.com/adsmanager/manage/campaigns?"
                    f"act={config['ad_account']}&selected_campaign_ids={c['id']}"
                ),
                fetched_at=datetime.now(UTC),
            )

    def _read_insights(
        self, config: dict[str, Any], state: dict[str, Any]
    ) -> Iterator[Record | Checkpoint]:
        url = f"{config['base_url']}/v19.0/act_{config['ad_account']}/insights"
        last_date = state.get("date_start") or ""
        page = 0
        max_seen = last_date

        # paging via offset (simplified for v0; real Meta uses cursor-paged response)
        while True:
            r = httpx.get(
                url,
                params={
                    "access_token": config["access_token"],
                    "fields": (
                        "date_start,campaign_id,campaign_name,ad_id,ad_set_id,"
                        "spend,impressions,clicks,conversions,purchase_roas"
                    ),
                    "limit": 1000,
                    "after": page,
                },
                timeout=15.0,
            )
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data", [])
            if not data:
                break

            for row in data:
                if row["date_start"] <= last_date:
                    continue
                pk = f"{row['ad_id']}:{row['date_start']}"
                yield Record(
                    stream="ad_insights",
                    primary_key=pk,
                    payload=row,
                    source_record_url=(
                        f"https://business.facebook.com/adsmanager/manage/ads?"
                        f"act={config['ad_account']}&selected_ad_ids={row['ad_id']}"
                    ),
                    fetched_at=datetime.now(UTC),
                )
                if row["date_start"] > max_seen:
                    max_seen = row["date_start"]

            yield Checkpoint(stream="ad_insights", cursor={"date_start": max_seen})

            # mock_saas server doesn't actually page; break if we got under limit
            if len(data) < 1000:
                break
            page += 1
