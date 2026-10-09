-- Orders, typed and deduplicated. The only place "which orders count" is decided.
--
-- Everything downstream reads this rather than raw_orders, so the rules live
-- once. Repeating them across four marts is how three agree and the fourth
-- quietly does not.

with source as (

    select * from {{ source('raw', 'raw_orders') }}

),

typed as (

    select
        order_id,
        user_id,
        restaurant_id,
        city,
        cast(placed_at    as timestamp)  as placed_at,
        cast(delivered_at as timestamp)  as delivered_at,
        cast(total_amount as double)     as total_amount,
        payment_method
    from source

),

flagged as (

    select
        *,
        datediff('minute', placed_at, delivered_at) as delivery_minutes,

        -- Each of these is a defect class seen in the export, named rather than
        -- silently filtered. A row that fails one is still here; what changes is
        -- that `is_valid` turns false and the marts stop counting it.
        delivered_at < placed_at                      as has_negative_duration,
        total_amount < 0                              as has_negative_amount,
        total_amount > {{ var('max_plausible_order') }} as has_implausible_amount

    from typed

),

deduped as (

    -- The export paginates and retries, so pages overlap and an order can
    -- arrive twice. The copies are byte-identical, which is what makes
    -- dedupe-by-key correct here: there is no later version to prefer, so any
    -- one of them is the row.
    select
        *,
        row_number() over (partition by order_id order by placed_at) as rn
    from flagged

)

select
    order_id,
    user_id,
    restaurant_id,
    city,
    placed_at,
    delivered_at,
    delivery_minutes,
    total_amount,
    payment_method,
    has_negative_duration,
    has_negative_amount,
    has_implausible_amount,
    not (has_negative_duration or has_negative_amount or has_implausible_amount)
        as is_valid,
    delivery_minutes > {{ var('sla_minutes') }} as is_sla_breach
from deduped
where rn = 1
