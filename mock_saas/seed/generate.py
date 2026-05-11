from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from faker import Faker

fake = Faker("en_IN")
random.seed(42)
Faker.seed(42)

PINCODES_HIGH_RTO = ["110084", "201001", "302013", "700091", "560100"]
PINCODES_LOW_RTO = ["560001", "400001", "411001", "500032", "600028"]


def gen_orders(merchant_id: str, n: int = 1000) -> list[dict]:
    out: list[dict] = []
    base = datetime(2026, 2, 1, tzinfo=UTC)
    for i in range(n):
        placed_at = base + timedelta(days=random.randint(0, 90), hours=random.randint(0, 23))
        is_cod = random.random() < 0.65  # 65% COD share, realistic for IN D2C
        pincode = random.choice(PINCODES_HIGH_RTO + PINCODES_LOW_RTO)
        cart_value = round(random.uniform(499, 4999), 2)
        utm = f"camp-{random.randint(1, 10)}"
        # ~5% of orders get a partial refund 1–14 days after placement
        if random.random() < 0.05:
            refund_amount = round(cart_value * random.uniform(0.05, 0.30), 2)
            refunded_at = placed_at + timedelta(days=random.randint(1, 14))
            refunds = [
                {
                    "id": f"refund-{i}-1",
                    "amount": str(refund_amount),
                    "currency": "INR",
                    "reason": random.choice(
                        ["damaged", "wrong size", "customer changed mind", "quality issue"]
                    ),
                    "created_at": refunded_at.isoformat(),
                }
            ]
        else:
            refunds = []
        out.append(
            {
                "id": f"shopify-{merchant_id}-{i:06d}",
                "name": f"#{1000 + i}",
                "created_at": placed_at.isoformat(),
                "updated_at": placed_at.isoformat(),
                "financial_status": "paid" if not is_cod else "pending",
                "total_price": str(cart_value),
                "subtotal_price": str(round(cart_value * 0.92, 2)),
                "total_tax": str(round(cart_value * 0.05, 2)),
                "total_discounts": "0.00",
                "total_shipping_price_set": {"shop_money": {"amount": "49.00"}},
                "currency": "INR",
                "gateway": "Cash on Delivery" if is_cod else "razorpay",
                "shipping_address": {
                    "zip": pincode,
                    "city": fake.city(),
                    "address1": fake.street_address(),
                    "phone": fake.phone_number(),
                },
                "customer": {
                    "id": f"cust-{random.randint(1, 200)}",
                    "email": fake.email(),
                    "phone": fake.phone_number(),
                },
                "line_items": [
                    {
                        "id": f"li-{i}-{j}",
                        "sku": f"SKU-{random.randint(1, 30)}",
                        "title": fake.word().title(),
                        "quantity": random.randint(1, 3),
                        "price": str(round(cart_value / random.randint(1, 3), 2)),
                    }
                    for j in range(random.randint(1, 3))
                ],
                "note_attributes": [{"name": "utm_campaign", "value": utm}],
                "refunds": refunds,
            }
        )
    # Sort by updated_at so cursor-paginating connectors (updated_at_min) see
    # the full volume; otherwise each page's max-cursor advances past
    # later rows still on disk and the connector skips them.
    out.sort(key=lambda o: o["updated_at"])
    return out


def gen_shipments(orders: list[dict]) -> list[dict]:
    out: list[dict] = []
    for o in orders:
        is_cod = o["gateway"] == "Cash on Delivery"
        zip_ = o["shipping_address"]["zip"]
        # high RTO when COD + bad pincode; low RTO otherwise
        rto_prob = 0.33 if (is_cod and zip_ in PINCODES_HIGH_RTO) else 0.05
        is_rto = random.random() < rto_prob
        shipped = datetime.fromisoformat(o["created_at"])
        out.append(
            {
                "shipment_id": f"sr-{o['id']}",
                "order_id": o["id"],
                "awb_code": f"AWB{random.randint(10**11, 10**12)}",
                "courier_name": random.choice(
                    ["Delhivery", "Ecom Express", "Bluedart", "Xpressbees"]
                ),
                "current_status": "RTO Delivered" if is_rto else "Delivered",
                "is_rto": is_rto,
                "freight_charges": round(random.uniform(45, 95), 2),
                "shipped_date": shipped.isoformat(),
                "delivered_date": (shipped + timedelta(days=random.randint(2, 6))).isoformat(),
            }
        )
    # Sort by shipped_date so the Shiprocket connector's date cursor walks
    # the whole list rather than skipping ahead.
    out.sort(key=lambda s: s["shipped_date"])
    return out


def gen_meta(merchant_id: str, n_campaigns: int = 10) -> tuple[list[dict], list[dict]]:
    campaigns = [
        {
            "id": f"camp-{i}",
            "name": f"Campaign {i}",
            "status": "ACTIVE",
            "objective": "OUTCOME_SALES",
        }
        for i in range(1, n_campaigns + 1)
    ]
    insights: list[dict] = []
    base = datetime(2026, 2, 1).date()
    for c in campaigns:
        for d in range(90):
            day = base + timedelta(days=d)
            spend = round(random.uniform(500, 5000), 2)
            insights.append(
                {
                    "date_start": day.isoformat(),
                    "campaign_id": c["id"],
                    "campaign_name": c["name"],
                    "ad_id": f"ad-{c['id']}-1",
                    "ad_set_id": f"adset-{c['id']}-1",
                    "spend": str(spend),
                    "impressions": random.randint(2000, 50000),
                    "clicks": random.randint(50, 800),
                    "conversions": random.randint(0, 30),
                    "purchase_roas": [
                        {
                            "action_type": "purchase",
                            "value": str(round(random.uniform(0.4, 4.5), 2)),
                        }
                    ],
                }
            )
    # Sort by (date_start, campaign_id) so the Meta connector's date_start
    # cursor sees the whole volume.
    insights.sort(key=lambda r: (r["date_start"], r["campaign_id"]))
    return campaigns, insights


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merchants", type=int, default=1)
    ap.add_argument("--orders-per-merchant", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=Path("mock_saas/seed/data"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for m in range(args.merchants):
        merchant = f"m{m:03d}"
        orders = gen_orders(merchant, args.orders_per_merchant)
        shipments = gen_shipments(orders)
        campaigns, insights = gen_meta(merchant)
        (args.out / f"{merchant}_shopify_orders.json").write_text(json.dumps(orders))
        (args.out / f"{merchant}_shiprocket_shipments.json").write_text(json.dumps(shipments))
        (args.out / f"{merchant}_meta_campaigns.json").write_text(json.dumps(campaigns))
        (args.out / f"{merchant}_meta_insights.json").write_text(json.dumps(insights))


if __name__ == "__main__":
    main()
