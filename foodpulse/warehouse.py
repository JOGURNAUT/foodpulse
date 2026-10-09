"""The warehouse, local or cloud, behind one interface.

`data/foodpulse.duckdb` is a file; `snowflake://ACCOUNT/DB/SCHEMA` is Snowflake.
Nothing above this layer knows which it got.

WHY DUCKDB AND NOT SQLITE

Because the thing being learned here is a columnar warehouse, and SQLite is not
one. DuckDB and Snowflake are the same shape -- columnar storage, vectorised
execution, the analytical SQL surface (QUALIFY, window frames, struct types) --
so a model written against one runs against the other with the dialect
differences that remain being small and nameable. SQLite would have meant
rewriting every mart when the cloud target arrived, and quietly learning a row
store while believing otherwise.

WHY BOTH, AND NOT JUST SNOWFLAKE

A Snowflake trial is thirty days. A project that only runs inside one is a
project that stops running a month after it is written, which is exactly when
somebody is most likely to open it. The DuckDB path keeps this clonable and
runnable by anyone, forever, with no account.

The Snowflake path is the one that matters for the architecture -- external
stage, COPY INTO, warehouses that scale independently of storage -- and the SQL
for it is in sql/snowflake/, written to be run in order.
"""

from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass
from typing import Any

SNOWFLAKE_SCHEME = "snowflake://"
DEFAULT_LOCAL = "data/foodpulse.duckdb"

RAW_TABLES = ("restaurants", "menu", "users", "orders", "order_items", "reviews")

# Loaded exactly as exported: text in, no casting, no filtering. Typing happens
# in staging, where a bad value becomes a visible failed test rather than a row
# the loader silently dropped on the way in.
RAW_SCHEMA = {
    "restaurants": "restaurant_id VARCHAR, name VARCHAR, city VARCHAR, "
                   "cuisine VARCHAR, rating VARCHAR, onboarded_at VARCHAR",
    "menu": "menu_item_id VARCHAR, restaurant_id VARCHAR, item_name VARCHAR, "
            "price VARCHAR, is_veg VARCHAR",
    "users": "user_id VARCHAR, city VARCHAR, signed_up_at VARCHAR",
    "orders": "order_id VARCHAR, user_id VARCHAR, restaurant_id VARCHAR, "
              "city VARCHAR, placed_at VARCHAR, delivered_at VARCHAR, "
              "total_amount VARCHAR, payment_method VARCHAR",
    "order_items": "order_item_id VARCHAR, order_id VARCHAR, menu_item_id VARCHAR, "
                   "quantity VARCHAR, unit_price VARCHAR",
    "reviews": "review_id VARCHAR, order_id VARCHAR, restaurant_id VARCHAR, "
               "rating VARCHAR, review_text VARCHAR, created_at VARCHAR",
}

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WarehouseError(RuntimeError):
    pass


def _check_identifier(name: str) -> str:
    """Table names are interpolated into DDL, which parameters cannot carry.

    So they are validated against a pattern instead of trusted. Every name here
    comes from RAW_TABLES and not from input, but a loader that interpolates
    unchecked identifiers is one refactor away from being injectable.
    """
    if not _IDENT.match(name):
        raise WarehouseError(f"unsafe identifier: {name!r}")
    return name


@dataclass
class DuckWarehouse:
    path: str = DEFAULT_LOCAL

    @property
    def uri(self) -> str:
        return self.path

    @property
    def flavour(self) -> str:
        return "duckdb"

    def connect(self):
        import duckdb

        p = pathlib.Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(p))

    def load_csv(self, conn, table: str, csv_path: pathlib.Path) -> int:
        """Replace a raw table from a CSV, inside one transaction.

        Replace rather than append: re-running a load has to produce the table,
        not add to it, or a second run doubles the warehouse and nothing says so.
        """
        name = _check_identifier(table)
        conn.execute("BEGIN")
        try:
            conn.execute(f"CREATE OR REPLACE TABLE raw_{name} ({RAW_SCHEMA[name]})")
            conn.execute(
                f"INSERT INTO raw_{name} SELECT * FROM read_csv(?, header=true, "
                f"all_varchar=true)", [str(csv_path)])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return conn.execute(f"SELECT COUNT(*) FROM raw_{name}").fetchone()[0]

    def query(self, conn, sql: str, params: tuple = ()) -> list[tuple]:
        return conn.execute(sql, list(params)).fetchall()


@dataclass
class SnowflakeWarehouse:
    """Written, not exercised here.

    The connection and the COPY INTO are real; what this project cannot prove is
    that a given account has them configured. sql/snowflake/ holds the setup --
    storage integration, external stage, file formats, raw tables -- in the
    order it has to run, and the README says plainly which parts have been run
    and which have not.
    """

    account: str
    database: str
    schema: str
    warehouse: str = "COMPUTE_WH"

    @property
    def uri(self) -> str:
        return f"{SNOWFLAKE_SCHEME}{self.account}/{self.database}/{self.schema}"

    @property
    def flavour(self) -> str:
        return "snowflake"

    def connect(self):
        try:
            import snowflake.connector
        except ImportError as exc:
            raise WarehouseError(
                "snowflake-connector-python is not installed. `pip install "
                "snowflake-connector-python`, or point at the local DuckDB file."
            ) from exc
        password = os.environ.get("SNOWFLAKE_PASSWORD")
        user = os.environ.get("SNOWFLAKE_USER")
        if not (user and password):
            raise WarehouseError(
                "set $SNOWFLAKE_USER and $SNOWFLAKE_PASSWORD. Credentials are "
                "read from the environment and never taken as arguments: a "
                "command line is visible in `ps` and in shell history.")
        return snowflake.connector.connect(
            account=self.account, user=user, password=password,
            database=self.database, schema=self.schema, warehouse=self.warehouse)

    def load_csv(self, conn, table: str, csv_path: pathlib.Path) -> int:
        """PUT to the table stage, then COPY INTO.

        Not row-by-row inserts: Snowflake bills compute by the second and an
        INSERT per row spends all of it on round trips. PUT uploads the file
        once and COPY INTO loads it in bulk, which is the only sensible way in
        and the reason the external-stage SQL exists at all.
        """
        name = _check_identifier(table)
        cur = conn.cursor()
        cur.execute(f"CREATE OR REPLACE TABLE RAW_{name.upper()} ({RAW_SCHEMA[name]})")
        cur.execute(f"PUT file://{csv_path.as_posix()} @%RAW_{name.upper()} "
                    f"OVERWRITE = TRUE")
        cur.execute(
            f"COPY INTO RAW_{name.upper()} FROM @%RAW_{name.upper()} "
            f"FILE_FORMAT = (TYPE = CSV SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '\"') "
            f"ON_ERROR = ABORT_STATEMENT")
        cur.execute(f"SELECT COUNT(*) FROM RAW_{name.upper()}")
        return cur.fetchone()[0]

    def query(self, conn, sql: str, params: tuple = ()) -> list[tuple]:
        cur = conn.cursor()
        cur.execute(sql, params or None)
        return cur.fetchall()


Warehouse = DuckWarehouse | SnowflakeWarehouse


def open_warehouse(uri: str | None = None) -> Warehouse:
    """A warehouse from a URI, scheme first.

    The argument that names the target also picks the driver, so a deployment
    cannot be configured with a Snowflake account and a local-mode flag that
    disagree.
    """
    uri = uri or os.environ.get("FOODPULSE_WAREHOUSE") or DEFAULT_LOCAL
    if uri.startswith(SNOWFLAKE_SCHEME):
        rest = uri[len(SNOWFLAKE_SCHEME):].strip("/").split("/")
        if len(rest) < 3:
            raise WarehouseError(
                f"{uri!r} should be snowflake://ACCOUNT/DATABASE/SCHEMA")
        return SnowflakeWarehouse(account=rest[0], database=rest[1], schema=rest[2])
    return DuckWarehouse(path=uri)


def load_all(raw_dir: str | pathlib.Path = "data/raw",
             uri: str | None = None) -> dict[str, Any]:
    wh = open_warehouse(uri)
    raw = pathlib.Path(raw_dir)
    counts: dict[str, int] = {}
    conn = wh.connect()
    try:
        for table in RAW_TABLES:
            path = raw / f"{table}.csv"
            if not path.exists():
                raise WarehouseError(
                    f"no {path} - run `python -m foodpulse.generate` first")
            counts[table] = wh.load_csv(conn, table, path.resolve())
    finally:
        conn.close()
    return {"warehouse": wh.uri, "flavour": wh.flavour, "counts": counts}
