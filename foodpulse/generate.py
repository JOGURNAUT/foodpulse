"""Synthetic food-delivery data, with the defects a warehouse has to survive.

Six tables in the shape a delivery marketplace actually keeps them: restaurants
and users as dimensions, a menu, orders, the items inside those orders, and
free-text reviews.

Generated rather than downloaded, and seeded, so the whole project runs from a
clone with no credentials and no 2 GB file in git. Same seed, same rows.

Rows are handed to an `emit` callback one at a time rather than returned as
lists. At 2,000,000 orders the facts are roughly 4.6M line items and 800K
reviews, which does not want to sit in memory at once -- `main()` streams them
straight to CSV while `build()` collects them for the tests. One code path, two
sinks, so the tests exercise the generator that ships.

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
from collections import deque

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

# How far back a duplicated order can come from. A real export re-sends the tail
# of the previous page, not a row from the middle of the file, so a duplicate
# lands near its original rather than anywhere.
DUP_WINDOW = 64

TABLES = ("restaurants", "menu", "users", "orders", "order_items", "reviews")


def _ts(day: dt.date, rng: random.Random) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(0)) + dt.timedelta(
        hours=rng.uniform(8, 23), minutes=rng.uniform(0, 59))


def generate(args, emit) -> None:
    """Produce every row, handing each to `emit(table_name, row)`.

    The single source of truth for what the data looks like. `build()` collects
    the rows into lists and `main()` streams them to CSV, so neither can drift
    from what the tests assert.
    """
    rng = random.Random(args.seed)

    restaurants = []
    for i in range(1, args.restaurants + 1):
        cuisine = rng.choice(CUISINES)
        row = {
            "restaurant_id": f"R{i:05d}",
            "name": f"{rng.choice(['Spice','Urban','Royal','Corner','Daily'])} "
                    f"{rng.choice(['Kitchen','House','Table','Bowl','Grill'])} {i}",
            "city": rng.choice(CITIES),
            "cuisine": cuisine,
            "rating": round(rng.uniform(3.0, 4.9), 1),
            "onboarded_at": (START - dt.timedelta(days=rng.randrange(30, 400))).isoformat(),
        }
        restaurants.append(row)
        emit("restaurants", row)

    by_restaurant: dict[str, list[dict]] = {}
    menu_count = 0
    for r in restaurants:
        for dish in DISHES[r["cuisine"]]:
            menu_count += 1
            row = {
                "menu_item_id": f"M{menu_count:06d}",
                "restaurant_id": r["restaurant_id"],
                "item_name": dish,
                "price": round(rng.uniform(90, 480), 2),
                "is_veg": rng.random() < 0.6,
            }
            by_restaurant.setdefault(r["restaurant_id"], []).append(row)
            emit("menu", row)

    users = []
    for i in range(1, args.users + 1):
        row = {
            "user_id": f"U{i:06d}",
            "city": rng.choice(CITIES),
            "signed_up_at": (START - dt.timedelta(days=rng.randrange(1, 500))).isoformat(),
        }
        users.append(row)
        emit("users", row)

    item_count = 0
    review_count = 0
    # Only the recent tail is kept, so memory stays flat however many orders are
    # asked for.
    recent: deque[dict] = deque(maxlen=DUP_WINDOW)

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
        order_id = f"O{n:08d}"
        for item in items:
            qty = rng.randint(1, 3)
            total += item["price"] * qty
            item_count += 1
            emit("order_items", {
                "order_item_id": f"OI{item_count:09d}",
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

        order = {
            "order_id": order_id,
            "user_id": user["user_id"],
            "restaurant_id": restaurant_id,
            "city": restaurant["city"],
            "placed_at": placed.isoformat(sep=" ", timespec="seconds"),
            "delivered_at": delivered.isoformat(sep=" ", timespec="seconds"),
            "total_amount": round(total, 2),
            "payment_method": rng.choice(["upi", "card", "wallet", "cod"]),
        }
        emit("orders", order)
        recent.append(order)

        if rng.random() < 0.42:
            late = (delivered - placed).total_seconds() / 60 > 45
            rating = None if rng.random() < args.null_rating_rate else (
                rng.randint(1, 3) if late else rng.randint(3, 5))
            review_count += 1
            emit("reviews", {
                "review_id": f"RV{review_count:08d}",
                "order_id": order_id,
                "restaurant_id": restaurant_id,
                "rating": rating,
                "review_text": rng.choice(REVIEW_BAD if late else REVIEW_GOOD),
                "created_at": (delivered + dt.timedelta(
                    hours=rng.uniform(0.5, 48))).isoformat(sep=" ", timespec="seconds"),
            })

        # An export that paginates and retries re-sends rows it already sent, so
        # the same order arrives twice. Byte-identical, which is what makes it
        # survivable -- and which is why the staging model can dedupe on the key
        # alone. Only the order header repeats; its line items do not, which is
        # exactly the asymmetry stg_orders has to handle.
        if len(recent) == DUP_WINDOW and rng.random() < args.dup_rate:
            emit("orders", dict(recent[rng.randrange(DUP_WINDOW)]))


def build(args) -> dict[str, list[dict]]:
    """Every row in memory, keyed by table. For tests and small runs."""
    tables: dict[str, list[dict]] = {name: [] for name in TABLES}

    def emit(table: str, row: dict) -> None:
        tables[table].append(row)

    generate(args, emit)
    return tables


class CsvSink:
    """Opens one CSV per table and writes rows as they arrive.

    The header comes from the first row of each table, so a column added to a
    row dict reaches the file without a schema declared in two places.
    """

    def __init__(self, out: pathlib.Path):
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, object] = {}
        self._writers: dict[str, csv.DictWriter] = {}
        self.counts: dict[str, int] = {}

    def emit(self, table: str, row: dict) -> None:
        writer = self._writers.get(table)
        if writer is None:
            fh = (self.out / f"{table}.csv").open("w", newline="", encoding="utf-8")
            writer = csv.DictWriter(fh, fieldnames=list(row))
            writer.writeheader()
            self._files[table] = fh
            self._writers[table] = writer
            self.counts[table] = 0
        writer.writerow(row)
        self.counts[table] += 1

    def close(self) -> None:
        for fh in self._files.values():
            fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--orders", type=int, default=2_000_000)
    p.add_argument("--restaurants", type=int, default=2000)
    p.add_argument("--users", type=int, default=150_000)
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="data/raw")
    p.add_argument("--orphan-rate", type=float, default=0.004)
    p.add_argument("--dup-rate", type=float, default=0.012)
    p.add_argument("--null-rating-rate", type=float, default=0.08)
    p.add_argument("--late-delivery-rate", type=float, default=0.002)
    p.add_argument("--negative-rate", type=float, default=0.0015)
    p.add_argument("--currency-rate", type=float, default=0.001)
    args = p.parse_args(argv)

    out = pathlib.Path(args.out)
    with CsvSink(out) as sink:
        generate(args, sink.emit)
        counts = dict(sink.counts)

    for name in TABLES:
        path = out / f"{name}.csv"
        mb = path.stat().st_size / 1024 / 1024 if path.exists() else 0
        print(f"  {name:14} {counts.get(name, 0):>10,}  {mb:>8.1f} MB")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
