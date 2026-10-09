select
    order_item_id,
    order_id,
    menu_item_id,
    cast(quantity   as integer) as quantity,
    cast(unit_price as double)  as unit_price,
    cast(quantity as integer) * cast(unit_price as double) as line_amount
from {{ source('raw', 'raw_order_items') }}
