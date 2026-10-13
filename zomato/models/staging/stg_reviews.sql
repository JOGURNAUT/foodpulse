-- Staging (Silver): joins in the restaurant's city (reviews.csv doesn't carry
-- one of its own) and drops reviews with no comment text, since this table
-- feeds the AI layer (enrich_reviews.py, rag_chat.py) where an empty comment
-- is useless to embed or classify.
select
r.review_id,
r.order_id,
r.user_id::number as customer_id,
r.restaurant_id::string as restaurant_id,
r.rating::number as rating,
r.comment::string as comment,
r.review_date::DATE as review_date,
res.city as city,
from {{ source('raw', 'reviews') }} r
left join {{ ref('stg_restaurants') }} res on r.restaurant_id = res.restaurant_id
where r.comment is not null