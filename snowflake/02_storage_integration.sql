-- =====================================================================
-- Step 2 — Secure S3 <-> Snowflake link (Storage Integration)
-- This is the production-grade way to connect: no AWS access key or
-- secret key is ever stored inside Snowflake.
--
-- ORDER OF OPERATIONS (see aws/iam/ for the matching AWS-side JSON):
--   A. In AWS IAM, create policy `s3-read-policy.json` (read-only, scoped
--      to one bucket) and attach it to a new role, e.g. `snowflake-s3-role`,
--      trusted with the PLACEHOLDER policy (snowflake-role-trust-policy-initial.json)
--      for now — trusting your own AWS account root.
--   B. Run CREATE STORAGE INTEGRATION below with that role's ARN.
--   C. DESC INTEGRATION -> copy STORAGE_AWS_IAM_USER_ARN + STORAGE_AWS_EXTERNAL_ID.
--   D. Paste those two values into the role's trust policy
--      (snowflake-role-trust-policy-final.json) — only Snowflake's own
--      IAM user, scoped by that external ID, may assume the role now.
-- =====================================================================
USE ROLE ACCOUNTADMIN;

-- >>> EDIT THESE TWO <<<
--   <ROLE_ARN> = arn:aws:iam::<your-account-id>:role/snowflake-s3-role
--   <BUCKET>   = your S3 bucket name
CREATE OR REPLACE STORAGE INTEGRATION ZOMATO_S3_INT
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = '<ROLE_ARN>'
  STORAGE_ALLOWED_LOCATIONS = ('s3://<BUCKET>/');

GRANT USAGE ON INTEGRATION ZOMATO_S3_INT TO ROLE DBT_ROLE;

-- Run this, then copy the two values into the IAM role's trust policy (Step D).
-- Note: never re-run CREATE OR REPLACE on this integration afterward — it
-- regenerates the external ID and silently breaks the trust relationship.
DESC INTEGRATION ZOMATO_S3_INT;
--   STORAGE_AWS_IAM_USER_ARN  ->  the "AWS" principal in the trust policy
--   STORAGE_AWS_EXTERNAL_ID   ->  the sts:ExternalId condition
