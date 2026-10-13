-- Staging (Silver): line-item level detail -- one row per food item in an
-- order. This is the ~23M-row table, so it's kept as a cheap view, not materialized.
select
    order_item_id,
    order_id,
    r_id as restaurant_id,
    f_id,
    price::decimal(10,2) as price,
    quantity::number as quantity,
    line_amount::decimal(10,2) as line_amount
from {{ source('raw', 'order_items') }}