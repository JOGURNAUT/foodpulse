-- Restaurants, typed. One row per restaurant_id; the source is already unique
-- on it and the schema test below is what keeps that true.

select
    restaurant_id,
    name                              as restaurant_name,
    city,
    cuisine,
    cast(rating as double)            as platform_rating,
    cast(onboarded_at as date)        as onboarded_at
from {{ source('raw', 'raw_restaurants') }}
