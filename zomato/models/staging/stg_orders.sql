-- Staging (Silver): orders.csv was already clean/typed at generation time, so
-- this is mostly a pass-through -- just the same city cleanup as restaurants,
-- plus a boolean is_delivered flag derived from order_status.
select
    order_id,
    order_timestamp,
    order_date,
    user_id as customer_id,
    r_id as restaurant_id,
    trim(coalesce(regexp_substr(restaurant_city, '[^,]+$'), restaurant_city)) as city,
    cuisine,
    items_count,
    sales_qty,
    subtotal,
    discount,
    delivery_fee,
    gst,
    sales_amount,
    currency,
    payment_method,
    order_status,
    (order_status = 'Delivered') as is_delivered,
    customer_rating,
    delivery_time_min
from {{ source('raw', 'orders') }}