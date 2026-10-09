"""Tests for the type-2 snapshot: history the source has already destroyed.

The restaurants export is a picture of right now. A restaurant that moved cities
in September looks, in today's file, like it was always where it is now. Join a
September order to the current dimension and it lands in the wrong city, with
nothing to say so -- the source does not lie, it simply has no memory.

The test that matters here is test_a_point_in_time_join_finds_exactly_one_row.
A snapshot whose windows have a gap or an overlap looks completely fine in a
row count and breaks every historical join: a query landing in the gap finds no
row and silently drops the order, and one landing in an overlap finds two and
silently doubles it.
"""

from __future__ import annotations

import csv
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import DuckWarehouse  # noqa: E402
from scripts.build_models import build_model, build_snapshot  # noqa: E402

DBT = ROOT / "dbt_foodpulse"
SNAP = DBT / "snapshots" / "snap_restaurant.sql"
STG = DBT / "models" / "staging" / "stg_restaurants.sql"


def _write(path: pathlib.Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _restaurants(city: str = "Pune", rating: float = 4.2) -> list[dict]:
    return [
        {"restaurant_id": "R00001", "name": "Corner Table 1", "city": city,
         "cuisine": "Biryani", "rating": rating, "onboarded_at": "2026-01-01"},
        {"restaurant_id": "R00002", "name": "Spice House 2", "city": "Kochi",
         "cuisine": "Desserts", "rating": 4.0, "onboarded_at": "2026-01-01"},
    ]


class Fixture:
    def __init__(self, tmp: pathlib.Path):
        self.raw = tmp / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.wh = DuckWarehouse(path=str(tmp / "wh.duckdb"))
        self.conn = self.wh.connect()

    def source(self, rows: list[dict]) -> None:
        path = self.raw / "restaurants.csv"
        _write(path, rows)
        self.wh.load_csv(self.conn, "restaurants", path)
        build_model(self.conn, "stg_restaurants", STG, full_refresh=False)

    def snap(self) -> str:
        return build_snapshot(self.conn, "snap_restaurant", SNAP)

    def versions(self, rid: str = "R00001") -> list[tuple]:
        return self.conn.execute(
            "SELECT city, platform_rating, dbt_valid_from, dbt_valid_to "
            "FROM snap_restaurant WHERE restaurant_id = ? "
            "ORDER BY dbt_valid_from", [rid]).fetchall()

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def f(tmp_path):
    fx = Fixture(tmp_path)
    yield fx
    fx.close()


def test_the_first_run_opens_one_version_per_key(f):
    f.source(_restaurants())
    f.snap()
    rows = f.versions()
    assert len(rows) == 1
    assert rows[0][0] == "Pune"
    assert rows[0][3] is None, "the current version must stay open"


def test_an_unchanged_source_adds_nothing(f):
    """The property that makes this runnable on a schedule. A snapshot that
    opened a version every run would turn a dimension of 2,000 restaurants into
    an unbounded table and bury the real moves in noise."""
    f.source(_restaurants())
    f.snap()
    before = f.conn.execute("SELECT count(*) FROM snap_restaurant").fetchone()[0]

    f.source(_restaurants())
    f.snap()
    after = f.conn.execute("SELECT count(*) FROM snap_restaurant").fetchone()[0]
    assert after == before


def test_a_changed_column_closes_the_old_version_and_opens_a_new_one(f):
    f.source(_restaurants(city="Pune"))
    f.snap()

    f.source(_restaurants(city="Bengaluru"))
    f.snap()

    rows = f.versions()
    assert len(rows) == 2
    assert rows[0][0] == "Pune" and rows[0][3] is not None, "old must be closed"
    assert rows[1][0] == "Bengaluru" and rows[1][3] is None, "new must be open"


def test_only_one_version_is_ever_open(f):
    """Two open rows for one key doubles that restaurant in every join that
    filters on dbt_valid_to IS NULL, which is how a dimension starts inflating
    revenue without any number looking obviously wrong."""
    f.source(_restaurants(city="Pune"))
    f.snap()
    f.source(_restaurants(city="Bengaluru"))
    f.snap()
    f.source(_restaurants(city="Jaipur"))
    f.snap()

    open_rows = f.conn.execute(
        "SELECT restaurant_id, count(*) FROM snap_restaurant "
        "WHERE dbt_valid_to IS NULL GROUP BY restaurant_id "
        "HAVING count(*) > 1").fetchall()
    assert not open_rows
    assert len(f.versions()) == 3


def test_a_point_in_time_join_finds_exactly_one_row(f):
    """The one that matters.

    Windows have to meet exactly: a closed row's valid_to must equal the next
    row's valid_from. A gap and the order falls through every historical join;
    an overlap and it is counted twice. Both look fine in a row count.
    """
    f.source(_restaurants(city="Pune"))
    f.snap()
    f.source(_restaurants(city="Bengaluru"))
    f.snap()

    import datetime as dt

    _city, _rating, pune_from, boundary = f.versions()[0]
    tick = dt.timedelta(microseconds=1)

    def city_at(moment):
        return f.conn.execute(
            "SELECT city FROM snap_restaurant "
            "WHERE restaurant_id = 'R00001' "
            "  AND dbt_valid_from <= ? "
            "  AND (dbt_valid_to > ? OR dbt_valid_to IS NULL)",
            [moment, moment]).fetchall()

    for moment, expected in (
        (pune_from, "Pune"),             # the instant the first version opened
        (boundary - tick, "Pune"),       # a microsecond before the move
        (boundary, "Bengaluru"),         # the exact boundary: the new row owns it
        (boundary + tick, "Bengaluru"),  # after
    ):
        hits = city_at(moment)
        assert len(hits) == 1, f"at {moment} found {len(hits)} rows, not 1"
        assert hits[0][0] == expected, f"at {moment} got {hits[0][0]}"


def test_a_column_outside_check_cols_does_not_open_a_version(f):
    """check_cols is deliberately not every column. Including something that
    churns on its own would open a version every run and bury the real moves."""
    f.source(_restaurants(rating=4.2))
    f.snap()
    before = f.conn.execute("SELECT count(*) FROM snap_restaurant").fetchone()[0]

    # onboarded_at is selected but not checked; changing it must not matter.
    rows = _restaurants(rating=4.2)
    rows[0]["onboarded_at"] = "2025-06-15"
    f.source(rows)
    f.snap()

    after = f.conn.execute("SELECT count(*) FROM snap_restaurant").fetchone()[0]
    assert after == before


def test_a_new_restaurant_is_added_without_touching_the_others(f):
    f.source(_restaurants())
    f.snap()

    rows = _restaurants() + [
        {"restaurant_id": "R00003", "name": "Daily Bowl 3", "city": "Jaipur",
         "cuisine": "Rolls", "rating": 4.5, "onboarded_at": "2026-02-01"}]
    f.source(rows)
    f.snap()

    assert len(f.versions("R00003")) == 1
    assert len(f.versions("R00001")) == 1, "an unrelated key must not re-version"
