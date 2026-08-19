-- ============================================================================
-- analytics.cart_checkout_funnel_weekly
-- ----------------------------------------------------------------------------
-- The pre-aggregated weekly view every registered metric in
-- metrics/checkout/ reads. One row per
--
--     cohort_week x channel x payment_method x market x category x checkout_version
--
-- with one count column per funnel stage. Built directly on the session spine, so
-- the two views are two projections of the SAME stage ladder by construction
-- rather than by convention.
--
-- Why a second view rather than querying the spine
-- ------------------------------------------------
-- Not performance. It is a deliberately NARROWER interface: a governed ratio
-- metric wants pre-summed counts at a fixed grain, and a surface with no
-- individual rows on it makes R5 (no individual-level data) structurally easy to
-- honour instead of merely policed. Drop-off attribution and percentile timing
-- need the spine and are served by different skills.
--
-- Grain     : one row per cohort cell. Cells with zero carts do not appear, so a
--             thin slice is absent rather than zero — which is why the metrics
--             render a NULL rate as "-" and always show the denominator.
-- Maturity  : NOT applied here. Each metric applies its OWN stage's cutoff, so
--             one view serves an early-funnel metric at last week's freshness and
--             a return-window metric at six weeks' lag. Baking a single cutoff in
--             would force every metric to the slowest stage's freshness.
-- Window    : the current partial ISO week is excluded. A partially-counted week
--             would drag every weekly rate down for no reason a reader could see.
--
-- PII       : none. Aggregates only, plus the same aggregate geography and
--             enumerated categoricals the spine carries.
-- Dialect   : BigQuery. See cart_checkout_session_spine.sql on the offline path.
--
-- Note what is NOT carried: `is_first_order` exists on the spine but is not a
-- dimension here. Including it would double the cell count and halve the volume
-- behind every rate, for a slice no registered ratio needs. If a metric comes to
-- need it, add it here — do not widen a metric's allow-list to a column this view
-- does not have.
-- ============================================================================
CREATE OR REPLACE VIEW `${WAREHOUSE_PROJECT}.analytics.cart_checkout_funnel_weekly` AS
SELECT
  cohort_week,
  channel,
  payment_method,
  market,
  category,
  checkout_version,

  -- ============ stage counts ============
  -- Each is the COUNTIF of the corresponding spine flag, named for what it counts
  -- rather than for the flag, because these are the numerator/denominator columns
  -- a metric definition names.
  COUNTIF(cart_created_flag)              AS carts_created,
  COUNTIF(checkout_started_flag)          AS checkouts_started,
  COUNTIF(payment_submitted_flag)         AS payments_submitted,
  COUNTIF(payment_decisioned_flag)        AS payments_decisioned,
  COUNTIF(payment_approved_flag)          AS payments_approved,
  COUNTIF(payment_declined_flag)          AS payments_declined,
  -- Stopped before any decision, so present here and absent from
  -- payments_decisioned. See the spine DDL's note on why that separation matters.
  COUNTIF(fraud_failed_flag)              AS fraud_failed,
  COUNTIF(order_confirmed_flag)           AS orders_confirmed,
  -- The out-of-stock leak, defined by an ABSENCE: paid for and still not shipped.
  -- Because it is an absence, it is meaningless without the `shipped` maturity
  -- cutoff — an order confirmed yesterday has not shipped yet and is not a leak.
  COUNTIF(order_confirmed_flag AND NOT shipped_flag)
                                          AS orders_confirmed_not_shipped,
  COUNTIF(shipped_flag)                   AS orders_shipped,
  COUNTIF(delivered_flag)                 AS orders_delivered,
  COUNTIF(returned_flag)                  AS orders_returned,
  COUNTIF(return_window_complete_flag)    AS return_windows_complete,
  COUNTIF(cart_stale_flag)                AS carts_stale

FROM `${WAREHOUSE_PROJECT}.analytics.cart_checkout_session_spine`
WHERE cohort_week < DATE_TRUNC(CURRENT_DATE(), WEEK(MONDAY))   -- drop the partial week
GROUP BY
  cohort_week,
  channel,
  payment_method,
  market,
  category,
  checkout_version
