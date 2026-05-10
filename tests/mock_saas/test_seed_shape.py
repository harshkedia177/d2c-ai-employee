import json
import subprocess
from pathlib import Path


def test_seed_generates_orders_with_rto_pattern(tmp_path: Path):
    out = tmp_path / "seed"
    subprocess.check_call(
        [
            "uv",
            "run",
            "python",
            "-m",
            "mock_saas.seed.generate",
            "--merchants=1",
            "--orders-per-merchant=200",
            f"--out={out}",
        ]
    )
    orders = json.loads((out / "m000_shopify_orders.json").read_text())
    shipments = json.loads((out / "m000_shiprocket_shipments.json").read_text())
    assert len(orders) == 200
    assert len(shipments) == 200
    rto_rate = sum(1 for s in shipments if s["is_rto"]) / len(shipments)
    assert 0.05 < rto_rate < 0.50  # realistic RTO range


def test_seed_high_rto_pincodes_have_higher_rto_than_low_rto():
    """The seed encodes a real signal: COD orders to PINCODES_HIGH_RTO have ~33% RTO,
    others ~5%. Without this signal, the RTO agent has nothing to detect."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        subprocess.check_call(
            [
                "uv",
                "run",
                "python",
                "-m",
                "mock_saas.seed.generate",
                "--merchants=1",
                "--orders-per-merchant=2000",
                f"--out={d}",
            ]
        )
        orders = json.loads(Path(d, "m000_shopify_orders.json").read_text())
        shipments = json.loads(Path(d, "m000_shiprocket_shipments.json").read_text())

    from mock_saas.seed.generate import PINCODES_HIGH_RTO

    by_zip = {o["id"]: o["shipping_address"]["zip"] for o in orders}
    by_gw = {o["id"]: o["gateway"] for o in orders}

    high_cod = [
        s
        for s in shipments
        if by_gw[s["order_id"]] == "Cash on Delivery" and by_zip[s["order_id"]] in PINCODES_HIGH_RTO
    ]
    low_cod = [
        s
        for s in shipments
        if by_gw[s["order_id"]] == "Cash on Delivery"
        and by_zip[s["order_id"]] not in PINCODES_HIGH_RTO
    ]

    high_rate = sum(1 for s in high_cod if s["is_rto"]) / max(len(high_cod), 1)
    low_rate = sum(1 for s in low_cod if s["is_rto"]) / max(len(low_cod), 1)
    assert high_rate > 2 * low_rate, f"Signal too weak: high={high_rate:.2f} low={low_rate:.2f}"


def test_meta_insights_cover_90_days_for_each_campaign(tmp_path: Path):
    subprocess.check_call(
        [
            "uv",
            "run",
            "python",
            "-m",
            "mock_saas.seed.generate",
            "--merchants=1",
            "--orders-per-merchant=100",
            f"--out={tmp_path}",
        ]
    )
    campaigns = json.loads((tmp_path / "m000_meta_campaigns.json").read_text())
    insights = json.loads((tmp_path / "m000_meta_insights.json").read_text())
    assert len(campaigns) == 10
    by_camp: dict[str, int] = {}
    for r in insights:
        by_camp[r["campaign_id"]] = by_camp.get(r["campaign_id"], 0) + 1
    for c in campaigns:
        assert by_camp[c["id"]] == 90
