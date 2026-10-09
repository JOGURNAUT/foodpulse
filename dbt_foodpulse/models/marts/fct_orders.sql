{{ config(
    materialized='incremental',
    unique_key='order_id',
    incremental_strategy='merge'
) }}

-- One row per order, joined to its restaurant.
--
-- INCREMENTAL. At two million orders a full rebuild is wasted work on every run
-- but the first: yesterday's orders do not change, so recomputing them produces
-- the same rows at the same cost. The merge touches only what came in.
--
-- The lookback is the part that is easy to get wrong. The obvious filter is
-- "rows newer than the newest row I already have", and it quietly loses data:
-- an order placed at 23:58 that reaches the export at 00:04 arrives after a
-- later-timestamped order has already pushed the watermark past it, so it is
-- never selected again. Reprocessing a trailing window means a late row is
-- still picked up, and because the strategy is MERGE on order_id, the rows that
-- come back unchanged are updated in place rather than inserted twice.
--
-- The window has a cost -- it reprocesses lookback_days of orders every run --
-- and that is the trade: a little repeated work against not silently dropping
-- late arrivals. Narrow it past the real export delay and rows go missing with
-- nothing raising.
--
-- LEFT JOIN on purpose. ~0.4% of orders point at a restaurant_id that is not in
-- the dimension -- a row deleted between two exports. An inner join would make
-- those orders vanish and the revenue total would silently fall, with nothing
-- raising. They stay, flagged, and `is_orphan` is what the test asserts on.

with o as (

    select * from {{ ref('stg_orders') }}

    {% if is_incremental() %}
    where placed_at >= (
        select coalesce(max(placed_at), timestamp '1900-01-01')
             - interval {{ var('lookback_days') }} day
        from {{ this }}
    )
    {% endif %}

),
     r as (select * from {{ ref('stg_restaurants') }}),

-- Independently recomputed from the lines, so the header has something to be
-- checked against. A magnitude ceiling cannot catch a small basket multiplied
-- by a hundred: one order here is a real 91.21 carried as 9,121.00, which is
-- under any plausible ceiling and looks like an ordinary large order. Two
-- derivations of the same number disagree, and that is the only signal there is.
     lines as (
         select order_id, round(sum(line_amount), 2) as line_total
         from {{ ref('stg_order_items') }}
         -- Aggregating all four million line items to rebuild a few thousand
         -- orders would make the incremental run cost what the full run costs.
         {% if is_incremental() %}
         where order_id in (select order_id from o)
         {% endif %}
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
