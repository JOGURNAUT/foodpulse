-- Per city per day, over valid orders only.
--
-- orders_total and orders_valid are both carried deliberately. A breach rate
-- over 40 of 300 orders and one over 290 of 300 are different claims, and a
-- table that reports only the rate makes them look identical.

select
    city || '|' || cast(order_date as varchar)      as city_day_key,
    city,
    order_date,
    count(*)                                        as orders_total,
    count(*) filter (where is_valid)                as orders_valid,
    count(*) filter (where is_orphan)               as orders_orphaned,
    round(sum(total_amount) filter (where is_valid), 2) as revenue,
    round(avg(total_amount) filter (where is_valid), 2) as avg_order_value,
    round(avg(delivery_minutes) filter (where is_valid), 2) as avg_delivery_minutes,
    count(*) filter (where is_valid and is_sla_breach)  as sla_breaches,
    case when count(*) filter (where is_valid) > 0
         then round(count(*) filter (where is_valid and is_sla_breach) * 1.0
                    / count(*) filter (where is_valid), 4) end as sla_breach_rate
from {{ ref('fct_orders') }}
group by city, order_date
