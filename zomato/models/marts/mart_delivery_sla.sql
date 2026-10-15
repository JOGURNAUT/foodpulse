-- Marts (Gold, business mart): delivery-time SLA by city and hour-of-day.
-- p50/p90 (median + 90th percentile) matter more than a plain average here --
-- a few very late deliveries would skew an average but barely move the
-- percentiles, so this is the more honest "how long does delivery actually take" view.
select city,
hour(order_timestamp) as order_hour,
count_if(is_delivered) as delivered_orders,
round(median(delivery_time_min),1) as p50, 
round(percentile_cont(0.9) within group (order by delivery_time_min),1) as p90
from {{ ref('fct_orders') }} 
where is_delivered
group by 1,2