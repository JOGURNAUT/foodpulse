"""Tests for the incremental models: the merge, and the lookback window.

These build a real warehouse in a temp directory, run the models, add rows the
way a second export would, and run them again. Nothing here is mocked, because
what is being tested is the SQL -- a mocked warehouse would only prove that the
Python around it calls the right functions.

The one that earns its place is test_a_late_arriving_order_is_still_picked_up,
together with the one below it that shows what the naive filter would have done
instead. An incremental model that loses late rows loses them silently: the run
succeeds, every schema test passes, the row simply is not there. Nobody notices
until a monthly total disagrees with the source, by which point the window to
reprocess has closed.
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import DuckWarehouse  # noqa: E402
from scripts.build_models import MODELS, build_model  # noqa: E402

DBT = ROOT / "dbt_foodpulse"
BASE_DAY = dt.datetime(2026, 8, 20, 12, 0, 0)


def _rows(n: int, start: int = 1, at: dt.datetime = BASE_DAY) -> list[dict]:
    """n plain, valid orders, one every minute from `at`.

    The minute offset counts from this call, not from the id, so `at` is really
    when these orders happened. Deriving it from `start` instead would put a row
    with a high id in the future however early `at` was, which is the opposite of
    what the late-arrival tests need.
    """
    out = []
    for offset, i in enumerate(range(start, start + n)):
        placed = at + dt.timedelta(minutes=offset)
        out.append({
            "order_id": f"O{i:08d}",
            "user_id": "U000001",
            "restaurant_id": "R00001",
            "city": "Pune",
            "placed_at": placed.isoformat(sep=" ", timespec="seconds"),
            "delivered_at": (placed + dt.timedelta(minutes=30)).isoformat(
                sep=" ", timespec="seconds"),
            "total_amount": 500.0,
            "payment_method": "upi",
        })
    return out


def _write(path: pathlib.Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


class Fixture:
    """A warehouse with the raw tables loaded, that can reload and rebuild."""

    def __init__(self, tmp: pathlib.Path):
        self.raw = tmp / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.wh = DuckWarehouse(path=str(tmp / "wh.duckdb"))
        self.conn = self.wh.connect()

        _write(self.raw / "restaurants.csv", [{
            "restaurant_id": "R00001", "name": "Corner Table 1", "city": "Pune",
            "cuisine": "Biryani", "rating": 4.2, "onboarded_at": "2026-01-01"}])
        _write(self.raw / "menu.csv", [{
            "menu_item_id": "M000001", "restaurant_id": "R00001",
            "item_name": "Chicken Biryani", "price": 250.0, "is_veg": False}])
        _write(self.raw / "users.csv", [{
            "user_id": "U000001", "city": "Pune", "signed_up_at": "2026-01-01"}])
        _write(self.raw / "reviews.csv", [{
            "review_id": "RV00000001", "order_id": "O00000001",
            "restaurant_id": "R00001", "rating": 5,
            "review_text": "hot and on time", "created_at": "2026-08-20 14:00:00"}])

    def set_orders(self, orders: list[dict]) -> None:
        _write(self.raw / "orders.csv", orders)
        # Two lines of 250 each, so the header's 500 reconciles.
        items = []
        for o in orders:
            for k in (1, 2):
                items.append({
                    "order_item_id": f"OI{o['order_id'][1:]}{k}",
                    "order_id": o["order_id"], "menu_item_id": "M000001",
                    "quantity": 1, "unit_price": 250.0})
        _write(self.raw / "order_items.csv", items)

    def load(self) -> None:
        for name in ("restaurants", "menu", "users", "orders", "order_items",
                     "reviews"):
            self.wh.load_csv(self.conn, name, self.raw / f"{name}.csv")

    def build(self, *, full_refresh: bool = False, upto: str = "fct_orders") -> None:
        for name, relative in MODELS:
            build_model(self.conn, name, DBT / relative, full_refresh=full_refresh)
            if name == upto:
                return

    def orders(self) -> list[tuple]:
        return self.conn.execute(
            "select order_id, total_amount from fct_orders order by order_id"
        ).fetchall()

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def wh(tmp_path):
    f = Fixture(tmp_path)
    yield f
    f.close()


def test_the_first_run_builds_the_whole_table(wh):
    wh.set_orders(_rows(100))
    wh.load()
    wh.build()
    assert len(wh.orders()) == 100


def test_a_second_run_with_no_new_data_changes_nothing(wh):
    """The merge has to be idempotent. A run that re-inserts what it already has
    doubles the table and every total built on it, while the row counts still
    look like a plausible business."""
    wh.set_orders(_rows(100))
    wh.load()
    wh.build()
    before = wh.orders()

    wh.build()
    assert wh.orders() == before


def test_new_orders_are_merged_in(wh):
    wh.set_orders(_rows(100))
    wh.load()
    wh.build()

    # A second export: the same 100 orders plus 20 new ones, which is what an
    # export that re-sends its tail actually looks like.
    wh.set_orders(_rows(120))
    wh.load()
    wh.build()

    rows = wh.orders()
    assert len(rows) == 120
    assert len({r[0] for r in rows}) == 120, "an order was inserted twice"


def test_a_corrected_order_is_updated_not_duplicated(wh):
    """Last write wins on the key. An amount restated by the source has to
    replace the old row -- an insert would leave both, and revenue would count
    the order twice at two different values."""
    wh.set_orders(_rows(50))
    wh.load()
    wh.build()

    corrected = _rows(50)
    corrected[10]["total_amount"] = 999.0
    wh.set_orders(corrected)
    wh.load()
    wh.build()

    rows = dict(wh.orders())
    assert len(rows) == 50
    assert rows[corrected[10]["order_id"]] == 999.0


def test_a_late_arriving_order_is_still_picked_up(wh):
    """The one that matters.

    An order placed before rows that have already loaded, arriving in a later
    export. The watermark is already past it. The lookback window is the only
    reason it is selected at all.
    """
    wh.set_orders(_rows(100))
    wh.load()
    wh.build()

    late = _rows(1, start=9001, at=BASE_DAY - dt.timedelta(days=1))
    wh.set_orders(_rows(100) + late)
    wh.load()
    wh.build()

    ids = {r[0] for r in wh.orders()}
    assert late[0]["order_id"] in ids, "a late arrival was silently dropped"
    assert len(ids) == 101


def test_the_naive_watermark_would_have_lost_it(wh):
    """The premise of the test above, asserted rather than claimed.

    This runs the filter the lookback replaced -- strictly newer than the newest
    row already loaded -- and shows the late order falls outside it. If someone
    ever 'simplifies' the model by dropping the interval, this is the test that
    explains what that costs.
    """
    wh.set_orders(_rows(100))
    wh.load()
    wh.build()

    late_at = BASE_DAY - dt.timedelta(days=1)
    late = _rows(1, start=9001, at=late_at)
    wh.set_orders(_rows(100) + late)
    wh.load()

    watermark = wh.conn.execute("select max(placed_at) from fct_orders").fetchone()[0]
    selected = wh.conn.execute(
        "select count(*) from stg_orders where placed_at > ?", [watermark]).fetchone()[0]
    assert selected == 0, "the naive filter would have selected the late row"

    with_lookback = wh.conn.execute(
        "select count(*) from stg_orders "
        "where placed_at >= ? - interval 3 day", [watermark]).fetchone()[0]
    assert with_lookback > 0, "the lookback must select it"


def test_the_window_bounds_what_is_reprocessed(wh):
    """The lookback is not free. It reprocesses a trailing window on every run,
    and this pins how much -- so widening it is a decision someone makes rather
    than something that drifts."""
    wh.set_orders(_rows(5000))
    wh.load()
    wh.build()

    watermark = wh.conn.execute("select max(placed_at) from fct_orders").fetchone()[0]
    reprocessed = wh.conn.execute(
        "select count(*) from stg_orders where placed_at >= ? - interval 3 day",
        [watermark]).fetchone()[0]

    assert reprocessed < 5000, "the window must be narrower than a full rebuild"
    assert reprocessed > 0, "an empty window would never pick up a late row"


def test_line_items_merge_on_their_own_grain(wh):
    """fct_order_items is at line grain, so its key is order_item_id. Merging it
    on order_id would keep one line per order and silently drop the rest of every
    multi-item basket."""
    wh.set_orders(_rows(50))
    wh.load()
    wh.build(upto="fct_order_items")

    n = wh.conn.execute("select count(*) from fct_order_items").fetchone()[0]
    assert n == 100, "two lines per order"

    wh.set_orders(_rows(60))
    wh.load()
    wh.build(upto="fct_order_items")

    n = wh.conn.execute("select count(*) from fct_order_items").fetchone()[0]
    distinct = wh.conn.execute(
        "select count(distinct order_item_id) from fct_order_items").fetchone()[0]
    assert n == distinct == 120
