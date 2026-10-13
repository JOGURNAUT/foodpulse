-- Staging (Silver): casts the raw all-STRING columns to real NUMBER/DECIMAL types.
-- try_to_* returns NULL instead of erroring on garbage values, so one bad row
-- can't fail the whole model -- the WHERE just drops rows with no valid restaurant_id.
select
    menu_id,
    try_to_number(r_id) as restaurant_id,
    f_id,
    cuisine,
    try_to_decimal(price, 10, 2) as price
from {{ source('raw', 'menu') }}
where try_to_number(r_id) is not null