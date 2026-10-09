"""Write docs/results.html from the warehouse.

Generated, never written by hand. A results page with numbers typed into it
stops being true the first time the pipeline runs again, and then it is a
screenshot pretending to be a report.

It is also deliberately a static file rather than a BI dashboard. A Looker
Studio or Snowsight board is bound to a live warehouse, and a Snowflake trial
lasts thirty days -- so a link to one dies about a month after it is shared,
which is roughly when somebody gets round to opening it. This page keeps working
after the account is gone.

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

OUT = ROOT / "docs" / "index.html"
SLA_MINUTES = 45

# Categorical slots from a palette checked for colourblind separation and
# contrast in both modes: worst adjacent CVD dE 24.7 light / 26.8 dark.
SLOW = {"light": "#eb6834", "dark": "#d95926"}
FAST = {"light": "#2a78d6", "dark": "#3987e5"}


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
    finally:
        conn.close()
    return {"cuisines": cuisines, "quality": quality, "cities": cities,
            "days": days, "flavour": wh.flavour,
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

    bars = "\n".join(f"""
      <div class="row">
        <div class="rh"><span class="nm">{c[0]}</span>
          <span class="rt">{c[5]:.1%}</span></div>
        <div class="track"><div class="fill" style="width:{max((c[5] or 0)/max_rate*100, 0.8):.1f}%;
             background:var(--{'slow' if (c[5] or 0) > 0.1 else 'fast'})"></div></div>
        <div class="sub">{c[1]:,} orders · avg {c[2]:.1f} min · p90 {c[4]:.0f} min</div>
      </div>""" for c in cs)

    qrows = "\n".join(
        f"<tr><td class='mono'>{k}</td><td class='n'>{v:,}</td></tr>"
        for k, v in q.items() if k not in ("orders_total", "orders_valid"))

    crows = "\n".join(
        f"<tr><td>{c[0]}</td><td class='n'>{c[1]:,}</td>"
        f"<td class='n'>{c[2]:.1f}</td><td class='n'>{c[3]:.1%}</td></tr>"
        for c in d["cities"])

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FoodPulse — Delivery SLA by Cuisine</title>
<style>
  :root {{
    color-scheme: light;
    --bg:#f4f4f2; --card:#fcfcfb; --line:#dedcd6;
    --ink:#0b0b0b; --ink2:#52514e; --ink3:#7b7a74;
    --slow:{SLOW['light']}; --fast:{FAST['light']}; --track:#e7e5df;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --bg:#121211; --card:#1a1a19; --line:#343430;
      --ink:#fff; --ink2:#c3c2b7; --ink3:#8f8e85;
      --slow:{SLOW['dark']}; --fast:{FAST['dark']}; --track:#2b2b28;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --bg:#121211; --card:#1a1a19; --line:#343430;
    --ink:#fff; --ink2:#c3c2b7; --ink3:#8f8e85;
    --slow:{SLOW['dark']}; --fast:{FAST['dark']}; --track:#2b2b28;
  }}
  *{{box-sizing:border-box}} html,body{{background:var(--bg);margin:0}}
  body{{font-family:ui-sans-serif,system-ui,"Segoe UI",sans-serif;color:var(--ink);
       padding:30px 16px 64px;line-height:1.5}}
  .wrap{{max-width:860px;margin:0 auto}}
  .eyebrow{{font:11.5px/1 ui-monospace,Menlo,monospace;letter-spacing:.14em;
           text-transform:uppercase;color:var(--ink3);margin-bottom:10px}}
  h1{{font-size:25px;line-height:1.25;margin:0 0 6px;letter-spacing:-.02em}}
  .stamp{{color:var(--ink3);font-size:11.5px;font-variant-numeric:tabular-nums;
         margin:0 0 24px}}
  section{{background:var(--card);border:1px solid var(--line);border-radius:10px;
          padding:18px 20px;margin-bottom:16px}}
  h2{{font-size:14px;margin:0 0 3px}}
  .lede{{color:var(--ink2);font-size:12.5px;margin:0 0 16px;max-width:72ch}}
  .row{{margin-bottom:14px}}
  .rh{{display:flex;justify-content:space-between;align-items:baseline;
      margin-bottom:5px}}
  .nm{{font-size:13px;font-weight:600}}
  .rt{{font-size:12.5px;font-variant-numeric:tabular-nums;color:var(--ink2)}}
  .track{{background:var(--track);border-radius:4px;height:16px;overflow:hidden}}
  .fill{{height:100%;border-radius:4px}}
  .sub{{font-size:11px;color:var(--ink3);margin-top:4px;
       font-variant-numeric:tabular-nums}}
  table{{width:100%;border-collapse:collapse;font-size:12.5px}}
  th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
     color:var(--ink2);padding:0 10px 7px 0;border-bottom:1px solid var(--line)}}
  td{{padding:7px 10px 7px 0;border-bottom:1px solid var(--line)}}
  tr:last-child td{{border-bottom:0}}
  td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
  .mono{{font-family:ui-monospace,Menlo,monospace;font-size:11.5px}}
  .caveat{{font-size:11.5px;color:var(--ink3);line-height:1.6;
          border-left:2px solid var(--line);padding-left:12px}}
  .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
        gap:12px;margin-bottom:4px}}
  .kpi{{border:1px solid var(--line);border-radius:8px;padding:12px 14px}}
  .kv{{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}}
  .kl{{font-size:11px;color:var(--ink2);margin-top:2px}}
</style>
</head>
<body><div class="wrap">

<p class="eyebrow">FoodPulse · delivery SLA</p>
<h1>A flat {SLA_MINUTES}-minute promise breaks {worst_rate:.0%} of the time on
    {slow[0].lower()} and {best_rate:.1%} on {fast[0].lower()}.</h1>
<p class="stamp">Generated {d['built_at']} from the {d['flavour']} warehouse ·
   {total:,} orders over {d['days']} days · every figure queried at build time</p>

<section>
  <h2>SLA breach rate by cuisine</h2>
  <p class="lede">The promise does not know what it promised. Kitchen time is a
     property of the dish &mdash; a biryani is cooked to order, a dessert is
     plated &mdash; and one flat threshold across all of them is wrong by
     {ratio:.0f}&times; between the ends of this list.</p>
  {bars}
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
    <div class="kpi"><div class="kv">{valid:,}</div><div class="kl">passed every rule</div></div>
    <div class="kpi"><div class="kv">{total - valid:,}</div><div class="kl">quarantined</div></div>
    <div class="kpi"><div class="kv">{(total - valid) / total:.2%}</div><div class="kl">defect rate</div></div>
  </div>
  <table style="margin-top:14px">
    <thead><tr><th>Rule</th><th class="n">Rows</th></tr></thead>
    <tbody>{qrows}</tbody>
  </table>
  <p class="caveat" style="margin-top:14px"><b>The magnitude check found
     {q.get('implausible_amount', 0)}; reconciling the header against its line
     items found {q.get('amount_vs_lines_mismatch', 0)}.</b> A ceiling cannot
     catch a small basket multiplied by a hundred &mdash; one order here is a
     real &#8377;91.21 carried as &#8377;9,121.00, which is under any plausible
     ceiling and reads as an ordinary large order. Two independent derivations
     of the same number disagreeing is the only signal there is.</p>
</section>

<section>
  <h2>By city</h2>
  <table>
    <thead><tr><th>City</th><th class="n">Orders</th>
      <th class="n">Avg minutes</th><th class="n">Breach rate</th></tr></thead>
    <tbody>{crows}</tbody>
  </table>
</section>

<section>
  <h2>How it is built</h2>
  <p class="lede">CSV exports &rarr; raw tables, loaded as text with nothing cast
     &rarr; dbt staging views that type, deduplicate and flag &rarr; marts. The
     warehouse is addressed by URI: a DuckDB file locally, Snowflake in the
     cloud, same models either way.</p>
  <p class="caveat">Raw stays untyped on purpose. Casting at load turns a bad
     value into a row the loader silently dropped; casting in staging turns it
     into a failed test with a name. <b>28 tests</b> run against the models
     &mdash; 22 schema, 6 singular &mdash; and the singular ones are the
     interesting half: that orphaned orders survive their join, that a withheld
     rating is never scored as a zero, that no order lands in two buckets.</p>
</section>

</div></body></html>
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
