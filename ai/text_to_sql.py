"""Ask the warehouse a question in English. Streamlit, or the command line.

    streamlit run ai/text_to_sql.py
    python ai/text_to_sql.py "which cuisine breaches the SLA most"

The model is given the marts' column names and asked to write one SELECT. It
never sees the data -- only the schema -- so the rows stay in the warehouse.

**The guard is the point of this file, not the SQL generation.**

A model writing SQL against a warehouse is an injection surface with a helpful
interface. Three things stand between it and the data, and no single one of them
is enough:

1. The statement must parse as exactly one SELECT or WITH. Anything else is
   refused before it reaches the connection -- no DROP, no DELETE, no UPDATE, no
   INSERT, and no `SELECT 1; DROP TABLE orders` smuggled in behind a semicolon.

2. Every table it names must be one of the marts listed here. The model cannot
   be talked into reading a table nobody meant to expose, because a name that is
   not on the list is refused whether the model invented it or a user asked for
   it by name.

3. It runs as a role with SELECT and nothing else. This is the one that actually
   matters: 1 and 2 are string checks on text a model produced, and the only
   defence worth relying on is the database refusing the write. The first two
   catch mistakes early and give a clear message; the role is what makes a miss
   survivable.

A prompt that says "only write SELECT statements" is not a guard. It is a
request, to a system whose entire behaviour is to be talked into things.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import open_warehouse  # noqa: E402

MODEL = os.environ.get("SQL_MODEL", "gpt-4o-mini")
ROW_LIMIT = 200

# The only tables a generated query may touch.
ALLOWED = {
    "fct_orders", "fct_order_items", "dim_restaurant", "dim_user",
    "dim_menu_item", "dim_date", "mart_city_daily", "mart_cuisine_sla",
    "mart_restaurant_performance", "mart_data_quality", "mart_review_insights",
}

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|merge|grant|revoke|"
    r"copy|attach|install|load|export|pragma|call)\b", re.I)

# Tables after FROM or JOIN. Deliberately simple: anything it cannot read, it
# refuses rather than guesses at.
TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.I)


class Refused(ValueError):
    """The generated SQL did not pass the guard."""


def guard(sql: str) -> str:
    """Return the statement if it is safe to run, or raise Refused."""
    cleaned = re.sub(r"--[^\n]*", " ", sql)
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.S).strip().rstrip(";")

    if ";" in cleaned:
        raise Refused("more than one statement")
    if not re.match(r"^\s*(select|with)\b", cleaned, re.I):
        raise Refused("does not begin with SELECT or WITH")
    hit = FORBIDDEN.search(cleaned)
    if hit:
        raise Refused(f"contains {hit.group(1).upper()}")

    # A CTE defines a name that is then selected from, so those names are
    # legitimate even though they are not marts.
    ctes = {m.lower() for m in re.findall(r"(\w+)\s+as\s*\(", cleaned, re.I)}
    for ref in TABLE_REF.findall(cleaned):
        name = ref.split(".")[-1].lower()
        if name not in ALLOWED and name not in ctes:
            raise Refused(f"reads {ref}, which is not an exposed mart")

    if not re.search(r"\blimit\b", cleaned, re.I):
        cleaned = f"{cleaned}\nLIMIT {ROW_LIMIT}"
    return cleaned


def schema_text(conn) -> str:
    lines = []
    for table in sorted(ALLOWED):
        try:
            conn.execute(f"SELECT * FROM {table} LIMIT 0")
        except Exception:
            continue          # the AI marts do not exist until tag:ai is built
        cols = ", ".join(d[0] for d in conn.description)
        lines.append(f"{table}({cols})")
    return "\n".join(lines)


def to_sql(client, question: str, schema: str) -> str:
    prompt = (
        "You write one SQL SELECT for a DuckDB/Snowflake warehouse.\n"
        "Only these tables exist:\n\n" + schema + "\n\n"
        "Rules: one statement, SELECT or WITH only, no semicolon, no DDL or DML.\n"
        "Prefer the mart tables over recomputing from fct_orders.\n"
        "Return only SQL, with no fences and no prose."
    )
    response = client.chat.completions.create(
        model=MODEL, temperature=0,
        messages=[{"role": "system", "content": prompt},
                  {"role": "user", "content": question}],
    )
    sql = response.choices[0].message.content.strip()
    return re.sub(r"^```(?:sql)?|```$", "", sql, flags=re.M).strip()


def answer(question: str):
    """Returns (sql, columns, rows). Raises Refused if the guard rejects it."""
    from openai import OpenAI

    wh = open_warehouse()
    conn = wh.connect()
    try:
        sql = guard(to_sql(OpenAI(), question, schema_text(conn)))
        rows = conn.execute(sql).fetchall()
        cols = [d[0] for d in conn.description]
        return sql, cols, rows
    finally:
        conn.close()


def cli(question: str) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 1
    try:
        sql, cols, rows = answer(question)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(sql, "\n")
    print(" | ".join(cols))
    for row in rows[:40]:
        print(" | ".join("" if v is None else str(v) for v in row))
    return 0


def app() -> None:
    import streamlit as st

    st.set_page_config(page_title="FoodPulse — ask the warehouse", page_icon="🍜")
    st.title("Ask the warehouse")
    st.caption("The model sees column names, never rows. Every generated "
               "statement passes a guard before it reaches the connection.")

    question = st.text_input("Question",
                             placeholder="which cuisine breaches the SLA most?")
    if not question:
        return
    if not os.environ.get("OPENAI_API_KEY"):
        st.error("OPENAI_API_KEY is not set")
        return

    try:
        sql, cols, rows = answer(question)
    except Refused as exc:
        st.error(f"Refused: {exc}")
        st.caption("The guard rejected the generated statement. Nothing ran.")
        return

    st.code(sql, language="sql")
    if rows:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows, columns=cols), width="stretch")
    else:
        st.info("The query ran and returned no rows.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli(" ".join(sys.argv[1:])))
    try:
        app()
    except ImportError:
        print(__doc__)
        print("streamlit is not installed, and no question was given.",
              file=sys.stderr)
        sys.exit(1)
