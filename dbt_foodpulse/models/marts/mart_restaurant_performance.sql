{{ config(materialized='table') }}

-- One row per restaurant: volume, revenue, speed, and how its customers rate it.
--
-- Every rate here carries its denominator alongside it. A restaurant breaching
-- the SLA 100% of the time across three orders and one breaching it 40% across
-- nine thousand are different facts, and a table that reports only the rate
-- makes the first one look like the emergency.
--
-- avg_rating deliberately excludes withheld ratings rather than scoring them
-- zero. Around 8% of reviews have text and no stars, and coalescing those to
-- zero would drag the average below the worst rating a user can actually give.
-- rated_reviews is next to it so the average can be read with its sample size.

with o as (select * from {{ ref('fct_orders') }} where is_valid),
     r as (select * from {{ ref('stg_restaurants') }}),

     reviews as (
         select
             restaurant_id,
             count(*)                                     as reviews_total,
             count(*) filter (where not rating_withheld)  as rated_reviews,
             avg(rating) filter (where not rating_withheld) as avg_rating
         from {{ ref('stg_reviews') }}
         group by restaurant_id
     ),

     orders as (
         select
             restaurant_id,
             count(*)                                  as orders_valid,
             count(distinct user_id)                   as distinct_users,
             sum(total_amount)                         as revenue,
             avg(delivery_minutes)                     as avg_delivery_minutes,
             quantile_cont(delivery_minutes, 0.5)      as p50_minutes,
             quantile_cont(delivery_minutes, 0.9)      as p90_minutes,
             count(*) filter (where is_sla_breach)      as sla_breaches,
             min(order_date)                           as first_order_date,
             max(order_date)                           as last_order_date
         from o
         group by restaurant_id
     )

select
    r.restaurant_id,
    r.restaurant_name,
    r.city,
    r.cuisine,
    r.platform_rating,
    r.onboarded_at,

    coalesce(o.orders_valid, 0)                   as orders_valid,
    coalesce(o.distinct_users, 0)                 as distinct_users,
    round(coalesce(o.revenue, 0), 2)              as revenue,
    round(coalesce(o.revenue, 0)
          / nullif(o.orders_valid, 0), 2)         as avg_order_value,

    round(o.avg_delivery_minutes, 1)              as avg_delivery_minutes,
    round(o.p50_minutes, 1)                       as p50_minutes,
    round(o.p90_minutes, 1)                       as p90_minutes,
    coalesce(o.sla_breaches, 0)                   as sla_breaches,
    round(o.sla_breaches * 1.0
          / nullif(o.orders_valid, 0), 4)         as sla_breach_rate,

    coalesce(v.reviews_total, 0)                  as reviews_total,
    coalesce(v.rated_reviews, 0)                  as rated_reviews,
    round(v.avg_rating, 2)                        as avg_rating,

    -- How far the stars have drifted from the platform's own number. A large
    -- gap is either a stale platform rating or a restaurant that recently
    -- changed, and both are worth a look.
    round(v.avg_rating - r.platform_rating, 2)    as rating_delta,

    o.first_order_date,
    o.last_order_date
from r
left join orders o on r.restaurant_id = o.restaurant_id
left join reviews v on r.restaurant_id = v.restaurant_id
