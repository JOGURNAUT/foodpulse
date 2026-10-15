-- Marts (Gold, table): thin pass-through -- all the real cleanup (ratings,
-- cost parsing, city extraction) already happened in stg_restaurants.
select
restaurant_id,
restaurant_name, 
city, cuisine, 
rating, 
rating_count, 
cost_for_two
from {{ ref('stg_restaurants') }}