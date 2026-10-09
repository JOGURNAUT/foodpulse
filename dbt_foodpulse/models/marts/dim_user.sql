{{ config(materialized='table') }}

-- One row per user, with their ordering behaviour attached.
--
-- Only valid orders count towards the behaviour columns. A user whose single
-- order was a refund written as a negative total would otherwise show lifetime
-- revenue below zero, and the segment built from it would be nonsense.
--
-- LEFT JOIN, so a user who has never ordered is still here with zeroes. They
-- are the population every acquisition question is about, and an inner join
-- would answer "how many users never ordered" with "none".

with u as (select * from {{ ref('stg_users') }}),

     activity as (
         select
             user_id,
             count(*)                                as orders_total,
             count(*) filter (where is_valid)        as orders_valid,
             sum(total_amount) filter (where is_valid) as revenue,
             min(placed_at)                          as first_order_at,
             max(placed_at)                          as last_order_at,
             count(*) filter (where is_valid and is_sla_breach) as sla_breaches
         from {{ ref('fct_orders') }}
         group by user_id
     )

select
    u.user_id,
    u.city,
    u.signed_up_at,
    coalesce(a.orders_total, 0)                 as orders_total,
    coalesce(a.orders_valid, 0)                 as orders_valid,
    round(coalesce(a.revenue, 0), 2)            as lifetime_revenue,
    round(coalesce(a.revenue, 0)
          / nullif(a.orders_valid, 0), 2)       as avg_order_value,
    a.first_order_at,
    a.last_order_at,
    coalesce(a.sla_breaches, 0)                 as sla_breaches,

    -- Days between signing up and ordering for the first time. Null, not zero,
    -- for someone who never ordered: zero would read as "ordered the same day",
    -- which is the opposite of what happened.
    case when a.first_order_at is not null
         then datediff('day', u.signed_up_at, a.first_order_at)
    end                                         as days_to_first_order,

    case
        when coalesce(a.orders_valid, 0) = 0  then 'never ordered'
        when a.orders_valid = 1               then 'one-time'
        when a.orders_valid <= 5              then 'occasional'
        when a.orders_valid <= 20             then 'regular'
        else                                       'heavy'
    end                                         as frequency_segment
from u
left join activity a on u.user_id = a.user_id
