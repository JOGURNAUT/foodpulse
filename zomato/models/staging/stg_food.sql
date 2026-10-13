-- Staging (Silver): one row per menu item, Title Case'd veg/non-veg flag.
-- Drops rows with no f_id so they can't break joins downstream.
select
    f_id,
    item as food_name,
    initcap(veg_or_non_veg) as veg_or_non_veg
from {{ source('raw', 'food') }}
where f_id is not null