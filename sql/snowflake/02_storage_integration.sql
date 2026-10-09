-- S3 as an external stage, via a role Snowflake assumes.
--
-- The point of a storage integration is that no AWS key ever enters Snowflake.
-- Snowflake is given an IAM role to assume; the trust policy on the AWS side
-- names Snowflake's own IAM user and an external ID, both of which Snowflake
-- only tells you AFTER the integration exists. So this is a two-pass setup:
--
--   1. run this, with a placeholder role ARN
--   2. DESC INTEGRATION FOODPULSE_S3 -> read STORAGE_AWS_IAM_USER_ARN and
--      STORAGE_AWS_EXTERNAL_ID
--   3. put those into the role's trust policy in AWS
--   4. the integration starts working; nothing here changes
--
-- A key pasted into a CREATE STAGE would skip all of that and put a long-lived
-- credential in the query history, where it stays.

CREATE STORAGE INTEGRATION IF NOT EXISTS FOODPULSE_S3
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = 'arn:aws:iam::<ACCOUNT_ID>:role/foodpulse-snowflake'
  STORAGE_ALLOWED_LOCATIONS = ('s3://foodpulse-lake/raw/');

-- Run this, then copy the two values into the AWS trust policy.
DESC INTEGRATION FOODPULSE_S3;
