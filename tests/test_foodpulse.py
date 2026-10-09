"""Tests for the Python layer: the generator and the warehouse router.

The dbt models have their own tests, run by scripts/build_models.py. These cover
what SQL cannot: that the generator actually injects the defects the models are
written to catch, and that the warehouse picks its backend from the URI.

A generator that quietly stopped injecting defects would leave every dbt test
passing over clean data, which proves nothing and looks like success.
"""

from __future__ import annotations

import argparse

import pytest

from foodpulse.generate import PREP_MINUTES, build
from foodpulse.warehouse import (
    DuckWarehouse, SnowflakeWarehouse, WarehouseError, _check_identifier,
    open_warehouse,
)


def args(**over):
    base = dict(orders=3000, restaurants=60, users=400, days=20, seed=5,
                orphan_rate=0.02, dup_rate=0.03, null_rating_rate=0.1,
                late_delivery_rate=0.01, negative_rate=0.01, currency_rate=0.01)
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture(scope="module")
def tables():
    return build(args())


# ------------------------------------------------- the defects must be there

def test_duplicate_orders_are_actually_injected(tables):
    """The export paginates and retries, so pages overlap. If this stopped
    happening, the dedupe in staging would be exercising nothing."""
    ids = [o["order_id"] for o in tables["orders"]]
    assert len(ids) > len(set(ids))


def test_duplicates_are_byte_identical(tables):
    """Which is what makes dedupe-by-key correct: there is no later version to
    prefer, so any one copy is the row. A differing copy would be a correction
    and would need last-write-wins instead."""
    seen: dict[str, dict] = {}
    for o in tables["orders"]:
        if o["order_id"] in seen:
            assert o == seen[o["order_id"]]
        seen[o["order_id"]] = o


def test_some_orders_point_at_a_restaurant_that_does_not_exist(tables):
    """What a row deleted between two exports looks like downstream. An inner
    join makes these disappear instead of reporting them."""
    known = {r["restaurant_id"] for r in tables["restaurants"]}
    orphans = [o for o in tables["orders"] if o["restaurant_id"] not in known]
    assert orphans


def test_some_deliveries_precede_their_order(tables):
    """Two services, two clocks. Produces a negative duration, which would drag
    an average down and look like good news."""
    bad = [o for o in tables["orders"] if o["delivered_at"] < o["placed_at"]]
    assert bad


def test_some_amounts_are_negative(tables):
    """A refund written into the orders table."""
    assert [o for o in tables["orders"] if o["total_amount"] < 0]


def test_some_reviews_have_no_rating(tables):
    """Not missing data: the user wrote text and skipped the stars. Scoring
    those as zero would drag every restaurant's average down."""
    withheld = [r for r in tables["reviews"] if r["rating"] is None]
    assert withheld
    assert all(r["review_text"] for r in withheld)


# ------------------------------------------------------ structure, not noise

def test_prep_time_is_a_property_of_the_cuisine():
    """The finding has to come from the system, not from the draw.

    Prep minutes are written into the generator per cuisine, which is why the
    SLA mart separates them. If the speeds were ever flattened, the finding
    would evaporate and every other test here would still pass.
    """
    assert len(set(PREP_MINUTES.values())) > 1
    assert PREP_MINUTES["Biryani"] > PREP_MINUTES["Desserts"] * 3


def test_the_slow_cuisine_is_slower_on_every_seed():
    """Decimals move with the seed; the ordering must not."""
    for seed in (1, 42, 777):
        t = build(args(seed=seed, orders=4000))
        cuisine = {r["restaurant_id"]: r["cuisine"] for r in t["restaurants"]}
        import datetime as dt
        times: dict[str, list[float]] = {}
        for o in t["orders"]:
            c = cuisine.get(o["restaurant_id"])
            if not c:
                continue
            placed = dt.datetime.fromisoformat(o["placed_at"])
            delivered = dt.datetime.fromisoformat(o["delivered_at"])
            mins = (delivered - placed).total_seconds() / 60
            if mins > 0:
                times.setdefault(c, []).append(mins)
        biryani = sum(times["Biryani"]) / len(times["Biryani"])
        dessert = sum(times["Desserts"]) / len(times["Desserts"])
        assert biryani > dessert, f"seed {seed}: {biryani:.1f} vs {dessert:.1f}"


def test_the_same_seed_gives_the_same_data():
    """Reproducibility, which is what makes any number on the page quotable."""
    assert build(args(seed=99)) == build(args(seed=99))


def test_line_items_reconcile_for_uncorrupted_orders(tables):
    """The generator's own arithmetic. If the header stopped matching its lines
    for ordinary orders, the reconciliation test downstream would fire on every
    row and stop meaning anything."""
    lines: dict[str, float] = {}
    for i in tables["order_items"]:
        lines[i["order_id"]] = lines.get(i["order_id"], 0) + i["quantity"] * i["unit_price"]
    clean = [o for o in tables["orders"]
             if o["total_amount"] > 0
             and abs(o["total_amount"] - lines.get(o["order_id"], 0)) < 0.02]
    assert len(clean) > len(tables["orders"]) * 0.9


# ------------------------------------------------------------- the warehouse

def test_the_uri_scheme_picks_the_backend(tmp_path):
    """The argument that names the target also picks the driver, so a
    deployment cannot be configured with a Snowflake account and a local-mode
    flag that disagree."""
    assert isinstance(open_warehouse(str(tmp_path / "x.duckdb")), DuckWarehouse)
    assert isinstance(open_warehouse("snowflake://acct/DB/SCHEMA"), SnowflakeWarehouse)


def test_a_malformed_snowflake_uri_is_rejected():
    with pytest.raises(WarehouseError, match="ACCOUNT/DATABASE/SCHEMA"):
        open_warehouse("snowflake://justanaccount")


def test_table_identifiers_are_validated_not_trusted():
    """Table names are interpolated into DDL, which parameters cannot carry. A
    loader that interpolates unchecked identifiers is one refactor away from
    being injectable."""
    assert _check_identifier("orders") == "orders"
    for bad in ("orders; DROP TABLE users", "orders--", "1orders", ""):
        with pytest.raises(WarehouseError):
            _check_identifier(bad)


def test_loading_twice_replaces_rather_than_doubles(tmp_path):
    """Re-running a load has to produce the table, not add to it."""
    import csv

    raw = tmp_path / "raw"
    raw.mkdir()
    path = raw / "users.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["user_id", "city", "signed_up_at"])
        w.writeheader()
        w.writerows([{"user_id": f"U{i}", "city": "Pune",
                      "signed_up_at": "2026-01-01"} for i in range(50)])

    wh = DuckWarehouse(path=str(tmp_path / "wh.duckdb"))
    conn = wh.connect()
    try:
        assert wh.load_csv(conn, "users", path) == 50
        assert wh.load_csv(conn, "users", path) == 50
    finally:
        conn.close()
