# FoodPulse

A food-delivery analytics warehouse: **S3 → Snowflake RAW → dbt → marts → AI**,
orchestrated by Airflow, with **84 tests** and a generated results page.

**[Live results →](https://jogurnaut.github.io/foodpulse/)** — generated from the
warehouse on every run, never typed in.

```
A flat 45-minute delivery promise breaks 37% of the time on biryani
and 0.4% on desserts.
```

That is the finding, and it is not about biryani. Kitchen time is a property of
the dish — one is cooked to order, one is plated — and a single SLA across both
is a promise that is mostly idle at one end and mostly broken at the other.

## Run it

No account needed. DuckDB is a file, and the whole pipeline runs against it.

```bash
pip install -r requirements.txt

python -m foodpulse.generate       # 2,000,000 orders, seeded, defects injected
python -m foodpulse.load           # into DuckDB
python scripts/build_models.py     # 17 models, then 35 model tests
python scripts/build_report.py     # write docs/index.html
python -m pytest tests/ -q         # 49 tests on the Python layer

streamlit run dashboard/app.py     # optional, live dashboard
```

The cloud path is the same models against Snowflake:

```bash
python scripts/upload_to_s3.py --bucket <your-bucket>   # CSVs to the lake
# then sql/snowflake/01-05 in Snowsight, with aws/iam/ for the AWS side
cd airflow && cp example.env .env && docker compose up -d   # localhost:8088
```

## Honest scope

This started from a tutorial build — [Darshil Parmar's Zomato
walkthrough](https://github.com/darshilparmar/zomato-ai-data-engineering-end-to-end-project)
— and follows its architecture: S3 lake, keyless storage integration, `COPY
INTO`, dbt medallion layering, an Airflow DAG, and an AI layer on top.

**What has run and what has not.** The DuckDB path runs end to end, and every
number on the results page comes from it. The Snowflake SQL and the Airflow
stack are written and ordered but have not been run against a live account; the
AI scripts need an `OPENAI_API_KEY` that this repo does not ship. Said plainly
here rather than listed as a technology and left to be assumed.

What is mine, and what the walkthrough does not have:

| | |
|---|---|
| **84 tests** | 29 schema, 6 singular, 49 pytest. The walkthrough has 16 schema tests and an empty `tests/` directory, so nothing singular |
| **A local target** | DuckDB, so this runs from a clone with no account at all |
| **A defect generator** | seeded, six failure classes injected at tunable rates |
| **A data-quality mart** | every excluded row counted under the rule that excluded it |
| **Tests on the merge** | including one that asserts a late-arriving order survives, and one that shows what the naive filter would have lost |
| **Tests on the SQL guard** | 20 of them, none calling a model |
| **A generated results page** | static, so it outlives the trial that produced it |

## Incremental, and the window that is easy to get wrong

`fct_orders` and `fct_order_items` are `materialized='incremental'` with a MERGE
on their own grain. At two million orders a full rebuild is wasted work on every
run but the first — yesterday's orders do not change.

The obvious incremental filter is *rows newer than the newest row I already
have*. It quietly loses data. An order placed at 23:58 that reaches the export
at 00:04 arrives **after** a later-timestamped order has already pushed the
watermark past it, so it is never selected again. The run succeeds, every schema
test passes, the row simply is not there.

So the filter reprocesses a trailing window instead, and because the strategy is
MERGE on the key, rows that come back unchanged are updated in place rather than
inserted twice. The window costs something — three days of orders every run —
and that is the trade being made deliberately.

Two tests hold this down: `test_a_late_arriving_order_is_still_picked_up`, and
`test_the_naive_watermark_would_have_lost_it` beside it, which asserts the
premise instead of claiming it in a comment. If someone later "simplifies" the
model by dropping the interval, the second test says what that costs.

```
                        full rebuild    incremental merge
fct_orders                    3.14s                 2.01s
fct_order_items               2.95s                 0.97s
```

## What the warehouse throws out

```
orders loaded            2,000,000
passed every rule        1,990,975
quarantined                  9,025      0.45%

negative_duration            4,051      delivered before it was ordered
negative_amount              3,025      a refund written into the orders table
implausible_amount           1,946      caught by a magnitude ceiling
amount_vs_lines_mismatch     4,986      caught by reconciling header to lines
orphaned_restaurant          8,041      restaurant deleted between two exports
reviews_rating_withheld     67,289      text written, stars skipped
```

**The magnitude check found 1,946. Reconciliation found 4,986.**

That gap is the most useful thing here. A ceiling cannot catch a small basket
multiplied by a hundred: a real ₹91.21 carried as ₹9,121.00 is under any
plausible ceiling and reads as an ordinary large order. Two independent
derivations of the same number disagreeing is the only signal there is — which
is why `assert_revenue_reconciles_to_line_items` exists, and why the flag is
computed in `fct_orders` rather than staging: staging cannot see the line items.

## Raw stays untyped on purpose

Every raw column is `VARCHAR`. Casting at load means a bad value either fails the
whole `COPY`, or — with `ON_ERROR = CONTINUE` — is silently skipped and the
counts quietly disagree with the source. The DAG leaves `ON_ERROR` at its
default, `ABORT_STATEMENT`, on every table: a failed COPY is a phone call, a
silently short one is a number in a report that somebody acts on.

Casting in staging moves that decision somewhere visible: a bad value becomes a
failed test with a name, beside the rule it broke.

## The tests

**29 schema tests** — uniqueness on every grain, not-null on every key,
referential integrity across six relationships, accepted ranges on ratings,
quantities and rates.

**6 singular tests**, which are the interesting half:

| | |
|---|---|
| `assert_orphans_survive_the_join` | if that LEFT JOIN is ever "tidied" to INNER, 8,041 orders vanish, revenue drops by their value, and every schema test still passes |
| `assert_revenue_reconciles_to_line_items` | the header and the sum of its lines, derived independently |
| `assert_withheld_ratings_are_not_scored_as_zero` | the tempting bug is `coalesce(rating, 0)`, which turns "declined to rate" into one star below the worst possible, across 8% of reviews |
| `assert_no_order_counted_twice` | the export's overlapping pages put the same order in the file twice; if dedupe breaks, totals look plausible and are ~1% high |
| `assert_sla_rule_is_applied_once` | the threshold lives in one `var`; a mart re-deriving it with its own number is how two dashboards disagree about the same day |
| `assert_rate_has_a_denominator` | a rate over zero orders is a division nobody noticed |

**49 pytest**, covering what SQL cannot:

- **14** on the generator and the warehouse router — that the generator still
  injects the defects the models are written to catch. A generator that quietly
  stopped would leave every dbt test passing over clean data.
- **8** on the incremental merge, against a real warehouse, nothing mocked.
- **7** on the type-2 snapshot, including one asserting a point-in-time join
  finds exactly one row at the version boundary — a gap there drops the order
  from every historical join, an overlap counts it twice, and both look fine in
  a row count.
- **20** on the text-to-SQL guard, none of which call a model.

## The SQL guard

`ai/text_to_sql.py` lets a model write SQL against the warehouse, which is an
injection surface with a helpful interface. Three things stand between it and
the data:

1. The statement must parse as exactly one `SELECT` or `WITH`.
2. Every table it names must be on an allowlist of marts.
3. It runs as a role with `SELECT` and nothing else.

**Only the third actually holds.** The first two are string checks on text a
model produced; they catch mistakes early and give a clear message, and the
grant is what makes a miss survivable. A prompt that says "only write SELECT
statements" is not a guard — it is a request, to a system whose entire behaviour
is being talked into things.

## Why DuckDB locally, Snowflake in the cloud

The warehouse is addressed by URI: `data/foodpulse.duckdb` is a file,
`snowflake://ACCOUNT/DB/SCHEMA` is Snowflake, and nothing above
`foodpulse/warehouse.py` knows which it got.

DuckDB rather than SQLite because the thing being learned is a **columnar**
warehouse, and SQLite is not one. DuckDB and Snowflake are the same shape —
columnar storage, vectorised execution, the analytical SQL surface, and both
have `MERGE INTO` — so the statement the incremental model builds locally is the
statement Snowflake would run.

Both paths rather than Snowflake alone because **a trial is thirty days**. A
project that only runs inside one stops running a month after it is written,
which is roughly when somebody opens it.

## The data is generated

Seeded, so the same seed gives the same rows and nothing large lives in git.
Rows stream to CSV rather than being held in memory, so the scale is a flag.

```bash
python -m foodpulse.generate --orders 10000000 --orphan-rate 0.05
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
aws/iam/               the three IAM documents for the S3 handshake
sql/snowflake/         setup, storage integration, stage, raw tables, COPY INTO
dbt_foodpulse/
  models/staging/      typed, deduplicated, flagged
  models/marts/        2 incremental facts, 4 dimensions, 5 marts
  snapshots/           type-2 slowly changing dimension
  macros/              schema naming
  tests/               6 singular tests
airflow/               Airflow 3 on Docker, one daily DAG
ai/                    LLM enrichment, RAG, text-to-SQL
dashboard/             Streamlit
scripts/               upload to S3, build the models, build the report
tests/                 49 pytest
docs/index.html        generated
```
