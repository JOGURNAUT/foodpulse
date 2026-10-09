-- The SLA threshold lives in one place.
--
-- is_sla_breach is set in staging from var('sla_minutes'). If a mart ever
-- re-derives it with its own number, the two drift and two dashboards disagree
-- about the same day. This checks the flag still matches the rule.

select order_id, delivery_minutes, is_sla_breach
from {{ ref('fct_orders') }}
where is_valid
  and is_sla_breach != (delivery_minutes > {{ var('sla_minutes') }})
