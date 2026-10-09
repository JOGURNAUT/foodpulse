-- One row per restaurant, with the measures the marts join back to.
--
-- Built from stg_restaurants and LEFT JOINed to its order history, not INNER:
-- a restaurant with no orders in the window is a real restaurant with a real
-- problem, and an inner join would delete exactly the ones worth looking at.

with r as (select * from {{ ref('stg_restaurants') }}),

activity as (
    select
        restaurant_id,
        count(*)                                     as orders_total,
        count(*) filter (where is_valid)             as orders_valid,
        sum(total_amount) filter (where is_valid)    as revenue,
        avg(delivery_minutes) filter (where is_valid) as avg_delivery_minutes
    from {{ ref('stg_orders') }}
    group by restaurant_id
),

scored as (
    select
        restaurant_id,
        avg(rating)                      as avg_review_rating,
        count(*)                         as reviews_total,
        count(*) filter (where rating is not null) as reviews_rated
    from {{ ref('stg_reviews') }}
    group by restaurant_id
)

select
    r.restaurant_id,
    r.restaurant_name,
    r.city,
    r.cuisine,
    r.platform_rating,
    r.onboarded_at,
    coalesce(a.orders_total, 0)  as orders_total,
    coalesce(a.orders_valid, 0)  as orders_valid,
    coalesce(a.revenue, 0)       as revenue,
    a.avg_delivery_minutes,
    s.avg_review_rating,
    coalesce(s.reviews_total, 0) as reviews_total,
    coalesce(s.reviews_rated, 0) as reviews_rated
from r
left join activity a using (restaurant_id)
left join scored   s using (restaurant_id)
