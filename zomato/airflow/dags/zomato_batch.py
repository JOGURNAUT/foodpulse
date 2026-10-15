"""
Daily batch pipeline: S3 -> Snowflake RAW -> dbt (Silver/Gold) -> AI enrichment -> AI mart.

reload_raw      COPY INTO the 7 RAW tables from the S3 stage (idempotent --
                re-running just reloads the same files).
dbt_build_core  dbt build, excluding the AI-tagged model: staging views +
                marts (dims, incremental facts, business marts) + their tests.
enrich_reviews  Calls OpenAI per un-enriched review, writes sentiment/topic
                labels to ZOMATO.AI.REVIEW_ENRICHED (see ai/enrich_reviews.py).
dbt_build_ai    dbt build, tag:ai only -- builds mart_review_insights now that
                the enrichment it depends on exists.

This module's docstring doubles as the DAG's description in the Airflow UI
(wired up below via doc_md=__doc__).
"""
from datetime import datetime
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.providers.standard.operators.bash import BashOperator      # Airflow 3 import

DBT = "/opt/airflow/dbt_venv/bin/dbt"          # separate venv (see Dockerfile) -- keeps dbt's
                                                # pinned dbt-snowflake version isolated from Airflow's own deps
DBT_PROJECT = "/opt/airflow/dbt/zomato"

# Dimension tables (restaurants/users/food/menu) are messier real-world data,
# so ON_ERROR='CONTINUE' skips bad rows instead of failing the whole load.
# Fact tables (orders/order_items/reviews) are clean generated data, so they
# stay on the default strict behaviour -- any bad row should fail loudly.
COPY_RAW = [
    "USE WAREHOUSE ZOMATO_WH",
    "COPY INTO ZOMATO.RAW.restaurants FROM @ZOMATO.RAW.ZOMATO_RAW_STAGE/restaurants/  ON_ERROR='CONTINUE'",
    "COPY INTO ZOMATO.RAW.users       FROM @ZOMATO.RAW.ZOMATO_RAW_STAGE/users/        ON_ERROR='CONTINUE'",
    "COPY INTO ZOMATO.RAW.food        FROM @ZOMATO.RAW.ZOMATO_RAW_STAGE/food/         ON_ERROR='CONTINUE'",
    "COPY INTO ZOMATO.RAW.menu        FROM @ZOMATO.RAW.ZOMATO_RAW_STAGE/menu/         ON_ERROR='CONTINUE'",
    "COPY INTO ZOMATO.RAW.orders      FROM @ZOMATO.RAW.ZOMATO_RAW_STAGE/orders/",
    "COPY INTO ZOMATO.RAW.order_items FROM @ZOMATO.RAW.ZOMATO_RAW_STAGE/order_items/",
    "COPY INTO ZOMATO.RAW.reviews     FROM @ZOMATO.RAW.ZOMATO_RAW_STAGE/reviews/",
]

with DAG(
    dag_id="zomato_batch",
    start_date=datetime(2024, 1, 1),   # arbitrary past date; catchup=False means
    schedule="@daily",                 # Airflow won't backfill every day since then
    catchup=False,
    tags=["zomato", "dbt", "snowflake"],
    doc_md=__doc__,
) as dag:

    reload_raw = SQLExecuteQueryOperator(
        task_id="reload_raw", conn_id="snowflake_default",
        sql=COPY_RAW, split_statements=True, autocommit=True,
    )

    dbt_build_core = BashOperator(
        task_id="dbt_build_core",
        bash_command=f"{DBT} build --exclude tag:ai --project-dir {DBT_PROJECT} --profiles-dir {DBT_PROJECT}",
    )

    enrich_reviews = BashOperator(
        task_id="enrich_reviews",
        bash_command=f"python /opt/airflow/ai/enrich_reviews.py",
    )

    dbt_build_ai = BashOperator(
        task_id = "dbt_build_ai",
        bash_command=f"{DBT} build --select tag:ai --project-dir {DBT_PROJECT} --profiles-dir {DBT_PROJECT}"
    )

    # Strictly sequential on purpose: enrich_reviews needs dbt_build_core's
    # staging data, and dbt_build_ai needs enrich_reviews' output to exist
    # before mart_review_insights can join to it.
    reload_raw >> dbt_build_core >> enrich_reviews >> dbt_build_ai