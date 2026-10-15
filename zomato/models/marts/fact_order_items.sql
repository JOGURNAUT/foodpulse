-- Marts (Gold, incremental fact): ~23M rows -- the line-item grain, so same
-- merge-on-incremental pattern as fct_orders. Joins in order_timestamp from
-- stg_orders purely to drive the incremental filter (order_items has no
-- timestamp of its own); order_date/city are denormalized here too so this
-- table can be queried standalone without an extra join to orders.
{{ config(materialized='incremental', unique_key='order_item_id', incremental_strategy='merge', on_schema_change='append_new_columns') }}
select oi.order_item_id, oi.order_id, oi.restaurant_id, oi.f_id, o.order_timestamp as order_ts,
       o.order_date, o.city, oi.price, oi.quantity, oi.line_amount
from {{ ref('stg_order_items') }} oi
inner join {{ ref('stg_orders') }} o using (order_id)
{% if is_incremental() %}
  where o.order_timestamp > (select coalesce(max(order_ts),'1900-01-01'::timestamp) from {{ this }})
{% endif %}