-- One order may appear in exactly one city-day bucket.
--
-- The export's overlapping pages put the same order in the file twice. If the
-- dedupe in staging ever stops working, the totals still look plausible --
-- about 1% high -- which is why this needs a test rather than a glance.

with per_order as (
    select order_id, count(distinct city || '|' || cast(order_date as varchar)) as buckets
    from {{ ref('fct_orders') }}
    group by order_id
)
select * from per_order where buckets > 1
