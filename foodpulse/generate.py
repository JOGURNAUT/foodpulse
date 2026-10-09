"""Synthetic food-delivery data, with the defects a warehouse has to survive.

Six tables in the shape a delivery marketplace actually keeps them: restaurants
and users as dimensions, a menu, orders, the items inside those orders, and
free-text reviews.

Generated rather than downloaded, and seeded, so the whole project runs from a
clone with no credentials and no 200 MB file in git. Same seed, same rows.

The defects are the point. A clean dataset cannot exercise a quality check, so
each of these is injected at a tunable rate and each one is something a real
marketplace export has:

  --orphan-rate       an order whose restaurant_id is not in restaurants, which
                      is what a mid-export deletion looks like downstream
  --dup-rate          the same order exported twice, because an export that
                      paginates and retries overlaps its pages
  --null-rating-rate  a review row with no rating -- the user wrote text and
                      skipped the stars, which is not missing data
  --late-delivery-rate a delivery whose timestamp precedes its order, from a
                      clock skew between two services
  --negative-rate     a negative order total, from a refund written as an order
  --currency-rate     an amount in paise where the column is rupees, which is
                      the quietest of these: the row is valid, the number is
                      a hundred times too large
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import random
import sys

CITIES = ["Bengaluru", "Pune", "Hyderabad", "Jaipur", "Kochi"]
CUISINES = ["North Indian", "South Indian", "Chinese", "Biryani", "Pizza",
            "Desserts", "Rolls", "Healthy"]
DISHES = {
    "North Indian": ["Paneer Butter Masala", "Dal Makhani", "Butter Naan"],
    "South Indian": ["Masala Dosa", "Idli Sambar", "Filter Coffee"],
    "Chinese": ["Hakka Noodles", "Chilli Paneer", "Manchow Soup"],
    "Biryani": ["Chicken Biryani", "Veg Biryani", "Mutton Biryani"],
    "Pizza": ["Margherita", "Farmhouse", "Garlic Bread"],
    "Desserts": ["Gulab Jamun", "Brownie", "Rasmalai"],
    "Rolls": ["Paneer Roll", "Egg Roll", "Chicken Roll"],
    "Healthy": ["Quinoa Bowl", "Greek Salad", "Grilled Sandwich"],
}
REVIEW_GOOD = ["hot and on time", "packaging was solid", "portion was generous",
               "exactly as described", "delivery was quick"]
REVIEW_BAD = ["arrived cold", "missing an item", "forty minutes late",
              "spilled in the bag", "not what was ordered"]

START = dt.date(2026, 8, 1)

# Minutes of kitchen time before a rider can even collect, by cuisine. Real and
# structural: a biryani is cooked to order and a dessert is plated. The SLA is a
# single flat 45 minutes for all of them, which is the thing mart_cuisine_sla
# exists to show is wrong -- the promise does not know what it promised.
PREP_MINUTES = {
    "Biryani": 26.0, "North Indian": 20.0, "Chinese": 16.0, "Pizza": 15.0,
    "South Indian": 12.0, "Rolls": 9.0, "Healthy": 8.0, "Desserts": 6.0,
}


def _ts(day: dt.date, rng: random.Random) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(0)) + dt.timedelta(
        hours=rng.uniform(8, 23), minutes=rng.uniform(0, 59))


def build(args) -> dict[str, list[dict]]:
    rng = random.Random(args.seed)

    restaurants = []
    for i in range(1, args.restaurants + 1):
        cuisine = rng.choice(CUISINES)
        restaurants.append({
            "restaurant_id": f"R{i:05d}",
            "name": f"{rng.choice(['Spice','Urban','Royal','Corner','Daily'])} "
                    f"{rng.choice(['Kitchen','House','Table','Bowl','Grill'])} {i}",
            "city": rng.choice(CITIES),
            "cuisine": cuisine,
            "rating": round(rng.uniform(3.0, 4.9), 1),
            "onboarded_at": (START - dt.timedelta(days=rng.randrange(30, 400))).isoformat(),
        })

    menu = []
    for r in restaurants:
        for dish in DISHES[r["cuisine"]]:
            menu.append({
                "menu_item_id": f"M{len(menu) + 1:06d}",
                "restaurant_id": r["restaurant_id"],
                "item_name": dish,
                "price": round(rng.uniform(90, 480), 2),
                "is_veg": rng.random() < 0.6,
            })
    by_restaurant: dict[str, list[dict]] = {}
    for m in menu:
        by_restaurant.setdefault(m["restaurant_id"], []).append(m)

    users = [{
        "user_id": f"U{i:06d}",
        "city": rng.choice(CITIES),
        "signed_up_at": (START - dt.timedelta(days=rng.randrange(1, 500))).isoformat(),
    } for i in range(1, args.users + 1)]

    orders, order_items, reviews = [], [], []
    for n in range(1, args.orders + 1):
        restaurant = rng.choice(restaurants)
        user = rng.choice(users)
        day = START + dt.timedelta(days=rng.randrange(args.days))
        placed = _ts(day, rng)
        # Prep is a property of the cuisine; the ride is not. Splitting them is
        # what makes the per-cuisine difference a real signal rather than noise
        # dressed up as one.
        prep = PREP_MINUTES[restaurant["cuisine"]] * rng.lognormvariate(0.0, 0.22)
        ride = rng.lognormvariate(0.0, 0.34) * 16
        minutes = max(6.0, prep + ride)
        delivered = placed + dt.timedelta(minutes=minutes)

        items = rng.sample(by_restaurant[restaurant["restaurant_id"]],
                           k=rng.randint(1, 3))
        total = 0.0
        order_id = f"O{n:07d}"
        for item in items:
            qty = rng.randint(1, 3)
            total += item["price"] * qty
            order_items.append({
                "order_item_id": f"OI{len(order_items) + 1:08d}",
                "order_id": order_id,
                "menu_item_id": item["menu_item_id"],
                "quantity": qty,
                "unit_price": item["price"],
            })

        restaurant_id = restaurant["restaurant_id"]
        # An order pointing at a restaurant that is not in the dimension. This
        # is what a row deleted between two exports looks like downstream, and
        # an inner join makes the order disappear instead of reporting it.
        if rng.random() < args.orphan_rate:
            restaurant_id = f"R{rng.randrange(90000, 99999)}"

        if rng.random() < args.late_delivery_rate:
            # Delivered before it was ordered. Two services, two clocks.
            delivered = placed - dt.timedelta(minutes=rng.uniform(2, 25))

        if rng.random() < args.negative_rate:
            total = -total           # a refund written into the orders table

        if rng.random() < args.currency_rate:
            total = total * 100      # paise into a rupees column

        orders.append({
            "order_id": order_id,
            "user_id": user["user_id"],
            "restaurant_id": restaurant_id,
            "city": restaurant["city"],
            "placed_at": placed.isoformat(sep=" ", timespec="seconds"),
            "delivered_at": delivered.isoformat(sep=" ", timespec="seconds"),
            "total_amount": round(total, 2),
            "payment_method": rng.choice(["upi", "card", "wallet", "cod"]),
        })

        if rng.random() < 0.42:
            late = (delivered - placed).total_seconds() / 60 > 45
            rating = None if rng.random() < args.null_rating_rate else (
                rng.randint(1, 3) if late else rng.randint(3, 5))
            reviews.append({
                "review_id": f"RV{len(reviews) + 1:07d}",
                "order_id": order_id,
                "restaurant_id": restaurant_id,
                "rating": rating,
                "review_text": rng.choice(REVIEW_BAD if late else REVIEW_GOOD),
                "created_at": (delivered + dt.timedelta(
                    hours=rng.uniform(0.5, 48))).isoformat(sep=" ", timespec="seconds"),
            })

    # An export that paginates and retries overlaps its pages, so the same order
    # arrives twice. Byte-identical, which is what makes it survivable -- and
    # which is why the staging model can dedupe on the key alone.
    extra = [dict(o) for o in rng.sample(orders, k=int(len(orders) * args.dup_rate))]
    orders.extend(extra)
    rng.shuffle(orders)

    return {"restaurants": restaurants, "menu": menu, "users": users,
            "orders": orders, "order_items": order_items, "reviews": reviews}


def write_csvs(tables: dict[str, list[dict]], out: pathlib.Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        path = out / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--orders", type=int, default=40000)
    p.add_argument("--restaurants", type=int, default=400)
    p.add_argument("--users", type=int, default=6000)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="data/raw")
    p.add_argument("--orphan-rate", type=float, default=0.004)
    p.add_argument("--dup-rate", type=float, default=0.012)
    p.add_argument("--null-rating-rate", type=float, default=0.08)
    p.add_argument("--late-delivery-rate", type=float, default=0.002)
    p.add_argument("--negative-rate", type=float, default=0.0015)
    p.add_argument("--currency-rate", type=float, default=0.001)
    args = p.parse_args(argv)

    tables = build(args)
    write_csvs(tables, pathlib.Path(args.out))
    for name, rows in tables.items():
        print(f"  {name:14} {len(rows):>8,}")
    print(f"\n  -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
