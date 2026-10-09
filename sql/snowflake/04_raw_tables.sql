-- Raw tables: every column VARCHAR, deliberately.
--
-- Typing at load means a bad value fails the COPY and the whole file is
-- rejected, or -- worse, with ON_ERROR = CONTINUE -- the bad rows are silently
-- skipped and the counts quietly disagree with the source.
--
-- Loading as text and casting in staging moves that decision somewhere visible:
-- a bad value becomes a failed test with a name, next to the rule it broke.

USE SCHEMA FOODPULSE.RAW;

CREATE OR REPLACE TABLE RAW_RESTAURANTS (
  restaurant_id VARCHAR, name VARCHAR, city VARCHAR,
  cuisine VARCHAR, rating VARCHAR, onboarded_at VARCHAR,
  _loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TABLE RAW_MENU (
  menu_item_id VARCHAR, restaurant_id VARCHAR, item_name VARCHAR,
  price VARCHAR, is_veg VARCHAR,
  _loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TABLE RAW_USERS (
  user_id VARCHAR, city VARCHAR, signed_up_at VARCHAR,
  _loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TABLE RAW_ORDERS (
  order_id VARCHAR, user_id VARCHAR, restaurant_id VARCHAR, city VARCHAR,
  placed_at VARCHAR, delivered_at VARCHAR, total_amount VARCHAR,
  payment_method VARCHAR,
  _loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TABLE RAW_ORDER_ITEMS (
  order_item_id VARCHAR, order_id VARCHAR, menu_item_id VARCHAR,
  quantity VARCHAR, unit_price VARCHAR,
  _loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TABLE RAW_REVIEWS (
  review_id VARCHAR, order_id VARCHAR, restaurant_id VARCHAR,
  rating VARCHAR, review_text VARCHAR, created_at VARCHAR,
  _loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
