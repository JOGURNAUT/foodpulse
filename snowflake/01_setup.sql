-- =====================================================================
-- Step 1 — Warehouse, database, schemas, role
-- Run in Snowsight as ACCOUNTADMIN (a worksheet).
-- =====================================================================
USE ROLE ACCOUNTADMIN;

-- Compute: extra-small, auto-suspend fast so trial credits last.
CREATE WAREHOUSE IF NOT EXISTS ZOMATO_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND   = 60
  AUTO_RESUME    = TRUE
  INITIALLY_SUSPENDED = TRUE;

-- Database + medallion schemas (Bronze/Silver/Gold, plus snapshots and the AI layer).
CREATE DATABASE IF NOT EXISTS ZOMATO;
CREATE SCHEMA IF NOT EXISTS ZOMATO.RAW;        -- Bronze: landed as-is from S3, all-STRING columns
CREATE SCHEMA IF NOT EXISTS ZOMATO.STAGING;    -- Silver: cleaned/typed dbt views
CREATE SCHEMA IF NOT EXISTS ZOMATO.MARTS;      -- Gold: dims, incremental facts, business marts
CREATE SCHEMA IF NOT EXISTS ZOMATO.SNAPSHOTS;  -- SCD2 history (dbt snapshots)
CREATE SCHEMA IF NOT EXISTS ZOMATO.AI;         -- LLM-enriched tables (written by ai/enrich_reviews.py)

-- The role dbt and Airflow connect as.
CREATE ROLE IF NOT EXISTS DBT_ROLE;
GRANT USAGE   ON WAREHOUSE ZOMATO_WH TO ROLE DBT_ROLE;
GRANT OPERATE ON WAREHOUSE ZOMATO_WH TO ROLE DBT_ROLE;
GRANT ALL     ON DATABASE  ZOMATO    TO ROLE DBT_ROLE;
GRANT ALL     ON ALL SCHEMAS IN DATABASE ZOMATO TO ROLE DBT_ROLE;
GRANT ALL     ON FUTURE SCHEMAS IN DATABASE ZOMATO TO ROLE DBT_ROLE;
GRANT ALL     ON FUTURE TABLES  IN DATABASE ZOMATO TO ROLE DBT_ROLE;
GRANT ALL     ON FUTURE VIEWS   IN DATABASE ZOMATO TO ROLE DBT_ROLE;

-- Let your own login use the role too (handy for manual worksheet queries).
SET my_user = CURRENT_USER();
GRANT ROLE DBT_ROLE TO USER IDENTIFIER($my_user);

SELECT 'setup complete' AS status;
