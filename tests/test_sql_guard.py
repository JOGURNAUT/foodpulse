"""Tests for the guard on generated SQL.

A model writing SQL against a warehouse is an injection surface with a helpful
interface, and the guard is the only part of that feature worth testing: the
generation can be wrong and you get a bad answer, but the guard being wrong
means a generated statement reaches the connection.

None of these call a model. The guard takes a string and returns a string or
raises, which is exactly the shape that can be tested exhaustively and for free
-- so the attacks below are the ones a prompt-injected or simply confused model
would produce, written out rather than imagined.

What these tests do NOT prove is that the warehouse is safe. The guard is a
string check on text a model produced; the defence that actually holds is the
role it connects as having SELECT and nothing else. These catch mistakes early
and give a clear message. The grant is what makes a miss survivable.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.text_to_sql import ROW_LIMIT, Refused, guard  # noqa: E402


# ------------------------------------------------------------ what must pass

def test_a_plain_select_passes():
    out = guard("select cuisine, sla_breach_rate from mart_cuisine_sla")
    assert "mart_cuisine_sla" in out


def test_a_cte_passes_and_its_name_is_not_mistaken_for_a_table():
    """A CTE defines a name that is then selected from. Treating those as
    unexposed tables would refuse most real queries the model writes."""
    out = guard("""
        with slow as (select * from mart_cuisine_sla where sla_breach_rate > 0.2)
        select cuisine from slow order by cuisine
    """)
    assert "slow" in out


def test_a_join_between_two_marts_passes():
    guard("select o.city from fct_orders o join dim_date d "
          "on o.order_date = d.date_day")


def test_a_trailing_semicolon_is_tolerated():
    """One statement that happens to end in a semicolon is not two statements,
    and refusing it would reject most of what a model returns."""
    guard("select * from mart_data_quality;")


# --------------------------------------------------------- what must be refused

@pytest.mark.parametrize("sql, because", [
    ("drop table fct_orders", "DROP"),
    ("delete from fct_orders where 1=1", "DELETE"),
    ("update fct_orders set total_amount = 0", "UPDATE"),
    ("insert into fct_orders values (1)", "INSERT"),
    ("truncate table fct_orders", "TRUNCATE"),
    ("create table x as select 1", "CREATE"),
    ("alter table fct_orders drop column city", "ALTER"),
    ("grant all on fct_orders to public", "GRANT"),
])
def test_write_statements_are_refused(sql, because):
    with pytest.raises(Refused):
        guard(sql)


def test_a_second_statement_behind_a_semicolon_is_refused():
    """The classic. The query reads fine up to the semicolon."""
    with pytest.raises(Refused, match="more than one statement"):
        guard("select 1 from fct_orders; drop table fct_orders")


def test_a_write_hidden_in_a_comment_does_not_slip_through_either_way():
    """Comments are stripped before the check, so a DROP inside one is not a
    false positive -- and a real DROP cannot be smuggled past the check by
    putting a comment in front of it."""
    guard("select city from fct_orders -- drop table fct_orders")
    with pytest.raises(Refused):
        guard("/* harmless */ drop table fct_orders")


def test_a_table_outside_the_allowlist_is_refused():
    """An allowlist, not a denylist. A table nobody meant to expose is refused
    whether the model invented the name or a user asked for it."""
    with pytest.raises(Refused, match="not an exposed mart"):
        guard("select * from review_embedding")
    with pytest.raises(Refused, match="not an exposed mart"):
        guard("select * from information_schema.tables")


def test_a_qualified_name_cannot_smuggle_in_another_schema():
    with pytest.raises(Refused, match="not an exposed mart"):
        guard("select * from pg_catalog.pg_tables")


def test_a_join_to_a_forbidden_table_is_refused():
    """Every table reference is checked, not only the first one. A query that
    starts legitimately and joins somewhere it should not is the realistic
    shape of this mistake."""
    with pytest.raises(Refused, match="not an exposed mart"):
        guard("select * from fct_orders join raw_orders using (order_id)")


def test_something_that_is_not_a_query_at_all_is_refused():
    with pytest.raises(Refused, match="SELECT or WITH"):
        guard("I cannot answer that question.")


# ------------------------------------------------------------------ the limit

def test_a_limit_is_added_when_the_model_forgets_one():
    """Not security -- an accident. 'show me the orders' against two million
    rows is one unbounded answer away from an unresponsive page."""
    assert f"LIMIT {ROW_LIMIT}" in guard("select * from fct_orders")


def test_an_existing_limit_is_left_alone():
    out = guard("select * from fct_orders limit 5")
    assert out.lower().count("limit") == 1
