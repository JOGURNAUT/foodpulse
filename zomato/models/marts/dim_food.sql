-- Marts (Gold, table): thin pass-through dimension, staging already did the cleanup.
select f_id, food_name, veg_or_non_veg
from {{ ref('stg_food') }}