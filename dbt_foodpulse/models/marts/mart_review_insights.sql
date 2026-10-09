{{ config(materialized='table', tags=['ai']) }}

-- What the review text says, counted -- by city, cuisine and topic.
--
-- Tagged `ai` because it is the only model that depends on review_enriched,
-- which an LLM task produces. `dbt build --exclude tag:ai` leaves a complete,
-- correct warehouse behind without an API key; this is the layer on top.
--
-- The enrichment is a sample, not the whole table. Every rate here is therefore
-- a rate within what was labelled, and reviews_enriched is carried beside each
-- one so that is visible rather than implied -- a 60% complaint rate over 12
-- labelled reviews is not a finding.
--
-- This, not the RAG chat, is what answers "what do people complain about most".
-- Retrieval finds what is nearest in meaning to a question, which is not the
-- same as what is most common; counting is counting.

with enriched as (
    select
        e.review_id,
        e.sentiment,
        e.topic,
        r.rating,
        r.rating_withheld,
        o.city,
        o.cuisine,
        o.order_date,
        o.delivery_minutes,
        o.is_sla_breach
    from review_enriched e
    join {{ ref('stg_reviews') }} r on e.review_id = r.review_id
    join {{ ref('fct_orders') }}  o on r.order_id  = o.order_id
    where o.is_valid
)

select
    city,
    cuisine,
    topic,

    count(*)                                            as reviews_enriched,
    count(*) filter (where sentiment = 'negative')      as negative,
    count(*) filter (where sentiment = 'positive')      as positive,
    round(count(*) filter (where sentiment = 'negative') * 1.0
          / nullif(count(*), 0), 4)                     as negative_rate,

    -- The star rating beside the text's sentiment. The two disagreeing is the
    -- interesting row: four stars with text about a missing item is a customer
    -- being polite, and the stars alone would never show it.
    round(avg(rating) filter (where not rating_withheld), 2) as avg_rating,
    count(*) filter (where rating_withheld)              as ratings_withheld,

    -- Whether the complaint matches what the pipeline measured. A topic of
    -- delivery_speed on orders that mostly met the SLA means the promise is
    -- wrong rather than the delivery -- which is the same finding
    -- mart_cuisine_sla reaches from the other direction.
    round(avg(delivery_minutes), 1)                      as avg_delivery_minutes,
    round(count(*) filter (where is_sla_breach) * 1.0
          / nullif(count(*), 0), 4)                      as sla_breach_rate
from enriched
group by city, cuisine, topic
