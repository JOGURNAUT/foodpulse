# Airflow 3 + the Snowflake provider + dbt, and the AI task's client.
FROM apache/airflow:3.0.3

# FAB gives the classic username/password login. Airflow 3 moved it out of core
# into a provider, so without it the UI has no way to sign in.
RUN pip install --no-cache-dir \
      apache-airflow-providers-snowflake \
      apache-airflow-providers-fab \
      openai

# dbt in its own virtualenv, not alongside Airflow.
#
# This is not tidiness. dbt and Airflow both pin Jinja, protobuf and a SQL
# driver stack, and resolving them into one environment means pip quietly
# downgrades something Airflow needs to satisfy dbt. The failure shows up later
# as a scheduler that will not start, which reads like an Airflow problem.
#
# Separate venv, and the DAG calls the binary by its absolute path.
RUN python -m venv /opt/airflow/dbt_venv && \
    /opt/airflow/dbt_venv/bin/pip install --no-cache-dir "dbt-snowflake==1.8.*"
