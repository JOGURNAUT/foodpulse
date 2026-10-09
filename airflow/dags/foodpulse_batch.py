"""FoodPulse daily batch: S3 -> Snowflake RAW -> dbt -> LLM enrichment -> AI mart.

    copy_into_raw  ->  dbt_build_core  ->  enrich_reviews  ->  dbt_build_ai

One DAG rather than four schedules, because these are not four jobs that happen
to run at the same time -- each one is wrong without the one before it. A
separate schedule for dbt would eventually run it against a half-loaded RAW on
the morning the COPY was slow, produce marts from partial data, and report
success.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.providers.standard.operators.bash import BashOperator

DBT = "/opt/airflow/dbt_venv/bin/dbt"
PROJECT = "/opt/airflow/dbt/dbt_foodpulse"
STAGE = "@FOODPULSE.RAW.FOODPULSE_RAW_STAGE"

# ON_ERROR is left at its default, ABORT_STATEMENT, on purpose and on every
# table. CONTINUE skips the rows it cannot parse and reports success, so the
# table ends up short by an amount nobody counted and the DAG goes green. A
# failed COPY is a phone call; a silently short one is a number in a report that
# somebody acts on.
#
# FORCE is deliberately absent. Snowflake remembers which files it has loaded,
# so a re-run after a partial failure skips them instead of loading them twice.
COPY_RAW = [
    "USE WAREHOUSE FOODPULSE_WH",
    f"COPY INTO FOODPULSE.RAW.raw_restaurants FROM {STAGE}/restaurants/",
    f"COPY INTO FOODPULSE.RAW.raw_menu        FROM {STAGE}/menu/",
    f"COPY INTO FOODPULSE.RAW.raw_users       FROM {STAGE}/users/",
    f"COPY INTO FOODPULSE.RAW.raw_orders      FROM {STAGE}/orders/",
    f"COPY INTO FOODPULSE.RAW.raw_order_items FROM {STAGE}/order_items/",
    f"COPY INTO FOODPULSE.RAW.raw_reviews     FROM {STAGE}/reviews/",
]

default_args = {
    # A COPY that lost its connection and a Snowflake warehouse still resuming
    # are both worth one more attempt. Three with a gap between them, not ten
    # immediately -- retrying a genuinely broken statement nine more times just
    # delays the alert by however long the retries take.
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="foodpulse_batch",
    start_date=datetime(2026, 8, 1),
    schedule="@daily",
    # The COPY reloads whatever is in the stage rather than a dated partition,
    # so a backfill of sixty days would run the same statement sixty times
    # against the same files. catchup belongs on a pipeline whose tasks are
    # parameterised by the interval they are for; this one is not.
    catchup=False,
    # The one setting that is not optional here. fct_orders is incremental, and
    # two runs merging into it at once is a race: both read the same watermark,
    # both reprocess the same window, and the second overwrites rows the first
    # was still writing. A slow run must delay the next one, not overlap it.
    max_active_runs=1,
    default_args=default_args,
    tags=["foodpulse", "dbt", "snowflake", "s3"],
    doc_md=__doc__,
) as dag:

    copy_into_raw = SQLExecuteQueryOperator(
        task_id="copy_into_raw",
        conn_id="snowflake_default",
        sql=COPY_RAW,
        split_statements=True,
        autocommit=True,
        doc_md="S3 -> Snowflake RAW, every column still text. "
               "Casting happens in staging, where a bad value becomes a named "
               "failing test instead of a row the loader quietly dropped.",
    )

    # `dbt build` rather than `dbt run` followed by `dbt test`: build runs each
    # model and then its own tests before anything downstream of it starts, so a
    # model that fails its test does not feed the mart below it. run-then-test
    # builds the whole warehouse on bad data and tells you afterwards.
    dbt_build_core = BashOperator(
        task_id="dbt_build_core",
        bash_command=(
            f"{DBT} build --exclude tag:ai "
            f"--project-dir {PROJECT} --profiles-dir {PROJECT}"
        ),
        doc_md="Staging and marts, with their tests, in dependency order.",
    )

    # Sample-capped and idempotent: it only asks the model about reviews that
    # have no enrichment row yet. Without that, every run pays again for every
    # review it has already read, and the bill grows with the table rather than
    # with the new rows.
    enrich_reviews = BashOperator(
        task_id="enrich_reviews",
        bash_command="python /opt/airflow/ai/enrich_reviews.py",
        doc_md="LLM as a transformation step: review text -> structured "
               "sentiment and topic columns in FOODPULSE.AI.",
    )

    # Separate from dbt_build_core because it depends on a table the core build
    # cannot produce. Tagged, so the core build can run without an API key at
    # all and the pipeline still has a working warehouse at the end of it.
    dbt_build_ai = BashOperator(
        task_id="dbt_build_ai",
        bash_command=(
            f"{DBT} build --select tag:ai "
            f"--project-dir {PROJECT} --profiles-dir {PROJECT}"
        ),
        doc_md="The marts that read the enriched reviews.",
    )

    copy_into_raw >> dbt_build_core >> enrich_reviews >> dbt_build_ai
