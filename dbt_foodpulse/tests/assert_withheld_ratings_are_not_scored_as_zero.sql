-- A review with no stars must not pull an average down.
--
-- The tempting bug is coalesce(rating, 0), which turns "declined to rate" into
-- "rated it one star below the worst possible" -- and ~8% of reviews here have
-- no rating, so it would move every restaurant's score visibly.
--
-- Fails if any restaurant's average sits below the lowest rating it actually
-- received, which is only possible if nulls were counted.

with per_restaurant as (
    select
        restaurant_id,
        min(rating) filter (where rating is not null) as lowest_real_rating,
        avg(rating) filter (where rating is not null) as avg_real
    from {{ ref('stg_reviews') }}
    group by restaurant_id
    having count(*) filter (where rating is not null) > 0
)
select p.restaurant_id, p.lowest_real_rating, d.avg_review_rating
from per_restaurant p
join {{ ref('dim_restaurant') }} d using (restaurant_id)
where d.avg_review_rating < p.lowest_real_rating - 0.001
