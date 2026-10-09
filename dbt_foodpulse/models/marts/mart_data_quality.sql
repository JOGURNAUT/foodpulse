-- What the warehouse threw out, and why, as a table rather than a log line.
--
-- Every row excluded from the marts is counted here under the rule that
-- excluded it. Without this, "we load clean data" is a claim with nothing
-- behind it, and a defect rate that doubles overnight looks exactly like a
-- quiet week.

with o as (select * from {{ ref('stg_orders') }}),
     f as (select * from {{ ref('fct_orders') }}),
     v as (select * from {{ ref('stg_reviews') }})

select 'orders_total'            as metric, count(*) as rows from o
union all select 'orders_valid',            count(*) from f where is_valid
union all select 'negative_duration',       count(*) from o where has_negative_duration
union all select 'negative_amount',         count(*) from o where has_negative_amount
union all select 'implausible_amount',      count(*) from o where has_implausible_amount
union all select 'amount_vs_lines_mismatch', count(*) from f where has_amount_mismatch
union all select 'orphaned_restaurant',     count(*) from f where is_orphan
union all select 'reviews_total',           count(*) from v
union all select 'reviews_rating_withheld', count(*) from v where rating_withheld
