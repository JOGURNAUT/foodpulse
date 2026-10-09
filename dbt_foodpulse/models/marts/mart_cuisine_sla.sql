-- Delivery performance by cuisine. The finding this project exists to surface.
--
-- p50 and p90 sit beside the mean because an average alone cannot separate a
-- cuisine that is uniformly slow from one that is fine for most orders and
-- badly late in the tail. Those need different fixes -- more riders versus a
-- prep-time problem at specific restaurants.

with valid as (
    select * from {{ ref('fct_orders') }}
    where is_valid and cuisine is not null
)

select
    cuisine,
    count(*)                                            as orders,
    round(avg(delivery_minutes), 2)                     as avg_minutes,
    round(quantile_cont(delivery_minutes, 0.5), 2)      as p50_minutes,
    round(quantile_cont(delivery_minutes, 0.9), 2)      as p90_minutes,
    count(*) filter (where is_sla_breach)               as sla_breaches,
    round(count(*) filter (where is_sla_breach) * 1.0 / count(*), 4) as sla_breach_rate,
    round(sum(total_amount), 2)                         as revenue
from valid
group by cuisine
