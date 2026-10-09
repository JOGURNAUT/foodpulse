{{ config(materialized='table') }}

-- A calendar, generated rather than derived from the orders.
--
-- This is the point of it. If the date dimension were `select distinct
-- order_date from fct_orders`, then a day with no orders would not exist, and a
-- day with no orders is exactly the day you need to see: the outage, the city
-- that went dark, the restaurant that stopped. Joining to a generated calendar
-- turns that into a visible zero instead of a missing row.
--
-- The range runs a year either side of the data so a backfill or a late arrival
-- lands inside it rather than falling out of every report that joins here.

with bounds as (
    select
        least(min(order_date), current_date) - interval 365 day as from_day,
        greatest(max(order_date), current_date) + interval 365 day as to_day
    from {{ ref('fct_orders') }}
),

days as (
    select unnest(generate_series(
        (select from_day from bounds),
        (select to_day from bounds),
        interval 1 day
    ))::date as date_day
)

select
    date_day,
    extract(year    from date_day)              as year,
    extract(month   from date_day)              as month,
    extract(day     from date_day)              as day_of_month,
    extract(dayofweek from date_day)            as day_of_week,
    strftime(date_day, '%A')                    as day_name,
    strftime(date_day, '%B')                    as month_name,
    date_trunc('week',  date_day)::date         as week_start,
    date_trunc('month', date_day)::date         as month_start,
    extract(dayofweek from date_day) in (0, 6)  as is_weekend
from days
