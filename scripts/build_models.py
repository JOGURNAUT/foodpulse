"""Build the dbt models and run every test, against DuckDB.

dbt is not installed here, and the local target is DuckDB rather than Snowflake,
so this resolves the Jinja dbt would resolve and executes the same model files.
The SQL under test is the SQL that ships; only the templating happens here.

What it handles, which is exactly what the models use and nothing more:

    {{ source('raw', 'orders') }}     the raw table
    {{ ref('stg_orders') }}           another model
    {{ var('sla_minutes') }}          a project variable
    {{ this }}                        the model's own relation
    {{ config(...) }}                 materialisation and its settings
    {% if is_incremental() %} ... {% endif %}

Anything else raises rather than leaving a brace in the SQL for the engine to
choke on fifty lines later.

Materialisations:

    view          CREATE VIEW
    table         CREATE OR REPLACE TABLE
    incremental   CREATE on the first run, MERGE on every run after

The MERGE is a real MERGE, not a delete-and-reinsert. DuckDB 1.4 and Snowflake
both have `MERGE INTO`, so the statement this builds locally is the statement
Snowflake would run -- which is the only reason testing it here means anything.

    python scripts/build_models.py
    python scripts/build_models.py --full-refresh     # rebuild incrementals
    python scripts/build_models.py --select fct_orders
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import open_warehouse  # noqa: E402

DBT = ROOT / "dbt_foodpulse"
VARS = {"max_plausible_order": 12000, "sla_minutes": 45, "lookback_days": 3}

# Dependency order. dbt derives this from the ref() graph; one ordered list is
# honest about what this is and cannot silently build a model before its parent.
MODELS = [
    ("stg_orders", "models/staging/stg_orders.sql"),
    ("stg_restaurants", "models/staging/stg_restaurants.sql"),
    ("stg_users", "models/staging/stg_users.sql"),
    ("stg_menu", "models/staging/stg_menu.sql"),
    ("stg_order_items", "models/staging/stg_order_items.sql"),
    ("stg_reviews", "models/staging/stg_reviews.sql"),
    ("fct_orders", "models/marts/fct_orders.sql"),
    ("fct_order_items", "models/marts/fct_order_items.sql"),
    ("dim_date", "models/marts/dim_date.sql"),
    ("dim_restaurant", "models/marts/dim_restaurant.sql"),
    ("dim_user", "models/marts/dim_user.sql"),
    ("dim_menu_item", "models/marts/dim_menu_item.sql"),
    ("mart_city_daily", "models/marts/mart_city_daily.sql"),
    ("mart_cuisine_sla", "models/marts/mart_cuisine_sla.sql"),
    ("mart_restaurant_performance", "models/marts/mart_restaurant_performance.sql"),
    ("mart_data_quality", "models/marts/mart_data_quality.sql"),
]

# Tagged `ai`: these depend on review_enriched, which the LLM task produces.
# Skipped unless that table exists, so the core warehouse builds and passes
# every test with no API key anywhere near it. `dbt build --exclude tag:ai` is
# the same idea on the real thing.
AI_MODELS = [
    ("mart_review_insights", "models/marts/mart_review_insights.sql"),
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
    ("unique", "dim_date.date_day",
     "select date_day from dim_date group by date_day having count(*) > 1"),
    ("not_null", "dim_date.is_weekend",
     "select * from dim_date where is_weekend is null"),
    ("unique", "fct_orders.order_id",
     "select order_id from fct_orders group by order_id having count(*) > 1"),
    ("not_null", "fct_orders.is_orphan",
     "select * from fct_orders where is_orphan is null"),
    ("unique", "fct_order_items.order_item_id",
     "select order_item_id from fct_order_items group by order_item_id having count(*) > 1"),
    ("relationships", "fct_order_items.order_id -> fct_orders",
     "select i.order_id from fct_order_items i left join fct_orders o "
     "using (order_id) where o.order_id is null"),
    ("unique", "dim_restaurant.restaurant_id",
     "select restaurant_id from dim_restaurant group by restaurant_id having count(*) > 1"),
    ("accepted_range", "dim_restaurant.orders_total >= 0",
     "select * from dim_restaurant where orders_total < 0"),
    ("unique", "dim_user.user_id",
     "select user_id from dim_user group by user_id having count(*) > 1"),
    ("unique", "dim_menu_item.menu_item_id",
     "select menu_item_id from dim_menu_item group by menu_item_id having count(*) > 1"),
    ("unique", "mart_city_daily.city_day_key",
     "select city_day_key from mart_city_daily group by city_day_key having count(*) > 1"),
    ("accepted_range", "mart_city_daily.sla_breach_rate 0..1",
     "select * from mart_city_daily where sla_breach_rate < 0 or sla_breach_rate > 1"),
    ("unique", "mart_cuisine_sla.cuisine",
     "select cuisine from mart_cuisine_sla group by cuisine having count(*) > 1"),
    ("not_null", "mart_cuisine_sla.p90_minutes",
     "select * from mart_cuisine_sla where p90_minutes is null"),
    ("unique", "mart_restaurant_performance.restaurant_id",
     "select restaurant_id from mart_restaurant_performance "
     "group by restaurant_id having count(*) > 1"),
]

# Snapshots: slowly-changing dimensions, kept in their own list because they are
# not models. A model is rebuilt from its source; a snapshot accumulates history
# the source has already thrown away, so it can never be recomputed -- drop the
# table and the history is gone for good.
SNAPSHOTS = [
    ("snap_restaurant", "snapshots/snap_restaurant.sql"),
]

_SNAPSHOT = re.compile(
    r"\{%\s*snapshot\s+\w+\s*%\}(.*?)\{%\s*endsnapshot\s*%\}", re.S)
_SOURCE = re.compile(r"\{\{\s*source\(\s*'raw'\s*,\s*'(\w+)'\s*\)\s*\}\}")
_REF = re.compile(r"\{\{\s*ref\(\s*'(\w+)'\s*\)\s*\}\}")
_VAR = re.compile(r"\{\{\s*var\(\s*'(\w+)'\s*\)\s*\}\}")
_THIS = re.compile(r"\{\{\s*this\s*\}\}")
_CONFIG = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.S)
_INCR = re.compile(r"\{%\s*if\s+is_incremental\(\)\s*%\}(.*?)\{%\s*endif\s*%\}", re.S)
_ANY_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


# What dbt_project.yml declares per folder. A model's own config() block wins
# over this, exactly as it does in dbt -- the project file sets the default and
# the model overrides it, never the other way round.
FOLDER_MATERIALISATION = {"staging": "view", "marts": "table"}


def read_config(sql: str, path: pathlib.Path) -> dict:
    """Pull the {{ config(...) }} call out of a model, as dbt does.

    Falls back to the folder default from dbt_project.yml, so a mart without a
    config block is a table and not a view. Getting that wrong is quiet: the
    models all build, every test passes, and the dashboard re-runs the whole
    aggregate on each read.
    """
    default = FOLDER_MATERIALISATION.get(path.parent.name, "view")
    match = _CONFIG.search(sql)
    if not match:
        return {"materialized": default}
    try:
        call = ast.parse(f"f({match.group(1)})", mode="eval").body
        cfg = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"cannot read config({match.group(1)}): {exc}") from None
    cfg.setdefault("materialized", default)
    return cfg


def compile_sql(path: pathlib.Path, *, name: str = "", incremental: bool = False) -> str:
    """Resolve the tags dbt would resolve, and refuse any it would not."""
    return compile_sql_text(path.read_text(encoding="utf-8"), path,
                            name=name, incremental=incremental)


def compile_sql_text(sql: str, path: pathlib.Path, *, name: str = "",
                     incremental: bool = False) -> str:
    """The same, on a string already in hand -- a snapshot's inner select."""
    sql = _CONFIG.sub("", sql)
    # The incremental block is kept only on a run that is actually incremental,
    # which is what is_incremental() means: the model is incremental, the
    # relation already exists, and this is not a full refresh.
    sql = _INCR.sub((lambda m: m.group(1)) if incremental else "", sql)
    sql = _SOURCE.sub(lambda m: m.group(1), sql)
    sql = _REF.sub(lambda m: m.group(1), sql)
    sql = _THIS.sub(name, sql)

    def var(match):
        key = match.group(1)
        if key not in VARS:
            raise KeyError(f"{path.name} uses var('{key}'), which is not declared")
        return str(VARS[key])

    sql = _VAR.sub(var, sql)
    leftover = _ANY_TAG.search(sql)
    if leftover:
        raise NotImplementedError(
            f"{path.name} uses a template tag this builder does not handle: "
            f"{leftover.group(0)[:60]}")
    return sql.strip().rstrip(";")


def relation_type(conn, name: str) -> str | None:
    rows = conn.execute(
        "SELECT table_type FROM information_schema.tables WHERE table_name = ?",
        [name]).fetchall()
    if not rows:
        return None
    return "VIEW" if "VIEW" in rows[0][0].upper() else "TABLE"


def drop_if_present(conn, name: str) -> None:
    """IF EXISTS does not protect against a type mismatch: dropping a VIEW that
    exists as a TABLE raises, so a model that changes materialisation would fail
    on its second run. The catalog decides the keyword."""
    kind = relation_type(conn, name)
    if kind:
        conn.execute(f"DROP {kind} IF EXISTS {name}")


def columns_of(conn, sql: str) -> list[str]:
    conn.execute(f"SELECT * FROM ({sql}) AS _probe LIMIT 0")
    return [d[0] for d in conn.description]


def build_model(conn, name: str, path: pathlib.Path, *, full_refresh: bool) -> str:
    cfg = read_config(path.read_text(encoding="utf-8"), path)
    mat = cfg["materialized"]

    if mat == "view":
        drop_if_present(conn, name)
        conn.execute(f"CREATE VIEW {name} AS {compile_sql(path, name=name)}")
        return "view"

    if mat == "table":
        drop_if_present(conn, name)
        conn.execute(f"CREATE TABLE {name} AS {compile_sql(path, name=name)}")
        return "table"

    if mat != "incremental":
        raise NotImplementedError(f"{name}: unknown materialisation {mat!r}")

    key = cfg.get("unique_key")
    if not key:
        raise ValueError(f"{name}: an incremental model needs a unique_key")
    strategy = cfg.get("incremental_strategy", "merge")
    if strategy != "merge":
        raise NotImplementedError(f"{name}: only the merge strategy is implemented")

    exists = relation_type(conn, name) == "TABLE"
    if full_refresh or not exists:
        drop_if_present(conn, name)
        conn.execute(f"CREATE TABLE {name} AS {compile_sql(path, name=name)}")
        return "incremental (full)"

    # The real thing: compile WITH the incremental filter, then merge the result
    # into the existing table on the unique key. Same statement Snowflake runs.
    sql = compile_sql(path, name=name, incremental=True)
    cols = columns_of(conn, sql)
    updates = ", ".join(f"{c} = s.{c}" for c in cols if c != key)
    collist = ", ".join(cols)
    srclist = ", ".join(f"s.{c}" for c in cols)
    conn.execute(f"""
        MERGE INTO {name} AS t
        USING ({sql}) AS s
          ON t.{key} = s.{key}
        WHEN MATCHED THEN UPDATE SET {updates}
        WHEN NOT MATCHED THEN INSERT ({collist}) VALUES ({srclist})
    """)
    return "incremental (merge)"


def build_snapshot(conn, name: str, path: pathlib.Path) -> str:
    """Type-2 slowly changing dimension: one row per version, with its window.

    The source export is a picture of right now. A restaurant that moved cities
    in September looks, in today's file, like it was always where it is now --
    and every September order gets attributed to the wrong city the moment a
    report joins the current dimension. The source does not lie; it has no
    memory, and nothing downstream can recover what it overwrote.

    So each version keeps its own row:

        R00042  Pune        2026-08-01 -> 2026-09-14   (closed)
        R00042  Bengaluru   2026-09-14 -> null         (current)

    One timestamp is used for the whole run, so a closed row's valid_to is
    exactly the new row's valid_from. If they differed by even a microsecond, a
    point-in-time query landing in that gap would find no row at all -- or, if
    they overlapped, two.

    What this does NOT do, and dbt does not either by default: a key that
    disappears from the source keeps its row open forever. There is no way to
    tell a deletion from a row the export happened to omit, and guessing wrong
    closes a live restaurant.
    """
    raw = path.read_text(encoding="utf-8")
    body = _SNAPSHOT.search(raw)
    if not body:
        raise ValueError(f"{path.name} has no {{% snapshot %}} block")

    cfg = read_config(raw, path)
    key = cfg.get("unique_key")
    check_cols = cfg.get("check_cols")
    if not key or not check_cols:
        raise ValueError(f"{name}: needs unique_key and check_cols")
    if cfg.get("strategy", "check") != "check":
        raise NotImplementedError(f"{name}: only the check strategy is implemented")

    inner = compile_sql_text(body.group(1), path)
    cols = columns_of(conn, inner)

    if relation_type(conn, name) != "TABLE":
        conn.execute(f"""
            CREATE TABLE {name} AS
            SELECT *,
                   current_timestamp          AS dbt_valid_from,
                   cast(NULL AS TIMESTAMP)    AS dbt_valid_to
            FROM ({inner}) s
        """)
        return "snapshot (first)"

    # One timestamp for the whole run. See the docstring.
    stamp = conn.execute("SELECT current_timestamp").fetchone()[0]
    changed = " OR ".join(f"s.{c} IS DISTINCT FROM t.{c}" for c in check_cols)

    before = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]

    conn.execute(f"""
        UPDATE {name} AS t
        SET dbt_valid_to = ?
        WHERE t.dbt_valid_to IS NULL
          AND EXISTS (SELECT 1 FROM ({inner}) s
                      WHERE s.{key} = t.{key} AND ({changed}))
    """, [stamp])

    # Anything without an open row now: either a key whose version was just
    # closed above, or a key seen for the first time. Both want a new row.
    collist = ", ".join(cols)
    conn.execute(f"""
        INSERT INTO {name} ({collist}, dbt_valid_from, dbt_valid_to)
        SELECT {", ".join(f"s.{c}" for c in cols)}, ?, NULL
        FROM ({inner}) s
        LEFT JOIN {name} t
               ON t.{key} = s.{key} AND t.dbt_valid_to IS NULL
        WHERE t.{key} IS NULL
    """, [stamp])

    after = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    return f"snapshot (+{after - before})"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--full-refresh", action="store_true",
                   help="rebuild incremental models from scratch")
    p.add_argument("--select", help="build only this model and skip the tests")
    p.add_argument("--skip-tests", action="store_true")
    args = p.parse_args(argv)

    wh = open_warehouse()
    conn = wh.connect()
    failures: list[tuple[str, str, int]] = []
    try:
        print(f"models  ({wh.flavour})")
        planned = list(MODELS)
        if relation_type(conn, "review_enriched"):
            planned += AI_MODELS
        elif not args.select:
            print(f"  {'skipped':<20} {'tag:ai':<30}  "
                  f"review_enriched does not exist yet")

        for name, relative in planned:
            if args.select and name != args.select:
                continue
            started = time.perf_counter()
            how = build_model(conn, name, DBT / relative,
                              full_refresh=args.full_refresh)
            took = time.perf_counter() - started
            rows = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            print(f"  {how:<20} {name:<30} {rows:>10,} rows  {took:6.2f}s")

        if not args.select:
            print("\nsnapshots")
            for name, relative in SNAPSHOTS:
                started = time.perf_counter()
                how = build_snapshot(conn, name, DBT / relative)
                took = time.perf_counter() - started
                rows = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                open_rows = conn.execute(
                    f"SELECT count(*) FROM {name} WHERE dbt_valid_to IS NULL"
                ).fetchone()[0]
                print(f"  {how:<20} {name:<30} {rows:>10,} rows  "
                      f"({open_rows:,} current)  {took:6.2f}s")

        if args.select or args.skip_tests:
            return 0

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
