-- One row per order, joined to its restaurant.
--
-- LEFT JOIN on purpose. ~0.4% of orders point at a restaurant_id that is not in
-- the dimension -- a row deleted between two exports. An inner join would make
-- those orders vanish and the revenue total would silently fall, with nothing
-- raising. They stay, flagged, and `is_orphan` is what the test asserts on.

with o as (select * from {{ ref('stg_orders') }}),
     r as (select * from {{ ref('stg_restaurants') }}),

-- Independently recomputed from the lines, so the header has something to be
-- checked against. A magnitude ceiling cannot catch a small basket multiplied
-- by a hundred: one order here is a real 91.21 carried as 9,121.00, which is
-- under any plausible ceiling and looks like an ordinary large order. Two
-- derivations of the same number disagree, and that is the only signal there is.
     lines as (
         select order_id, round(sum(line_amount), 2) as line_total
         from {{ ref('stg_order_items') }}
         group by order_id
     )

select
    o.order_id,
    o.user_id,
    o.restaurant_id,
    r.restaurant_name,
    r.cuisine,
    coalesce(r.city, o.city)         as city,
    o.placed_at,
    cast(o.placed_at as date)        as order_date,
    o.delivered_at,
    o.delivery_minutes,
    o.total_amount,
    o.payment_method,
    l.line_total,
    abs(o.total_amount - l.line_total) > 0.02 as has_amount_mismatch,

    -- is_valid is narrowed here, not in staging: staging cannot see the lines.
    o.is_valid
        and not coalesce(abs(o.total_amount - l.line_total) > 0.02, false)
                                     as is_valid,
    o.is_sla_breach,
    r.restaurant_id is null          as is_orphan
from o
left join r on o.restaurant_id = r.restaurant_id
left join lines l on o.order_id = l.order_id
