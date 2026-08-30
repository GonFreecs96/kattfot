---
skill: funnel-dropoff
accuracy_tier: T2
source_view: ${KATTFOT_BIGQUERY_PROJECT}.analytics.cart_checkout_session_spine
engine: sqlite
fixture_backed: true
generated_at: 2026-08-19T10:18:10
maturity_mode: stage
maturity_cutoff: 2026-06-22
---

# funnel-dropoff — fixture-backed demonstration

> ⚠️ **FIXTURE DATA** — these numbers come from committed synthetic fixtures,
> not from a warehouse. They describe a seeded random process.

**Engine:** sqlite · **Maturity:** stage, settled through 2026-06-22

## Stage funnel

| Stage | Count | Stage conv. | Cumulative | Mature through |
| --- | --- | --- | --- | --- |
| Cart Created | 19,007 | n/a | 100.00% | 2026-08-10 |
| Checkout Started | 13,199 | 69.44% | 69.44% | 2026-08-10 |
| Payment Submitted | 10,343 | 78.36% | 54.42% | 2026-08-10 |
| Payment Decisioned | 9,384 | 97.53% | 52.95% | 2026-08-03 |
| Order Confirmed | 8,553 | 91.14% | 48.26% | 2026-08-03 |
| Shipped | 8,188 | 95.73% | 46.21% | 2026-08-03 |
| Delivered | 7,535 | 98.77% | 45.80% | 2026-07-27 |
| Return Window Complete | 4,207 | 88.91% | 40.60% | 2026-06-22 |

## Where it leaks (terminal branch)

Over the single settled cohort through 2026-06-22; branches sum to 10,362 carts created.

| Terminal branch | Count | % of carts |
| --- | --- | --- |
| 8 Return Window Complete | 4,207 | 40.60% |
| 7r Delivered then Returned | 524 | 5.06% |
| 7 Delivered (Return Window Open) | 1 | 0.01% |
| 6 Shipped (In Transit) | 51 | 0.49% |
| 5x Cancelled after Confirmation | 68 | 0.66% |
| 5 Confirmed not Shipped (Out of Stock) | 133 | 1.28% |
| 4d Payment Declined | 397 | 3.83% |
| 4f Fraud / Identity Check Failed | 81 | 0.78% |
| 3z Stale (Post-Submit) | 61 | 0.59% |
| 3 Payment Submitted, Undecided | 75 | 0.72% |
| 2 Checkout Started, No Payment | 1,538 | 14.84% |
| 1z Stale (Pre-Checkout) | 3,226 | 31.13% |

## Run warnings

- FIXTURE DATA: this read ran on the sqlite adapter against the committed synthetic fixtures. The numbers describe a seeded random process, not any real business; every downstream chart, dashboard, and PDF is watermarked accordingly.
- MATURITY: each stage is gated to the cohort weeks mature FOR THAT STAGE; the deepest fully-settled cohort is 2026-06-22, which is what the terminal-branch mix and any single-cohort funnel use. The lags themselves are hand-authored and illustrative, NOT measured — a real deployment regenerates the stage_maturity table by measuring open-anchored settle time on the spine.
- DISCONTINUITY: the 2026-04-27..2026-08-10 window crosses 2026-06-01 (delivery scan feed switched providers). Ship-to-deliver timestamps come from a different carrier feed after this date, with finer scan granularity. Delivered COUNTS are unaffected; ship-to-deliver percentiles shift by roughly half a day for feed reasons alone. Read a timing trend that crosses this date as two segments.
- CAVEAT (spine_timestamps_are_utc_and_cohorts_are_iso_monday): Stage timestamps are UTC and `cohort_week` is the ISO-Monday week the cart was CREATED — a cart keeps that cohort forever, even if it confirms three weeks later. A same-day comparison against a local-timezone dashboard will differ by up to a day at the boundary; that is the single most common reconciliation cause (see `kattfot:reconcile`, cause `TIMEZONE`).

## About this artifact

Composed by `scripts/demo_skills.py`, which scripts a deliberately plain
version of the narrative step so the artifact contract is checkable. In normal
use the agent writes that section against the question actually asked — a
script cannot know what was asked.
