-- Marts (Gold, table): customer dimension. Adds a generation bucket
-- (age_segment) on top of staging so marts/dashboards can slice revenue
-- by generation without repeating this CASE everywhere.
select
customer_id,
customer_name,
email,
age,
CASE WHEN age < 25 then 'Gen Z'
     WHEN age < 40 then 'Millennial'
     WHEN age < 55 then 'Gen X'
     WHEN age is null then 'Unknown'
     ELSE 'Boomer' END as age_segment,
gender,
marital_status,
occupation,
income_band,
education,
family_size
from {{ ref('stg_users') }}