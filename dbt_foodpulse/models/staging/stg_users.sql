select
    user_id,
    city,
    cast(signed_up_at as date) as signed_up_at
from {{ source('raw', 'raw_users') }}
