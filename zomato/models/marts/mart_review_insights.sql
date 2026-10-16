-- Marts (Gold, AI mart): the first dbt model that depends on an LLM output
-- rather than only raw source data -- joins the OpenAI-enriched sentiment/
-- topic labels (ZOMATO.AI.REVIEW_ENRICHED, written by ai/enrich_reviews.py)
-- back onto the reviews themselves. tag='ai' is what lets Airflow's
-- `dbt_build_ai` task build only this model with `dbt build --select tag:ai`,
-- separately from (and after) enrich_reviews.py has run.
{{ config(tags=['ai'])}}

select
rr.city,
e.topic,
e.sentiment_label,
count(*) as reviews,
round(avg(e.sentiment_score), 3) as avg_sentiment_score,
round(avg(rr.rating), 2)        as avg_star_rating,
count_if(e.key_issue is not null) as flagged_issues
from {{ source('ai', 'review_enriched')}} e
inner join {{ ref('stg_reviews')}} rr using (review_id)
group by 1, 2, 3