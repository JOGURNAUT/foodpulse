{% snapshot snap_restaurant %}

{{ config(
    unique_key='restaurant_id',
    strategy='check',
    check_cols=['restaurant_name', 'city', 'cuisine', 'platform_rating']
) }}

-- Slowly changing dimension, type 2: keep the history the source destroys.
--
-- The restaurants export is a snapshot of right now. A restaurant that moved
-- from Pune to Bengaluru in September looks, in today's file, like it was
-- always in Bengaluru -- and every September order gets attributed to the wrong
-- city the moment a report joins to the current dimension. The source does not
-- lie; it simply has no memory, and nothing downstream can recover what it
-- overwrote.
--
-- So each version gets its own row, with the window it was true for:
--
--   R00042  Pune        2026-08-01 -> 2026-09-14    (closed)
--   R00042  Bengaluru   2026-09-14 -> null          (current)
--
-- A report then joins on the order's date falling inside that window, and
-- September's orders stay in Pune where they happened.
--
-- strategy='check' rather than 'timestamp' because this source has no reliable
-- updated_at. A timestamp strategy is cheaper -- one comparison instead of four
-- -- but it trusts a column the upstream system has to maintain correctly, and
-- a row updated without its timestamp moving is invisible to it forever.
--
-- check_cols lists the columns a change in which is worth a new version. It is
-- deliberately not every column: including something that churns on its own,
-- like a recomputed score, would open a new version every single run and turn
-- the history into noise with the real moves buried in it.

select
    restaurant_id,
    restaurant_name,
    city,
    cuisine,
    platform_rating,
    onboarded_at
from {{ ref('stg_restaurants') }}

{% endsnapshot %}
