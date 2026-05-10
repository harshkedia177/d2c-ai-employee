import httpx
import respx

from packages.connectors.meta_ads.connector import MetaAdsConnector


def test_streams_returns_campaigns_and_ad_insights():
    c = MetaAdsConnector()
    by_name = {s.name: s for s in c.streams({})}
    assert set(by_name) == {"campaigns", "ad_insights"}
    assert by_name["ad_insights"].cursor_field == "date_start"


@respx.mock
def test_read_campaigns_yields_records_with_provenance():
    respx.get("http://localhost:9000/meta/v19.0/act_m000/campaigns").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "camp-1",
                        "name": "Sale",
                        "status": "ACTIVE",
                        "objective": "OUTCOME_SALES",
                    }
                ]
            },
        )
    )
    c = MetaAdsConnector()
    cfg = {
        "ad_account": "m000",
        "base_url": "http://localhost:9000/meta",
        "access_token": "tok",
    }
    out = list(c.read("campaigns", cfg, None))
    records = [r for r in out if hasattr(r, "primary_key")]
    assert len(records) == 1
    assert records[0].primary_key == "camp-1"
    assert "business.facebook.com" in records[0].source_record_url
    assert "selected_campaign_ids=camp-1" in records[0].source_record_url


@respx.mock
def test_read_insights_emits_per_day_per_ad_records():
    respx.get("http://localhost:9000/meta/v19.0/act_m000/insights").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "date_start": "2026-05-01",
                        "campaign_id": "c1",
                        "campaign_name": "C1",
                        "ad_id": "ad1",
                        "ad_set_id": "as1",
                        "spend": "1234.5",
                        "impressions": 1000,
                        "clicks": 30,
                        "conversions": 2,
                        "purchase_roas": [{"action_type": "purchase", "value": "2.1"}],
                    }
                ],
                "paging": {},
            },
        )
    )
    c = MetaAdsConnector()
    cfg = {
        "ad_account": "m000",
        "base_url": "http://localhost:9000/meta",
        "access_token": "tok",
    }
    out = list(c.read("ad_insights", cfg, None))
    records = [r for r in out if hasattr(r, "primary_key")]
    assert len(records) == 1
    assert records[0].primary_key == "ad1:2026-05-01"
    assert records[0].payload["spend"] == "1234.5"


@respx.mock
def test_insights_cursor_skips_old_dates():
    respx.get("http://localhost:9000/meta/v19.0/act_m000/insights").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "date_start": "2026-04-01",
                        "campaign_id": "c1",
                        "campaign_name": "C1",
                        "ad_id": "ad1",
                        "ad_set_id": "as1",
                        "spend": "100",
                        "impressions": 100,
                        "clicks": 5,
                        "conversions": 0,
                        "purchase_roas": [],
                    },
                    {
                        "date_start": "2026-06-01",
                        "campaign_id": "c1",
                        "campaign_name": "C1",
                        "ad_id": "ad1",
                        "ad_set_id": "as1",
                        "spend": "200",
                        "impressions": 200,
                        "clicks": 10,
                        "conversions": 1,
                        "purchase_roas": [],
                    },
                ],
                "paging": {},
            },
        )
    )
    c = MetaAdsConnector()
    cfg = {
        "ad_account": "m000",
        "base_url": "http://localhost:9000/meta",
        "access_token": "tok",
    }
    out = list(c.read("ad_insights", cfg, state={"date_start": "2026-05-01"}))
    records = [r for r in out if hasattr(r, "primary_key")]
    assert len(records) == 1
    assert records[0].primary_key == "ad1:2026-06-01"


def test_read_unknown_stream_is_empty():
    c = MetaAdsConnector()
    cfg = {
        "ad_account": "m000",
        "base_url": "http://localhost:9000/meta",
        "access_token": "tok",
    }
    assert list(c.read("orders", cfg, None)) == []
