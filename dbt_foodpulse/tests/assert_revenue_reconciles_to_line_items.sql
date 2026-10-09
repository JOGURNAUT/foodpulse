-- An order's total must equal the sum of its lines.
--
-- Two independent paths to the same number: the header amount the export
-- carries, and the quantity-times-price sum of the items. They can only
-- disagree if a line was lost, duplicated, or priced from the wrong menu
-- version -- all of which produce a plausible total that is simply wrong.
--
-- Only valid orders: the deliberately corrupted ones are excluded upstream, and
-- asserting on them would be asserting that the corruption is intact.

-- Reads fct_orders, not stg_orders: staging cannot see the line items, so the
-- reconciliation flag is set downstream and this asserts that nothing which
-- passed it still disagrees.
with header as (
    select order_id, total_amount
    from {{ ref('fct_orders') }}
    where is_valid
),
lines as (
    select order_id, round(sum(line_amount), 2) as line_total
    from {{ ref('stg_order_items') }}
    group by order_id
)
select
    header.order_id,
    header.total_amount,
    lines.line_total,
    abs(header.total_amount - lines.line_total) as diff
from header
join lines using (order_id)
where abs(header.total_amount - lines.line_total) > 0.02
