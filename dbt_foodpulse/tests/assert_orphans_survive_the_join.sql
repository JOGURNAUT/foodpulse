-- An orphaned order must still be in fct_orders.
--
-- The guard against the join quietly becoming INNER. If someone "tidies" that
-- LEFT JOIN, the orphaned orders disappear, revenue drops by their value, every
-- schema test still passes, and nothing anywhere says a row was lost.
--
-- Returns rows only when stg_orders holds an order that fct_orders does not,
-- which is a dbt test failure.

select o.order_id
from {{ ref('stg_orders') }} o
left join {{ ref('fct_orders') }} f using (order_id)
where f.order_id is null
