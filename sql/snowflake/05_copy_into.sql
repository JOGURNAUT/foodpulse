-- Bulk load from the stage.
--
-- COPY INTO, not INSERT. Snowflake bills compute by the second and an INSERT
-- per row spends nearly all of it on round trips; COPY reads the files in
-- parallel across the warehouse and is the only sensible way in.
--
-- ON_ERROR = ABORT_STATEMENT on purpose. The alternative, CONTINUE, skips bad
-- rows and reports success, so the table ends up short by an amount nobody
-- counted. A load that cannot complete should fail and say so.
--
-- COPY also tracks which files it has already loaded and skips them, so
-- re-running this after a partial failure does not duplicate anything. FORCE =
-- TRUE would defeat that and is deliberately not here.

USE SCHEMA FOODPULSE.RAW;

COPY INTO RAW_RESTAURANTS (restaurant_id, name, city, cuisine, rating, onboarded_at)
  FROM @FOODPULSE_RAW_STAGE/restaurants.csv
  FILE_FORMAT = (FORMAT_NAME = CSV_EXPORT) ON_ERROR = ABORT_STATEMENT;

COPY INTO RAW_MENU (menu_item_id, restaurant_id, item_name, price, is_veg)
  FROM @FOODPULSE_RAW_STAGE/menu.csv
  FILE_FORMAT = (FORMAT_NAME = CSV_EXPORT) ON_ERROR = ABORT_STATEMENT;

COPY INTO RAW_USERS (user_id, city, signed_up_at)
  FROM @FOODPULSE_RAW_STAGE/users.csv
  FILE_FORMAT = (FORMAT_NAME = CSV_EXPORT) ON_ERROR = ABORT_STATEMENT;

COPY INTO RAW_ORDERS (order_id, user_id, restaurant_id, city, placed_at,
                      delivered_at, total_amount, payment_method)
  FROM @FOODPULSE_RAW_STAGE/orders.csv
  FILE_FORMAT = (FORMAT_NAME = CSV_EXPORT) ON_ERROR = ABORT_STATEMENT;

COPY INTO RAW_ORDER_ITEMS (order_item_id, order_id, menu_item_id, quantity, unit_price)
  FROM @FOODPULSE_RAW_STAGE/order_items.csv
  FILE_FORMAT = (FORMAT_NAME = CSV_EXPORT) ON_ERROR = ABORT_STATEMENT;

COPY INTO RAW_REVIEWS (review_id, order_id, restaurant_id, rating, review_text, created_at)
  FROM @FOODPULSE_RAW_STAGE/reviews.csv
  FILE_FORMAT = (FORMAT_NAME = CSV_EXPORT) ON_ERROR = ABORT_STATEMENT;

-- What the load actually did, per file.
SELECT * FROM TABLE(INFORMATION_SCHEMA.COPY_HISTORY(
  TABLE_NAME => 'RAW_ORDERS', START_TIME => DATEADD(hours, -1, CURRENT_TIMESTAMP())));
