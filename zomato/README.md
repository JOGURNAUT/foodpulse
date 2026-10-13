# zomato (dbt project)

This is the dbt project for FoodPulse — see the [repo root README](../README.md)
for the full pipeline architecture, tech stack, and how to run everything.

Quick commands from this directory:

```bash
dbt debug              # check the Snowflake connection
dbt build --exclude tag:ai   # staging views + marts + tests
dbt build --select tag:ai    # the AI-enriched mart (needs ai/enrich_reviews.py to have run first)
```
