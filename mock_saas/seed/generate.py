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

# Seed-data window: span the 90 days ending "now" so the scheduler's recent-
# window queries (typically the last 14d) always land on data. Computed once
# at module load so all gen_* helpers share the same anchor.
N_HISTORY_DAYS = 90
_NOW = datetime.now(UTC).replace(microsecond=0)
_SEED_END = _NOW.date()
_SEED_BASE = _NOW - timedelta(days=N_HISTORY_DAYS - 1)


def gen_orders(merchant_id: str, n: int = 1000) -> list[dict]:
    out: list[dict] = []
    base = _SEED_BASE
    for i in range(n):
        placed_at = base + timedelta(
            days=random.randint(0, N_HISTORY_DAYS - 1),
            hours=random.randint(0, 23),
        )
        is_cod = random.random() < 0.65
        pincode = random.choice(PINCODES_HIGH_RTO + PINCODES_LOW_RTO)
        cart_value = round(random.uniform(499, 4999), 2)
        # IMPORTANT: utm_campaign must match Meta's campaign `name` exactly —
        # the scheduler joins core.order.utm_campaign = core.campaign.name to
        # compute attributed_revenue per campaign.
        # Bias attribution: campaigns 1–3 get fewer orders than 4–10 (so the
        # "overspend" campaigns below land in the danger zone for Meta Pauser
        # to actually propose pauses, not just keep). Weighted choice.
        utm = f"Campaign {random.choices(range(1, 11), weights=[1,1,1,3,3,3,3,3,3,3])[0]}"
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
                # Real Shopify returns both: `gateway` (legacy single value) and
                # `payment_gateway_names` (modern array of all txn gateways).
                # https://shopify.dev/docs/api/admin-rest/latest/resources/order
                "gateway": "Cash on Delivery" if is_cod else "razorpay",
                "payment_gateway_names": (
                    ["Cash on Delivery"] if is_cod else ["razorpay"]
                ),
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
    # Sort by updated_at so cursor-paginating connectors don't skip rows.
    out.sort(key=lambda o: o["updated_at"])
    return out


def gen_shipments(orders: list[dict]) -> list[dict]:
    out: list[dict] = []
    for o in orders:
        is_cod = o["gateway"] == "Cash on Delivery"
        zip_ = o["shipping_address"]["zip"]
        rto_prob = 0.33 if (is_cod and zip_ in PINCODES_HIGH_RTO) else 0.05
        is_rto = random.random() < rto_prob
        shipped = datetime.fromisoformat(o["created_at"])
        awb = f"AWB{random.randint(10**11, 10**12)}"
        out.append(
            {
                "shipment_id": f"sr-{o['id']}",
                "order_id": o["id"],
                # Real Shiprocket /v1/external/orders uses `awb`; older clients
                # see `awb_code`. Emit both for compatibility.
                "awb": awb,
                "awb_code": awb,
                "courier_name": random.choice(
                    ["Delhivery", "Ecom Express", "Bluedart", "Xpressbees"]
                ),
                # Real API encodes RTO in the status string ("RTO Delivered",
                # "RTO Initiated"); the boolean is a mock convenience. The
                # normalizer derives is_rto from the status if the boolean is absent.
                "current_status": "RTO Delivered" if is_rto else "Delivered",
                "is_rto": is_rto,
                "freight_charges": round(random.uniform(45, 95), 2),
                "shipped_date": shipped.isoformat(),
                "delivered_date": (shipped + timedelta(days=random.randint(2, 6))).isoformat(),
            }
        )
    # Sort by shipped_date so the Shiprocket date cursor walks the whole list.
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
    base = _SEED_BASE.date()
    # "Overspender" campaigns get daily budgets ~3–4× the others. Combined with
    # the lower attribution weight for campaigns 1–3 in gen_orders, this
    # produces post-RTO ROAS in the pause/reduce zone for these campaigns —
    # gives Meta Pauser a meaningful decision instead of a trivial "Keep all".
    OVERSPEND_IDS = {"camp-1", "camp-2", "camp-3"}
    for c in campaigns:
        is_overspend = c["id"] in OVERSPEND_IDS
        for d in range(N_HISTORY_DAYS):
            day = base + timedelta(days=d)
            spend = (
                round(random.uniform(8000, 18000), 2)
                if is_overspend
                else round(random.uniform(500, 5000), 2)
            )
            # Daily conversions: kept comfortably above the agent's 50/window
            # learning-phase threshold for a 14-day window
            # (LEARNING_PHASE_MIN_CONVERSIONS in packages/agents/meta_pauser.py).
            # 14 × avg(8..30)=14×19=~266 conversions ≫ 50.
            # Real Graph API returns numeric metrics as strings, not ints.
            # https://developers.facebook.com/docs/marketing-api/insights/
            insights.append(
                {
                    "date_start": day.isoformat(),
                    "campaign_id": c["id"],
                    "campaign_name": c["name"],
                    "ad_id": f"ad-{c['id']}-1",
                    "ad_set_id": f"adset-{c['id']}-1",
                    "spend": str(spend),
                    "impressions": str(random.randint(2000, 50000)),
                    "clicks": str(random.randint(50, 800)),
                    "conversions": str(random.randint(8, 30)),
                    "purchase_roas": [
                        {
                            "action_type": "purchase",
                            "value": str(round(random.uniform(0.4, 4.5), 2)),
                        }
                    ],
                }
            )
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
