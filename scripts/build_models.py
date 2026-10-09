"""Build the dbt models and run every test, against DuckDB.

dbt is not installed here, and the local target is DuckDB rather than Snowflake,
so this resolves the Jinja dbt would resolve -- `ref`, `source`, `var` -- and
executes the same model files. The SQL under test is the SQL that ships; only
the templating happens here.

What it is NOT: a reimplementation of dbt. It handles exactly the three tags the
models use and refuses anything else, loudly, rather than leaving a brace in the
SQL for the engine to choke on fifty lines later.

It also runs the tests, which is the part that matters: a schema test is a query
that must return no rows, and a singular test is the same thing written out. Both
are just SQL, so both run here without dbt.

    python scripts/build_models.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import open_warehouse  # noqa: E402

DBT = ROOT / "dbt_foodpulse"
VARS = {"max_plausible_order": 12000, "sla_minutes": 45}

MODELS = [
    ("stg_orders", "view", "models/staging/stg_orders.sql"),
    ("stg_restaurants", "view", "models/staging/stg_restaurants.sql"),
    ("stg_users", "view", "models/staging/stg_users.sql"),
    ("stg_menu", "view", "models/staging/stg_menu.sql"),
    ("stg_order_items", "view", "models/staging/stg_order_items.sql"),
    ("stg_reviews", "view", "models/staging/stg_reviews.sql"),
    ("fct_orders", "table", "models/marts/fct_orders.sql"),
    ("dim_restaurant", "table", "models/marts/dim_restaurant.sql"),
    ("mart_city_daily", "table", "models/marts/mart_city_daily.sql"),
    ("mart_cuisine_sla", "table", "models/marts/mart_cuisine_sla.sql"),
    ("mart_data_quality", "table", "models/marts/mart_data_quality.sql"),
]

# The schema tests from the YAML, written as the queries dbt generates. Each one
# returns rows only on failure.
SCHEMA_TESTS = [
    ("unique", "stg_orders.order_id",
     "select order_id from stg_orders group by order_id having count(*) > 1"),
    ("not_null", "stg_orders.order_id",
     "select * from stg_orders where order_id is null"),
    ("not_null", "stg_orders.is_valid",
     "select * from stg_orders where is_valid is null"),
    ("relationships", "stg_orders.user_id -> stg_users",
     "select o.user_id from stg_orders o left join stg_users u using (user_id) "
     "where u.user_id is null"),
    ("unique", "stg_restaurants.restaurant_id",
     "select restaurant_id from stg_restaurants group by restaurant_id having count(*) > 1"),
    ("accepted_range", "stg_restaurants.platform_rating 0..5",
     "select * from stg_restaurants where platform_rating < 0 or platform_rating > 5"),
    ("unique", "stg_users.user_id",
     "select user_id from stg_users group by user_id having count(*) > 1"),
    ("unique", "stg_menu.menu_item_id",
     "select menu_item_id from stg_menu group by menu_item_id having count(*) > 1"),
    ("relationships", "stg_menu.restaurant_id -> stg_restaurants",
     "select m.restaurant_id from stg_menu m left join stg_restaurants r "
     "using (restaurant_id) where r.restaurant_id is null"),
    ("unique", "stg_order_items.order_item_id",
     "select order_item_id from stg_order_items group by order_item_id having count(*) > 1"),
    ("relationships", "stg_order_items.order_id -> stg_orders",
     "select i.order_id from stg_order_items i left join stg_orders o "
     "using (order_id) where o.order_id is null"),
    ("accepted_range", "stg_order_items.quantity >= 1",
     "select * from stg_order_items where quantity < 1"),
    ("unique", "stg_reviews.review_id",
     "select review_id from stg_reviews group by review_id having count(*) > 1"),
    ("accepted_range", "stg_reviews.rating 1..5",
     "select * from stg_reviews where rating is not null and (rating < 1 or rating > 5)"),
    ("unique", "fct_orders.order_id",
     "select order_id from fct_orders group by order_id having count(*) > 1"),
    ("not_null", "fct_orders.is_orphan",
     "select * from fct_orders where is_orphan is null"),
    ("unique", "dim_restaurant.restaurant_id",
     "select restaurant_id from dim_restaurant group by restaurant_id having count(*) > 1"),
    ("accepted_range", "dim_restaurant.orders_total >= 0",
     "select * from dim_restaurant where orders_total < 0"),
    ("unique", "mart_city_daily.city_day_key",
     "select city_day_key from mart_city_daily group by city_day_key having count(*) > 1"),
    ("accepted_range", "mart_city_daily.sla_breach_rate 0..1",
     "select * from mart_city_daily where sla_breach_rate < 0 or sla_breach_rate > 1"),
    ("unique", "mart_cuisine_sla.cuisine",
     "select cuisine from mart_cuisine_sla group by cuisine having count(*) > 1"),
    ("not_null", "mart_cuisine_sla.p90_minutes",
     "select * from mart_cuisine_sla where p90_minutes is null"),
]

_SOURCE = re.compile(r"\{\{\s*source\(\s*'raw'\s*,\s*'(\w+)'\s*\)\s*\}\}")
_REF = re.compile(r"\{\{\s*ref\(\s*'(\w+)'\s*\)\s*\}\}")
_VAR = re.compile(r"\{\{\s*var\(\s*'(\w+)'\s*\)\s*\}\}")
_ANY_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def compile_sql(path: pathlib.Path) -> str:
    sql = path.read_text(encoding="utf-8")
    sql = _SOURCE.sub(lambda m: m.group(1), sql)
    sql = _REF.sub(lambda m: m.group(1), sql)

    def var(match):
        name = match.group(1)
        if name not in VARS:
            raise KeyError(f"{path.name} uses var('{name}'), which is not declared")
        return str(VARS[name])

    sql = _VAR.sub(var, sql)
    leftover = _ANY_TAG.search(sql)
    if leftover:
        raise NotImplementedError(
            f"{path.name} uses a template tag this builder does not handle: "
            f"{leftover.group(0)[:60]}")
    return sql


def main() -> int:
    wh = open_warehouse()
    conn = wh.connect()
    failures: list[tuple[str, str, int]] = []
    try:
        print(f"models  ({wh.flavour})")
        for name, materialisation, relative in MODELS:
            sql = compile_sql(DBT / relative)
            keyword = "VIEW" if materialisation == "view" else "TABLE"
            # IF EXISTS does not protect against a type mismatch: dropping a
            # VIEW that exists as a TABLE raises, and a model that changes
            # materialisation between runs would fail on the second one. So the
            # catalog decides which keyword to use.
            existing = conn.execute(
                "SELECT table_type FROM information_schema.tables "
                "WHERE table_name = ?", [name]).fetchall()
            for (kind,) in existing:
                conn.execute(f"DROP {'VIEW' if 'VIEW' in kind.upper() else 'TABLE'} "
                             f"IF EXISTS {name}")
            conn.execute(f"CREATE {keyword} {name} AS {sql}")
            print(f"  built {materialisation:5}  {name}")

        print("\nschema tests")
        for kind, subject, sql in SCHEMA_TESTS:
            rows = conn.execute(sql).fetchall()
            ok = not rows
            if not ok:
                failures.append((kind, subject, len(rows)))
            print(f"  {'PASS' if ok else 'FAIL':5}  {kind:<15} {subject}"
                  + ("" if ok else f"   ({len(rows)} offending rows)"))

        print("\nsingular tests")
        for path in sorted((DBT / "tests").glob("*.sql")):
            rows = conn.execute(compile_sql(path)).fetchall()
            ok = not rows
            if not ok:
                failures.append(("singular", path.stem, len(rows)))
            print(f"  {'PASS' if ok else 'FAIL':5}  {path.stem}"
                  + ("" if ok else f"   ({len(rows)} offending rows)"))
    finally:
        conn.close()

    total = len(SCHEMA_TESTS) + len(list((DBT / "tests").glob("*.sql")))
    print(f"\n{total - len(failures)}/{total} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
