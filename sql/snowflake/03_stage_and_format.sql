-- The file format and the external stage.

USE SCHEMA FOODPULSE.RAW;

CREATE FILE FORMAT IF NOT EXISTS CSV_EXPORT
  TYPE = CSV
  SKIP_HEADER = 1
  FIELD_OPTIONALLY_ENCLOSED_BY = '"'
  -- An empty cell is a null, not the two-character string "". Without this the
  -- staging casts fail on a value that looks present and is not.
  EMPTY_FIELD_AS_NULL = TRUE
  -- Refuse a file whose column count does not match the table. The default
  -- tolerates it and pads, which loads a shifted file as though it were fine.
  ERROR_ON_COLUMN_COUNT_MISMATCH = TRUE;

CREATE STAGE IF NOT EXISTS FOODPULSE_RAW_STAGE
  STORAGE_INTEGRATION = FOODPULSE_S3
  URL = 's3://foodpulse-lake/raw/'
  FILE_FORMAT = CSV_EXPORT;

LIST @FOODPULSE_RAW_STAGE;
