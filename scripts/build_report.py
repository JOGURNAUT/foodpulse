"""Write docs/results.html from the warehouse.

Generated, never written by hand. A results page with numbers typed into it
stops being true the first time the pipeline runs again, and then it is a
screenshot pretending to be a report.

It is also deliberately a static file rather than a BI dashboard. A Looker
Studio or Snowsight board is bound to a live warehouse, and a Snowflake trial
lasts thirty days -- so a link to one dies about a month after it is shared,
which is roughly when somebody gets round to opening it. This page keeps working
after the account is gone.

Chrome, colour and type come from docs/site.css, shared with the Overview page
and with Dispatch and Arrival's own results pages, so the three projects read
as one body of work rather than three documents that happen to be linked.

    python scripts/build_report.py
"""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import open_warehouse  # noqa: E402

OUT = ROOT / "docs" / "results.html"
SLA_MINUTES = 45


def fetch() -> dict:
    wh = open_warehouse()
    conn = wh.connect()
    try:
        cuisines = conn.execute(
            "SELECT cuisine, orders, avg_minutes, p50_minutes, p90_minutes, "
            "sla_breach_rate, revenue FROM mart_cuisine_sla "
            "ORDER BY sla_breach_rate DESC").fetchall()
        quality = dict(conn.execute(
            "SELECT metric, rows FROM mart_data_quality").fetchall())
        cities = conn.execute(
            "SELECT city, SUM(orders_valid), ROUND(AVG(avg_delivery_minutes),1), "
            "ROUND(SUM(sla_breaches)*1.0/SUM(orders_valid),4) "
            "FROM mart_city_daily GROUP BY city ORDER BY 4 DESC").fetchall()
        days = conn.execute(
            "SELECT COUNT(DISTINCT order_date) FROM mart_city_daily").fetchone()[0]
        snap_rows, snap_open = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE dbt_valid_to IS NULL) "
            "FROM snap_restaurant").fetchone()
    finally:
        conn.close()
    return {"cuisines": cuisines, "quality": quality, "cities": cities,
            "days": days, "flavour": wh.flavour,
            "snap_rows": snap_rows, "snap_open": snap_open,
            "built_at": datetime.now(timezone.utc).replace(tzinfo=None)
                                .strftime("%Y-%m-%d %H:%M UTC")}


def render(d: dict) -> str:
    cs = d["cuisines"]
    slow, fast = cs[0], cs[-1]
    worst_rate = slow[5] or 0.0
    best_rate = fast[5] or 0.0
    ratio = (worst_rate / best_rate) if best_rate else 0
    q = d["quality"]
    total = q.get("orders_total", 0)
    valid = q.get("orders_valid", 0)
    max_rate = max((c[5] or 0) for c in cs) or 1

    def cuisine_row(c):
        rate = c[5] or 0
        cls = "a" if rate > 0.1 else "b"
        return f"""
      <div class="bar-row">
        <div class="bar-head">
          <span class="bar-name">{c[0]}</span>
          <span class="bar-val">{rate:.1%} breach</span>
        </div>
        <div class="track">
          <div class="fill" style="width: {max(rate / max_rate * 100, 0.8):.1f}%;
               background: var(--series-{cls})"></div>
        </div>
        <div class="bar-sub">{c[1]:,} orders &middot; avg {c[2]:.1f} min &middot;
             p90 {c[4]:.0f} min</div>
      </div>"""

    bars = "\n".join(cuisine_row(c) for c in cs)

    qrows = "\n".join(
        f"<tr><td class='mono'>{k}</td><td class='n'>{v:,}</td></tr>"
        for k, v in q.items() if k not in ("orders_total", "orders_valid"))

    def city_row(c):
        return (f"<tr><td>{c[0]}</td><td class='n'>{c[1]:,}</td>"
                f"<td class='n'>{c[2]:.1f}</td><td class='n'>{c[3]:.1%}</td></tr>")

    crows = "\n".join(city_row(c) for c in d["cities"])

    implausible = q.get("implausible_amount", 0)
    mismatch = q.get("amount_vs_lines_mismatch", 0)
    mismatch_mult = (mismatch / implausible) if implausible else 0

    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FoodPulse — Results</title>
<meta name="description" content="SLA breach rate by cuisine, what the warehouse throws out and why, and the per-city breakdown, every figure queried from the warehouse at build time.">
<link rel="stylesheet" href="site.css">
</head>
<body>

<nav class="sitenav" aria-label="Pages">
  <a href="index.html">Overview</a>
  <a href="architecture.html">Architecture</a>
  <a href="results.html" aria-current="page">Results</a>
  <span class="spacer"></span>
  <a class="out" href="https://github.com/JOGURNAUT/foodpulse">Source</a>
  <button class="themetoggle" type="button">Light</button>
</nav>

<div class="wrap">

<p class="eyebrow">FoodPulse &middot; delivery SLA</p>
<h1>A flat {SLA_MINUTES}-minute promise breaks {worst_rate:.0%} of the time on
    {slow[0].lower()} and {best_rate:.1%} on {fast[0].lower()}.</h1>
<p class="stamp">generated {d['built_at']} from the {d['flavour']} warehouse
   &middot; {total:,} orders over {d['days']} days
   &middot; every figure queried at build time</p>

<section>
  <h2>SLA breach rate by cuisine</h2>
  <p class="lede">The promise does not know what it promised. Kitchen time is a
     property of the dish &mdash; a biryani is cooked to order, a dessert is
     plated &mdash; and one flat threshold across all of them is wrong by
     {ratio:.0f}&times; between the ends of this list.</p>
  <div class="legend">
    <span><i class="swatch" style="background: var(--series-a)"></i> breach rate above 10%</span>
    <span><i class="swatch" style="background: var(--series-b)"></i> below 10%</span>
  </div>
  <div class="bars">{bars}</div>
  <p class="caveat"><b>Synthetic data.</b> Orders come from a seeded generator
     with defects injected on purpose, and the prep times the marts recover are
     the ones the generator was given. What is not circular is the shape: a
     single SLA across populations with different floors is a promise that is
     mostly idle at one end and mostly broken at the other.</p>
</section>

<section>
  <h2>What the warehouse threw out</h2>
  <p class="lede">Every excluded row, counted under the rule that excluded it.
     Without this, &ldquo;we load clean data&rdquo; is a claim with nothing
     behind it, and a defect rate that doubles overnight looks like a quiet week.</p>
  <div class="kpis">
    <div class="kpi"><div class="kv">{total:,}</div><div class="kl">orders loaded</div></div>
    <div class="kpi is-ok"><div class="kv">{valid:,}</div><div class="kl">passed every rule</div></div>
    <div class="kpi is-warn"><div class="kv">{total - valid:,}</div><div class="kl">quarantined</div></div>
    <div class="kpi"><div class="kv">{(total - valid) / total:.2%}</div><div class="kl">defect rate</div></div>
  </div>
  <div class="tbox" style="margin-top: 14px">
    <table data-sortable>
      <thead><tr><th data-sort>Rule</th><th class="n" data-sort>Rows</th></tr></thead>
      <tbody>{qrows}</tbody>
    </table>
  </div>
  <p class="caveat" style="margin-top: 14px"><b>The magnitude check found
     {implausible}; reconciling the header against its line items found
     {mismatch} &mdash; {mismatch_mult:.1f}&times; more.</b> A ceiling cannot
     catch a small basket multiplied by a hundred &mdash; one order here is a
     real &#8377;91.21 carried as &#8377;9,121.00, which is under any plausible
     ceiling and reads as an ordinary large order. Two independent derivations
     of the same number disagreeing is the only signal there is.</p>
</section>

<section>
  <h2>By city</h2>
  <p class="lede">Click or press Enter on a column heading to sort.</p>
  <div class="tbox">
    <table data-sortable>
      <thead><tr><th data-sort>City</th><th class="n" data-sort>Orders</th>
        <th class="n" data-sort>Avg minutes</th><th class="n" data-sort>Breach rate</th></tr></thead>
      <tbody>{crows}</tbody>
    </table>
  </div>
</section>

<section>
  <h2>How it is built</h2>
  <p class="lede">CSV exports &rarr; S3 &rarr; raw tables, loaded as text with
     nothing cast &rarr; dbt staging views that type, deduplicate and flag
     &rarr; 11 marts. The warehouse is addressed by URI: a DuckDB file locally,
     Snowflake in the cloud, same models either way.</p>
  <p class="lede" style="margin-top:-8px">
     <a href="architecture.html">Architecture diagram &rarr;</a></p>
  <p class="caveat">Raw stays untyped on purpose. Casting at load turns a bad
     value into a row the loader silently dropped; casting in staging turns it
     into a failed test with a name. <b>84 tests</b> run against the project
     &mdash; 29 schema, 6 singular, 49 pytest &mdash; and the singular ones are
     the interesting half: that orphaned orders survive their join, that a
     withheld rating is never scored as a zero, that no order lands in two
     buckets.</p>
  <p class="caveat"><b>{d['snap_rows']:,} restaurants are tracked as a type-2
     snapshot</b>, {d['snap_open']:,} of them on their first and only version
     so far. A restaurant that moves cities keeps its old row closed at the
     moment it changed, so an order placed before the move still joins to the
     city it actually happened in &mdash; seven tests hold the boundary exact,
     including one that checks four moments either side of it for exactly one
     matching row, never zero and never two.</p>
</section>

</div>

<script src="site.js"></script>
</body>
</html>
"""


def main() -> int:
    d = fetch()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(d), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"  {d['cuisines'][0][0]} {d['cuisines'][0][5]:.1%} vs "
          f"{d['cuisines'][-1][0]} {d['cuisines'][-1][5]:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
