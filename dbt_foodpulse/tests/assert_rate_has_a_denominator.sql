-- A rate reported without the orders it was computed over is uninterpretable,
-- and a rate reported over zero orders is a division nobody noticed.

select city_day_key, orders_valid, sla_breach_rate
from {{ ref('mart_city_daily') }}
where sla_breach_rate is not null
  and (orders_valid is null or orders_valid = 0)
