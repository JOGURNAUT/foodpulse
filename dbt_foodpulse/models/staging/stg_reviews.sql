-- Reviews. A null rating is NOT missing data: the user wrote text and skipped
-- the stars. It is kept as null and excluded from averages, rather than filled
-- with a zero that would drag every restaurant's score down.

select
    review_id,
    order_id,
    restaurant_id,
    cast(rating as integer)      as rating,
    review_text,
    cast(created_at as timestamp) as created_at,
    rating is null               as rating_withheld
from {{ source('raw', 'raw_reviews') }}
