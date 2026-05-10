from datetime import UTC, datetime

from packages.connectors.base import Record
from packages.udm.normalize.meta_to_udm import (
    ad_spend_daily_from_meta,
    campaign_from_meta,
)
from packages.udm.xref import canonical_id


def test_campaign_normalizes_with_provenance():
    rec = Record(
        stream="campaigns",
        primary_key="camp-3",
        payload={"id": "camp-3", "name": "Sale", "status": "ACTIVE", "objective": "OUTCOME_SALES"},
        source_record_url=(
            "https://business.facebook.com/adsmanager/manage/campaigns?act=m000"
            "&selected_campaign_ids=camp-3"
        ),
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    row = campaign_from_meta(rec, tenant_id="t1", raw_row_id=1)
    assert row["platform"] == "meta"
    assert row["name"] == "Sale"
    assert row["status"] == "ACTIVE"
    assert row["canonical_id"] == canonical_id("t1", "campaign", "meta_ads", "camp-3")
    assert row["raw_table"] == "raw.meta_campaigns"
    assert row["source_system"] == "meta_ads"


def test_ad_spend_canonical_id_links_to_same_campaign_canonical_id():
    """ad_spend_daily.campaign_canonical_id MUST match campaign.canonical_id
    so the join works in chat queries like 'spend by campaign'."""
    rec = Record(
        stream="ad_insights",
        primary_key="ad-1:2026-05-01",
        payload={
            "date_start": "2026-05-01",
            "campaign_id": "camp-3",
            "campaign_name": "Sale",
            "ad_id": "ad-1",
            "ad_set_id": "as-1",
            "spend": "1234.50",
            "impressions": 1000,
            "clicks": 30,
            "conversions": 2,
        },
        source_record_url="https://business.facebook.com/adsmanager/manage/ads?act=m000",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    row = ad_spend_daily_from_meta(rec, tenant_id="t1", raw_row_id=10)
    assert row["spend"] == 1234.50
    assert row["ad_id"] == "ad-1"
    assert row["impressions"] == 1000
    assert row["campaign_canonical_id"] == canonical_id("t1", "campaign", "meta_ads", "camp-3")
    assert row["currency"] == "INR"
