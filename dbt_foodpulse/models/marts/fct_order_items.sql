{{ config(
    materialized='incremental',
    unique_key='order_item_id',
    incremental_strategy='merge'
) }}

-- One row per line of an order: the basket, at item grain.
--
-- Four million rows against two million in fct_orders, which is why this one is
-- incremental too. The grain is the line, not the order, so the unique key is
-- order_item_id -- merging on order_id here would collapse a three-item basket
-- into one row and lose two thirds of the revenue.
--
-- It carries order_date and city denormalised from the header. That is a
-- deliberate duplication: almost every question at this grain is "what sold, in
-- which city, on which day", and without them every one of those needs a join
-- back to a two-million-row fact.
--
-- INNER JOIN to the order on purpose, and it is the opposite call to the one in
-- fct_orders. A line with no header is not a late arrival, it is a broken
-- export -- there is no order to attribute its revenue to and no date to put it
-- on. Those are counted in mart_data_quality rather than carried here as rows
-- with a null everywhere that matters.

with i as (

    select * from {{ ref('stg_order_items') }}

    {% if is_incremental() %}
    where order_id in (
        select order_id from {{ ref('fct_orders') }}
        where placed_at >= (
            select coalesce(max(order_placed_at), timestamp '1900-01-01')
                 - interval {{ var('lookback_days') }} day
            from {{ this }}
        )
    )
    {% endif %}

),
     o as (select * from {{ ref('fct_orders') }}),
     m as (select * from {{ ref('stg_menu') }})

select
    i.order_item_id,
    i.order_id,
    i.menu_item_id,
    m.item_name,
    m.is_veg,
    o.restaurant_id,
    o.cuisine,
    o.city,
    o.order_date,
    o.placed_at                      as order_placed_at,
    i.quantity,
    i.unit_price,
    i.line_amount,

    -- The header's validity decides the line's. A line belonging to an order
    -- that failed a quality rule must not be summed into revenue just because
    -- the line itself looks fine.
    o.is_valid
from i
join o on i.order_id = o.order_id
left join m on i.menu_item_id = m.menu_item_id
