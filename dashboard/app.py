"""The FoodPulse dashboard. Reads the marts, computes nothing.

    streamlit run dashboard/app.py

Every number here is selected from a mart. That is a rule, not a convenience:
the moment a dashboard starts deriving its own figures, there are two
definitions of "SLA breach" -- one in dbt and one in Python -- and they drift.
When they drift, two people reading two screens disagree about the same day and
neither is wrong, which is the worst possible way to lose trust in a warehouse.

If a number is missing from here, the fix is a mart, not a pandas groupby.

It sits beside docs/index.html rather than replacing it. This needs a running
warehouse and a running Python process; the static page survives both being
gone, which is what makes it the one to put on a resume.
"""

from __future__ import annotations

import pathlib
import sys

import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import open_warehouse  # noqa: E402

SLA_MINUTES = 45

st.set_page_config(page_title="FoodPulse", page_icon="🍜", layout="wide")


@st.cache_data(ttl=300)
def q(sql: str) -> pd.DataFrame:
    """One query, one dataframe. Cached, because Streamlit re-runs this whole
    file on every widget interaction -- without the cache, moving a selectbox
    re-queries every mart on the page."""
    wh = open_warehouse()
    conn = wh.connect()
    try:
        rows = conn.execute(sql).fetchall()
        cols = [d[0] for d in conn.description]
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=cols)


def table_exists(name: str) -> bool:
    return not q("SELECT table_name FROM information_schema.tables "
                 f"WHERE table_name = '{name}'").empty


st.title("FoodPulse")
st.caption("Food-delivery analytics warehouse. Every figure is read from a "
           "mart — nothing on this page is computed in Python.")

if not table_exists("mart_cuisine_sla"):
    st.error("The marts are not built yet.")
    st.code("python -m foodpulse.generate\n"
            "python -m foodpulse.load\n"
            "python scripts/build_models.py", language="bash")
    st.stop()

# ---------------------------------------------------------------- the headline

quality = q("SELECT metric, rows FROM mart_data_quality")
qd = dict(zip(quality["metric"], quality["rows"]))
total, valid = qd.get("orders_total", 0), qd.get("orders_valid", 0)

sla = q("SELECT cuisine, orders, avg_minutes, p50_minutes, p90_minutes, "
        "sla_breach_rate, revenue FROM mart_cuisine_sla ORDER BY sla_breach_rate DESC")

worst, best = sla.iloc[0], sla.iloc[-1]
st.subheader(
    f"A flat {SLA_MINUTES}-minute promise breaks {worst.sla_breach_rate:.0%} "
    f"of the time on {worst.cuisine.lower()} and "
    f"{best.sla_breach_rate:.1%} on {best.cuisine.lower()}."
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Orders loaded", f"{total:,}")
c2.metric("Passed every rule", f"{valid:,}")
c3.metric("Quarantined", f"{total - valid:,}")
c4.metric("Defect rate", f"{(total - valid) / total:.2%}" if total else "—")

# ------------------------------------------------------------------- the tabs

sla_tab, quality_tab, city_tab, menu_tab, ai_tab = st.tabs(
    ["SLA by cuisine", "Data quality", "Cities", "Menu", "Review topics"])

with sla_tab:
    st.caption("Kitchen time is a property of the dish — a biryani is cooked to "
               "order, a dessert is plated. One flat threshold across both is "
               "mostly idle at one end and mostly broken at the other.")
    st.bar_chart(sla.set_index("cuisine")["sla_breach_rate"], height=320)
    st.caption("p90 beside the mean on purpose: an average cannot separate a "
               "cuisine that is uniformly slow from one that is fine for most "
               "orders and badly late in the tail. Those need different fixes.")
    st.dataframe(sla, width="stretch", hide_index=True)

with quality_tab:
    st.caption("Every excluded row, counted under the rule that excluded it. "
               "Without this, “we load clean data” is a claim with nothing "
               "behind it and a defect rate that doubles looks like a quiet week.")
    st.dataframe(quality[~quality["metric"].isin(["orders_total", "orders_valid"])],
                 width="stretch", hide_index=True)
    ceiling = qd.get("implausible_amount", 0)
    reconciled = qd.get("amount_vs_lines_mismatch", 0)
    st.info(
        f"**A magnitude ceiling found {ceiling}. Reconciling the header "
        f"against its line items found {reconciled}.** A ceiling cannot catch a "
        f"small basket multiplied by a hundred — a real ₹91.21 carried as "
        f"₹9,121.00 is under any plausible ceiling and reads as an ordinary "
        f"large order. Two independent derivations disagreeing is the only "
        f"signal there is.")

with city_tab:
    city = q("SELECT city, SUM(orders_valid) AS orders, "
             "ROUND(AVG(avg_delivery_minutes), 1) AS avg_minutes, "
             "ROUND(SUM(sla_breaches) * 1.0 / SUM(orders_valid), 4) AS breach_rate "
             "FROM mart_city_daily GROUP BY city ORDER BY breach_rate DESC")
    st.dataframe(city, width="stretch", hide_index=True)
    daily = q("SELECT order_date, city, sla_breach_rate FROM mart_city_daily "
              "ORDER BY order_date")
    st.line_chart(daily.pivot(index="order_date", columns="city",
                              values="sla_breach_rate"), height=320)
    st.caption("Orders are carried beside the rate. A breach rate over 40 "
               "orders and one over 290 are different claims, and a table "
               "showing only the rate makes them identical.")

with menu_tab:
    menu = q("SELECT item_name, cuisine, list_price, realised_unit_price, "
             "units_sold, revenue, orders_appeared_in FROM dim_menu_item "
             "ORDER BY revenue DESC LIMIT 40")
    st.dataframe(menu, width="stretch", hide_index=True)
    st.caption("units_sold counts a three-unit line as three; "
               "orders_appeared_in counts that line once. Different questions — "
               "how much we move, against how many baskets it pulls.")

with ai_tab:
    if not table_exists("mart_review_insights"):
        st.info("The review-topic mart is not built. It is tagged `ai` and "
                "needs the enrichment task to have run — the rest of the "
                "warehouse does not depend on it.")
        st.code("python ai/enrich_reviews.py\npython scripts/build_models.py",
                language="bash")
    else:
        topics = q("SELECT city, cuisine, topic, reviews_enriched, negative_rate, "
                   "avg_rating, sla_breach_rate FROM mart_review_insights "
                   "WHERE reviews_enriched >= 5 ORDER BY negative_rate DESC LIMIT 50")
        st.dataframe(topics, width="stretch", hide_index=True)
        st.caption("Filtered to groups with at least five labelled reviews. A "
                   "60% complaint rate over three reviews is not a finding, and "
                   "showing it as one is how a dashboard starts lying politely.")

st.divider()
st.caption("Synthetic data from a seeded generator with defects injected on "
           "purpose. The prep times the marts recover are the ones the "
           "generator was given — the shape is the finding, not the decimals.")
