-- =====================================================================
-- Step 3 — External stage on S3 + CSV file format
-- =====================================================================
USE ROLE ACCOUNTADMIN;
USE DATABASE ZOMATO;
USE SCHEMA RAW;

-- Plain CSV (manual upload, no gzip). Files keep their header row, so SKIP_HEADER = 1.
-- The reviews comment field contains commas but is quoted, so keep the quote char.
CREATE OR REPLACE FILE FORMAT ZOMATO.RAW.CSV_FMT
  TYPE = 'CSV'
  COMPRESSION = 'AUTO'
  FIELD_DELIMITER = ','
  FIELD_OPTIONALLY_ENCLOSED_BY = '"'
  SKIP_HEADER = 1
  EMPTY_FIELD_AS_NULL = TRUE
  NULL_IF = ('', '\\N', 'NULL')
  TRIM_SPACE = FALSE
  ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE;   -- messy source rows NULL-fill instead of aborting

-- >>> EDIT <BUCKET> <<<
-- Upload one CSV per folder: raw/restaurants/ raw/users/ raw/food/ raw/menu/
--                            raw/orders/ raw/order_items/ raw/reviews/
CREATE OR REPLACE STAGE ZOMATO.RAW.ZOMATO_RAW_STAGE
  STORAGE_INTEGRATION = ZOMATO_S3_INT
  URL = 's3://<BUCKET>/raw/'
  FILE_FORMAT = ZOMATO.RAW.CSV_FMT;

-- Confirm Snowflake can see your files (should list all seven table folders).
LIST @ZOMATO.RAW.ZOMATO_RAW_STAGE;
