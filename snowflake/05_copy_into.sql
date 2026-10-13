-- =====================================================================
-- Step 5 — Load RAW from S3
-- Pulls the CSVs you uploaded to each raw/<table>/ folder into the RAW
-- tables from 04_raw_tables.sql. The header row is skipped by the file
-- format (SKIP_HEADER = 1); rows load by column position.
--
-- This same COPY INTO logic is what Airflow's `reload_raw` task runs
-- automatically every day (see airflow/dags/zomato_batch.py) — this file
-- is for the one-time manual initial load.
-- =====================================================================
USE ROLE ACCOUNTADMIN;
USE DATABASE ZOMATO;
USE SCHEMA RAW;
USE WAREHOUSE ZOMATO_WH;

-- Dimensions = real, messier source data -> tolerate and skip bad rows.
COPY INTO RAW.restaurants FROM @ZOMATO_RAW_STAGE/restaurants/  ON_ERROR = 'CONTINUE';
COPY INTO RAW.users       FROM @ZOMATO_RAW_STAGE/users/        ON_ERROR = 'CONTINUE';
COPY INTO RAW.food        FROM @ZOMATO_RAW_STAGE/food/         ON_ERROR = 'CONTINUE';
COPY INTO RAW.menu        FROM @ZOMATO_RAW_STAGE/menu/         ON_ERROR = 'CONTINUE';

-- Facts = clean generated data -> stay strict so row counts stay exact.
COPY INTO RAW.orders      FROM @ZOMATO_RAW_STAGE/orders/;
COPY INTO RAW.order_items FROM @ZOMATO_RAW_STAGE/order_items/;
COPY INTO RAW.reviews     FROM @ZOMATO_RAW_STAGE/reviews/;

-- Sanity check — expected roughly: orders = 10,000,000 · order_items ≈ 23,000,000 ·
-- restaurants ≈ 148,541 · reviews ≈ 300,000.
SELECT 'restaurants' AS t, COUNT(*) AS n FROM RAW.restaurants
UNION ALL SELECT 'users',       COUNT(*) FROM RAW.users
UNION ALL SELECT 'food',        COUNT(*) FROM RAW.food
UNION ALL SELECT 'menu',        COUNT(*) FROM RAW.menu
UNION ALL SELECT 'orders',      COUNT(*) FROM RAW.orders
UNION ALL SELECT 'order_items', COUNT(*) FROM RAW.order_items
UNION ALL SELECT 'reviews',     COUNT(*) FROM RAW.reviews
ORDER BY t;
