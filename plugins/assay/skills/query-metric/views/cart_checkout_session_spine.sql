-- ============================================================================
-- analytics.cart_checkout_session_spine
-- ----------------------------------------------------------------------------
-- The checkout funnel SPINE: one row per CART CREATED.
--
-- This is the instance-grain view the spine-backed skills read. It carries every
-- stage flag, every stage timestamp, the curated terminal-branch classification,
-- and the deepest-stage-reached ordinal — everything needed to answer "where did
-- each cart end up" and "how long did each transition take", neither of which can
-- be recovered from the pre-aggregated weekly view.
--
-- Grain     : one row per checkout_session_id (carts with a non-null creation).
-- Maturity  : NOT filtered here. Recency is EXPOSED via is_mature_45d and
--             days_since_created; consumers apply per-stage cutoffs from
--             config/checkout-funnel-spec.yaml. A view that pre-filtered would
--             force every consumer to the deepest stage's freshness.
-- Window    : all-time. The retired `v1-legacy` checkout cohort is present and
--             will dominate un-windowed comparisons — filter
--             checkout_version != 'v1-legacy' for active-book analysis, which is
--             what the funnel spec's legacy_exclude_column/value pair does by
--             default.
--
-- PII       : none. Identity is carried only as opaque surrogate keys
--             (checkout_session_id, customer_token, order_id) and aggregate
--             geography (market). tests/test_view_ddl_pii.py scans this file
--             against the R6 denylist in _shared/pii.py and fails pytest on any
--             match (this repo has no CI; you must run pytest yourself). That is
--             deliberate defense at the data layer: an allow-list
--             above a view that exposes a personal column is moot.
--
-- Dialect   : BigQuery. This is the documented production adapter. The offline
--             path does not run this DDL — it loads the fixture CSVs into sqlite
--             directly (see scripts/generate_fixtures.py and
--             _shared/adapters/sqlite_adapter.py), because reproducing a
--             CREATE VIEW over source events would mean shipping a synthetic
--             event stream too.
--
-- Substitute ${WAREHOUSE_PROJECT}, ${SOURCE_DATASET}, and the source table names
-- for your own. The stage semantics are what this file is really documenting;
-- the joins are illustrative of a normal shape (a session table, a payment
-- table, an order table, a fulfilment table) and will not match your schema.
--
-- See views/checkout-views.md for the full data dictionary and caveats.
-- ============================================================================
CREATE OR REPLACE VIEW `${WAREHOUSE_PROJECT}.analytics.cart_checkout_session_spine` AS
WITH
  sess AS (
    SELECT
      checkout_session_id,
      -- Opaque surrogate key for a person. Allowed under R6 as a tokenized join
      -- key; the personal columns it stands in for are never carried here.
      customer_token,
      channel,
      market,
      checkout_version,
      is_first_order,
      cart_created_at,
      checkout_started_at
    FROM `${WAREHOUSE_PROJECT}.${SOURCE_DATASET}.checkout_sessions`
    WHERE cart_created_at IS NOT NULL          -- = a cart ever existed
  ),
  pay AS (
    SELECT
      checkout_session_id,
      payment_method,
      payment_submitted_at,
      payment_decisioned_at,
      payment_decision,                        -- 'approved' | 'declined'
      fraud_check_result,                       -- 'pass' | 'fail' | NULL (not run)
      payment_review_state                      -- 'resolved' | 'pending' | NULL
    FROM `${WAREHOUSE_PROJECT}.${SOURCE_DATASET}.payment_attempts`
    -- At most one attempt is carried per session: the LAST one, so a retry that
    -- succeeded is what the funnel sees. This is the choice that makes the
    -- decline rate a measure of final outcome rather than of attempt count; the
    -- cost is that the retry itself becomes invisible, which the
    -- payment_decline_rate metric states as a caveat.
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY checkout_session_id
      ORDER BY payment_submitted_at DESC
    ) = 1
  ),
  ord AS (
    SELECT
      checkout_session_id,
      order_id,
      category,
      order_confirmed_at,
      cancelled_at
    FROM `${WAREHOUSE_PROJECT}.${SOURCE_DATASET}.orders`
    WHERE checkout_session_id IS NOT NULL
  ),
  ship AS (
    SELECT
      order_id,
      shipped_at,
      -- Split shipments collapse to the LAST delivery: an order is delivered when
      -- ALL of it has arrived, so it is never reported settled before all of it
      -- can be returned.
      MAX(delivered_at)   AS delivered_at,
      MIN(shipped_at)     AS first_shipped_at
    FROM `${WAREHOUSE_PROJECT}.${SOURCE_DATASET}.shipments`
    GROUP BY order_id, shipped_at
  ),
  ret AS (
    SELECT
      order_id,
      MIN(return_initiated_at) AS return_initiated_at
    FROM `${WAREHOUSE_PROJECT}.${SOURCE_DATASET}.returns`
    GROUP BY order_id
  )

SELECT
  -- ============ keys & dimensions ============
  s.checkout_session_id,
  s.customer_token,
  o.order_id,
  COALESCE(NULLIF(s.channel, ''), 'UNSPECIFIED')          AS channel,
  COALESCE(NULLIF(p.payment_method, ''), 'UNSPECIFIED')   AS payment_method,
  COALESCE(NULLIF(s.market, ''), 'UNSPECIFIED')           AS market,
  COALESCE(NULLIF(o.category, ''), 'UNSPECIFIED')         AS category,
  COALESCE(NULLIF(s.checkout_version, ''), 'UNSPECIFIED') AS checkout_version,
  COALESCE(s.is_first_order, FALSE)                       AS is_first_order,

  -- ============ cohort / maturity ============
  -- The cart's CREATION week, kept forever: a cart created in week W that confirms
  -- three weeks later still counts in W. This is what makes weekly conversion
  -- comparable, and also what makes recent cohorts genuinely incomplete.
  DATE_TRUNC(DATE(s.cart_created_at), WEEK(MONDAY))        AS cohort_week,
  DATE_TRUNC(DATE(s.cart_created_at), MONTH)              AS cohort_month,
  (s.cart_created_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 45 DAY))
                                                          AS is_mature_45d,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), s.cart_created_at, DAY)
                                                          AS days_since_created,

  -- ============ stage flags ============
  -- Each is the boolean the funnel spec's `stages:` ladder names. COALESCE to
  -- FALSE throughout: every downstream table is LEFT JOINed, so an absent row
  -- would otherwise surface as NULL and be counted as neither reached nor not.
  TRUE                                                    AS cart_created_flag,
  (s.checkout_started_at IS NOT NULL)                     AS checkout_started_flag,
  (p.payment_submitted_at IS NOT NULL)                    AS payment_submitted_flag,
  (p.payment_decisioned_at IS NOT NULL)                   AS payment_decisioned_flag,
  COALESCE(p.payment_decision = 'approved', FALSE)        AS payment_approved_flag,
  COALESCE(p.payment_decision = 'declined', FALSE)        AS payment_declined_flag,
  -- A fraud failure stops the attempt BEFORE any decision, so it is in the
  -- submitted population and absent from the decisioned one. Collapsing the two
  -- is the most common way a payment funnel misleads.
  COALESCE(p.fraud_check_result = 'fail', FALSE)          AS fraud_failed_flag,
  (o.order_confirmed_at IS NOT NULL)                      AS order_confirmed_flag,
  (sh.first_shipped_at IS NOT NULL)                       AS shipped_flag,
  (sh.delivered_at IS NOT NULL)                           AS delivered_flag,
  (r.return_initiated_at IS NOT NULL)                     AS returned_flag,

  -- ============ the return window ============
  -- Anchored on DELIVERY, not on the order date: a slow carrier pushes the whole
  -- settle point out. This is why the deepest stage's maturity lag is ~44d
  -- (delivery tail + 30d window) rather than 30d.
  TIMESTAMP_ADD(sh.delivered_at, INTERVAL 30 DAY)         AS return_window_ends_at,
  -- Kept: delivered, the window has closed, and no return was initiated.
  (sh.delivered_at IS NOT NULL
    AND TIMESTAMP_ADD(sh.delivered_at, INTERVAL 30 DAY) < CURRENT_TIMESTAMP()
    AND r.return_initiated_at IS NULL)                    AS return_window_complete_flag,

  -- ============ abandonment ============
  -- Definitional, not observed: there is no abandonment event, only the absence of
  -- progress past a 30-day convention. Change the threshold and the metric moves
  -- with no change in behavior — which is why the stale maturity lag is exactly 30.
  (p.payment_decisioned_at IS NULL
    AND s.cart_created_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY))
                                                          AS cart_stale_flag,

  -- ============ curated terminal branch ============
  -- The single "where did it end" bucket, evaluated DEEPEST-STAGE-FIRST so each
  -- cart lands in exactly one. This is what makes drop-off attribution possible:
  -- of all carts created, the share that ended in each branch. Raw flags stay
  -- exposed above for analyses that would rather not use these buckets.
  CASE
    WHEN sh.delivered_at IS NOT NULL
         AND TIMESTAMP_ADD(sh.delivered_at, INTERVAL 30 DAY) < CURRENT_TIMESTAMP()
         AND r.return_initiated_at IS NULL          THEN '8 Return Window Complete'
    WHEN r.return_initiated_at IS NOT NULL          THEN '7r Delivered then Returned'
    WHEN sh.delivered_at IS NOT NULL                THEN '7 Delivered (Return Window Open)'
    WHEN sh.first_shipped_at IS NOT NULL            THEN '6 Shipped (In Transit)'
    WHEN o.cancelled_at IS NOT NULL                 THEN '5x Cancelled after Confirmation'
    WHEN o.order_confirmed_at IS NOT NULL           THEN '5 Confirmed not Shipped (Out of Stock)'
    WHEN p.payment_decision = 'declined'            THEN '4d Payment Declined'
    WHEN p.fraud_check_result = 'fail'              THEN '4f Fraud / Identity Check Failed'
    WHEN p.payment_submitted_at IS NOT NULL
         AND p.payment_decisioned_at IS NULL
         AND s.cart_created_at
             < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
                                                    THEN '3z Stale (Post-Submit)'
    WHEN p.payment_submitted_at IS NOT NULL
         AND p.payment_review_state = 'pending'     THEN '3r Payment Under Review (Unresolved)'
    WHEN p.payment_submitted_at IS NOT NULL         THEN '3 Payment Submitted, Undecided'
    WHEN s.checkout_started_at IS NOT NULL          THEN '2 Checkout Started, No Payment'
    WHEN s.cart_created_at
         < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
                                                    THEN '1z Stale (Pre-Checkout)'
    ELSE                                                 '1 Cart Abandoned'
  END                                                     AS terminal_branch,

  -- Deepest stage reached, as an ordinal — for drop-off curves.
  CASE
    WHEN sh.delivered_at IS NOT NULL
         AND TIMESTAMP_ADD(sh.delivered_at, INTERVAL 30 DAY) < CURRENT_TIMESTAMP()
         AND r.return_initiated_at IS NULL      THEN 8
    WHEN sh.delivered_at IS NOT NULL            THEN 7
    WHEN sh.first_shipped_at IS NOT NULL        THEN 6
    WHEN o.order_confirmed_at IS NOT NULL       THEN 5
    WHEN p.payment_decisioned_at IS NOT NULL    THEN 4
    WHEN p.payment_submitted_at IS NOT NULL     THEN 3
    WHEN s.checkout_started_at IS NOT NULL      THEN 2
    ELSE 1
  END                                                     AS funnel_stage_reached,

  -- ============ stage timestamps ============
  -- The `transitions:` endpoints in the funnel spec. A row where either endpoint
  -- of a transition is NULL is excluded from that transition's percentiles, so
  -- each statistic covers the population that actually reached both points.
  s.cart_created_at,
  s.checkout_started_at,
  p.payment_submitted_at,
  p.payment_decisioned_at,
  o.order_confirmed_at,
  sh.first_shipped_at                                     AS shipped_at,
  sh.delivered_at,
  r.return_initiated_at,
  o.cancelled_at,

  -- ============ durations (minutes for fast stages, days for slow) ============
  TIMESTAMP_DIFF(s.checkout_started_at,   s.cart_created_at,       MINUTE) AS mins_cart_to_checkout,
  TIMESTAMP_DIFF(p.payment_submitted_at,  s.checkout_started_at,   MINUTE) AS mins_checkout_to_payment,
  TIMESTAMP_DIFF(p.payment_decisioned_at, p.payment_submitted_at,  MINUTE) AS mins_payment_to_decision,
  TIMESTAMP_DIFF(sh.first_shipped_at,     o.order_confirmed_at,    MINUTE) AS mins_confirm_to_ship,
  TIMESTAMP_DIFF(sh.delivered_at,         sh.first_shipped_at,     DAY)    AS days_ship_to_deliver,
  TIMESTAMP_DIFF(r.return_initiated_at,   sh.delivered_at,         DAY)    AS days_deliver_to_return

FROM sess s
LEFT JOIN pay  p  USING (checkout_session_id)
LEFT JOIN ord  o  USING (checkout_session_id)
LEFT JOIN ship sh ON sh.order_id = o.order_id
LEFT JOIN ret  r  ON r.order_id  = o.order_id
