{{ config(materialized='table') }}

-- One row per menu item, with how it actually sells.
--
-- units_sold and revenue come from the line grain, so a three-unit line counts
-- as three. orders_appeared_in comes from the order grain and counts that line
-- once. Both are here because they answer different questions -- "how much of
-- this do we move" against "how many baskets does it pull" -- and a single
-- column called something like "sales" would be read as whichever one the
-- reader assumed.
--
-- A priced item that has never sold stays, with zeroes. It is the menu item
-- worth asking about.

with m as (select * from {{ ref('stg_menu') }}),
     r as (select * from {{ ref('stg_restaurants') }}),

     sold as (
         select
             menu_item_id,
             sum(quantity)                   as units_sold,
             sum(line_amount)                as revenue,
             count(distinct order_id)        as orders_appeared_in
         from {{ ref('fct_order_items') }}
         where is_valid
         group by menu_item_id
     )

select
    m.menu_item_id,
    m.restaurant_id,
    r.restaurant_name,
    r.cuisine,
    r.city,
    m.item_name,
    m.price                                  as list_price,
    m.is_veg,
    coalesce(s.units_sold, 0)                as units_sold,
    round(coalesce(s.revenue, 0), 2)         as revenue,
    coalesce(s.orders_appeared_in, 0)        as orders_appeared_in,

    -- What it actually went out at, against what the menu says. A gap means
    -- discounting, or a price change the menu export has not caught up with.
    round(coalesce(s.revenue, 0)
          / nullif(s.units_sold, 0), 2)      as realised_unit_price
from m
left join r on m.restaurant_id = r.restaurant_id
left join sold s on m.menu_item_id = s.menu_item_id
