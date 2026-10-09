select
    menu_item_id,
    restaurant_id,
    item_name,
    cast(price as double)   as price,
    cast(is_veg as boolean) as is_veg
from {{ source('raw', 'raw_menu') }}
