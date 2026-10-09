# FoodPulse

A food-delivery analytics warehouse: CSV exports → raw tables → dbt staging →
marts, with **42 tests** and a generated results page.

**[Live results →](https://jogurnaut.github.io/foodpulse/)** — generated from the warehouse on every run, never typed in.

```
A flat 45-minute delivery promise breaks 37% of the time on biryani
and 0.4% on desserts.
```

That is the finding, and it is not about biryani. Kitchen time is a property of
the dish — one is cooked to order, one is plated — and a single SLA across both
is a promise that is mostly idle at one end and mostly broken at the other.

## Run it

```bash
python -m foodpulse.generate        # 40,000 orders, seeded, defects injected
python -m foodpulse.load            # into DuckDB
python scripts/build_models.py      # build the models, run all 28 model tests
python scripts/build_report.py      # write docs/index.html
python -m pytest tests/ -q          # 14 tests on the generator and loader
```

Nothing to sign up for. DuckDB is a file.

## Honest scope

This started from a tutorial build — [Darshil Parmar's Zomato
walkthrough](https://github.com/darshilparmar/zomato-ai-data-engineering-end-to-end-project)
— for the Snowflake and dbt architecture: external stage, `COPY INTO`,
staging/marts layering. The shape of `sql/snowflake/` follows that.

What is mine, and what the walkthrough does not have:

| | |
|---|---|
| **42 tests** | 22 schema, 6 singular, 14 pytest. The walkthrough has 16 schema tests and an empty `tests/` directory, so nothing singular |
| **A local target** | DuckDB, so this runs from a clone with no account |
| **A defect generator** | seeded, with six failure classes injected on purpose |
| **A data-quality mart** | every excluded row counted under the rule that excluded it |
| **A generated results page** | static, so it outlives the trial that produced it |

**What this does not do, and the walkthrough does.** No S3 lake — the CSVs load
straight in. No Airflow DAG. No incremental models: every mart is a full
rebuild, where the original uses `materialized='incremental'` with a MERGE so a
re-run touches only new rows. No AI layer and no Streamlit. It is also 40,000
orders against the original's 10 million, which is a different problem.

**What has run and what has not.** The DuckDB path runs end to end — that is
where the numbers on the results page come from. The Snowflake SQL in
`sql/snowflake/` is written and ordered but has not been executed against an
account. The README says so rather than implying otherwise.

## Why DuckDB locally, Snowflake in the cloud

The warehouse is addressed by URI: `data/foodpulse.duckdb` is a file,
`snowflake://ACCOUNT/DB/SCHEMA` is Snowflake, and nothing above
`foodpulse/warehouse.py` knows which it got.

DuckDB rather than SQLite because the thing being learned is a **columnar**
warehouse, and SQLite is not one. DuckDB and Snowflake are the same shape —
columnar storage, vectorised execution, the analytical SQL surface — so a model
written against one runs against the other. SQLite would have meant rewriting
every mart when the cloud target arrived, while quietly learning a row store.

Both paths rather than Snowflake alone because **a trial is thirty days**. A
project that only runs inside one stops running a month after it is written,
which is roughly when somebody opens it.

## What the warehouse throws out

```
orders loaded              40,000
passed every rule          39,826
quarantined                   174      0.43%

negative_duration              86      delivered before it was ordered
negative_amount                55      a refund written into the orders table
implausible_amount             32      caught by a magnitude ceiling
amount_vs_lines_mismatch       88      caught by reconciling header to lines
orphaned_restaurant           171      restaurant deleted between two exports
reviews_rating_withheld     1,326      text written, stars skipped — not missing data
```

**The magnitude check found 32. Reconciliation found 88.**

That gap is the most useful thing here. A ceiling cannot catch a small basket
multiplied by a hundred: one order is a real ₹91.21 carried as ₹9,121.00, which
is under any plausible ceiling and reads as an ordinary large order. Two
independent derivations of the same number disagreeing is the only signal there
is — which is why `assert_revenue_reconciles_to_line_items` exists, and why the
flag is computed in `fct_orders` rather than staging: staging cannot see the
line items.

## Raw stays untyped on purpose

Every raw column is `VARCHAR`. Casting at load means a bad value either fails the
whole `COPY`, or — with `ON_ERROR = CONTINUE` — is silently skipped and the
counts quietly disagree with the source.

Casting in staging moves that decision somewhere visible: a bad value becomes a
failed test with a name, beside the rule it broke.

## The tests

**22 schema tests** — uniqueness on every grain, not-null on every key,
referential integrity across five relationships, accepted ranges on ratings and
quantities.

**6 singular tests**, which are the interesting half:

| | |
|---|---|
| `assert_orphans_survive_the_join` | if that LEFT JOIN is ever "tidied" to INNER, 171 orders vanish, revenue drops by their value, and every schema test still passes |
| `assert_revenue_reconciles_to_line_items` | the header and the sum of its lines, derived independently |
| `assert_withheld_ratings_are_not_scored_as_zero` | the tempting bug is `coalesce(rating, 0)`, which turns "declined to rate" into one star below the worst possible, across 8% of reviews |
| `assert_no_order_counted_twice` | the export's overlapping pages put the same order in the file twice; if dedupe breaks, totals look plausible and are ~1% high |
| `assert_sla_rule_is_applied_once` | the threshold lives in one `var`; a mart re-deriving it with its own number is how two dashboards disagree about the same day |
| `assert_rate_has_a_denominator` | a rate over zero orders is a division nobody noticed |

**14 pytest** on the Python layer, covering what SQL cannot: that the generator
still injects the defects the models are written to catch. A generator that
quietly stopped would leave every dbt test passing over clean data.

## The data is generated

Seeded, so the same seed gives the same rows and nothing large lives in git. Six
defect classes are injected at tunable rates, because a clean dataset cannot
exercise a quality check.

```bash
python -m foodpulse.generate --orphan-rate 0.05 --currency-rate 0.02
```

So the per-cuisine finding is a **demonstration, not a measurement** — the prep
times the marts recover are the ones the generator was given. What is not
circular is the shape: a single threshold across populations with different
floors is wrong at both ends, and a test asserts that ordering survives a change
of seed.

## Layout

```
foodpulse/
  generate.py          synthetic orders, restaurants, users, reviews + defects
  warehouse.py         DuckDB or Snowflake, chosen by URI
  load.py              raw CSVs into the warehouse
sql/snowflake/         setup, storage integration, stage, raw tables, COPY INTO
dbt_foodpulse/
  models/staging/      typed, deduplicated, flagged
  models/marts/        fct_orders, dim_restaurant, city daily, cuisine SLA, quality
  tests/               6 singular tests
scripts/               build the models and the report
tests/                 14 pytest
docs/index.html      generated
```
