-- Warehouse, database, schemas. Run first, once.
--
-- The compute warehouse is XSMALL with AUTO_SUSPEND at 60 seconds, which is the
-- single setting that decides whether a trial survives the month: Snowflake
-- bills credits per second a warehouse is running, not per query, so one left
-- awake overnight costs more than every query this project will ever issue.

CREATE WAREHOUSE IF NOT EXISTS FOODPULSE_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND   = 60        -- seconds idle before it stops
  AUTO_RESUME    = TRUE
  INITIALLY_SUSPENDED = TRUE;

CREATE DATABASE IF NOT EXISTS FOODPULSE;

-- Three schemas, matching the layers. RAW holds what arrived; STAGING types it;
-- MARTS is what anything outside this project is allowed to read.
CREATE SCHEMA IF NOT EXISTS FOODPULSE.RAW;
CREATE SCHEMA IF NOT EXISTS FOODPULSE.STAGING;
CREATE SCHEMA IF NOT EXISTS FOODPULSE.MARTS;
