-- Marts (Gold, business mart): daily revenue rollup by city. GMV and AOV only
-- count delivered orders (sales_amount is 0'd out otherwise via the IFF), so
-- a cancelled order's value doesn't inflate revenue. div0() avoids a divide-
-- by-zero on a city/day with zero orders instead of erroring the model.
select
order_date,
city,
count(*) as orders, 
count_if(is_delivered) as delivered_orders,
round(div0(count_if(order_status='Cancelled'), count(*)),4) as cancel_rate,
sum(iff(is_delivered, sales_amount, 0)) as gmv,
round(div0(sum(iff(is_delivered, sales_amount,0)), count_if(is_delivered)),2) as aov
from {{ ref('fct_orders') }} group by 1,2