---
skill: explore
accuracy_tier: T3
question: "Of confirmed orders, what share is ultimately returned, by channel?"
surfaces: ["analytics.cart_checkout_funnel_weekly"]
source_mode: fixture_sql
source_project: ${KATTFOT_BIGQUERY_PROJECT}
engine: sqlite
fixture_backed: true
generated_at: 2026-08-30T20:21:09
manifest:
  plugin_version: 0.2.0
  register_sha: 9ad622eaf6017db3
  query_sha: eb7ee243c191e874
  result_sha: b91935cca84f0f7d
---

# Exploration — Of confirmed orders, what share is ultimately returned, by channel?

> **EXPLORATORY (T3) — NOT DECISION-GRADE.** Ad-hoc result: no reviewed
> definition, no named owner, no promise it means the same thing next week.

> ⚠️ **FIXTURE DATA** — these numbers come from committed synthetic
> fixtures, not from a warehouse. They describe a seeded random process.

**Surfaces:** analytics.cart_checkout_funnel_weekly   **Rows:** 76

## Results

| cohort_week | channel | confirmed_to_returned_rate | numerator | denominator |
| --- | --- | --- | --- | --- |
| 2026-04-06 | marketplace | 0.06666666666666667 | 3 | 45 |
| 2026-04-06 | mobile_app | 0.08974358974358974 | 14 | 156 |
| 2026-04-06 | social | 0.0 | 0 | 27 |
| 2026-04-06 | web | 0.12264150943396226 | 39 | 318 |
| 2026-04-13 | marketplace | 0.06557377049180328 | 4 | 61 |
| 2026-04-13 | mobile_app | 0.10714285714285714 | 18 | 168 |
| 2026-04-13 | social | 0.038461538461538464 | 1 | 26 |
| 2026-04-13 | web | 0.10884353741496598 | 32 | 294 |
| 2026-04-20 | marketplace | 0.13636363636363635 | 6 | 44 |
| 2026-04-20 | mobile_app | 0.05945945945945946 | 11 | 185 |
| 2026-04-20 | social | 0.2608695652173913 | 6 | 23 |
| 2026-04-20 | web | 0.12080536912751678 | 36 | 298 |
| 2026-04-27 | marketplace | 0.09433962264150944 | 5 | 53 |
| 2026-04-27 | mobile_app | 0.08 | 12 | 150 |
| 2026-04-27 | social | 0.09090909090909091 | 3 | 33 |
| 2026-04-27 | web | 0.10355029585798817 | 35 | 338 |
| 2026-05-04 | marketplace | 0.11475409836065574 | 7 | 61 |
| 2026-05-04 | mobile_app | 0.09090909090909091 | 16 | 176 |
| 2026-05-04 | social | 0.05263157894736842 | 1 | 19 |
| 2026-05-04 | web | 0.11075949367088607 | 35 | 316 |
| 2026-05-11 | marketplace | 0.05405405405405406 | 4 | 74 |
| 2026-05-11 | mobile_app | 0.12 | 21 | 175 |
| 2026-05-11 | social | 0.12 | 3 | 25 |
| 2026-05-11 | web | 0.09627329192546584 | 31 | 322 |
| 2026-05-18 | marketplace | 0.13846153846153847 | 9 | 65 |
| 2026-05-18 | mobile_app | 0.11875 | 19 | 160 |
| 2026-05-18 | social | 0.03571428571428571 | 1 | 28 |
| 2026-05-18 | web | 0.13846153846153847 | 36 | 260 |
| 2026-05-25 | marketplace | 0.09302325581395349 | 4 | 43 |
| 2026-05-25 | mobile_app | 0.11842105263157894 | 18 | 152 |
| 2026-05-25 | social | 0.0 | 0 | 21 |
| 2026-05-25 | web | 0.13120567375886524 | 37 | 282 |
| 2026-06-01 | marketplace | 0.11290322580645161 | 7 | 62 |
| 2026-06-01 | mobile_app | 0.052980132450331126 | 8 | 151 |
| 2026-06-01 | social | 0.21739130434782608 | 5 | 23 |
| 2026-06-01 | web | 0.08012820512820513 | 25 | 312 |
| 2026-06-08 | marketplace | 0.08888888888888889 | 4 | 45 |
| 2026-06-08 | mobile_app | 0.13513513513513514 | 20 | 148 |
| 2026-06-08 | social | 0.05405405405405406 | 2 | 37 |
| 2026-06-08 | web | 0.09120521172638436 | 28 | 307 |
| 2026-06-15 | marketplace | 0.15789473684210525 | 9 | 57 |
| 2026-06-15 | mobile_app | 0.08522727272727272 | 15 | 176 |
| 2026-06-15 | social | 0.18181818181818182 | 4 | 22 |
| 2026-06-15 | web | 0.11688311688311688 | 36 | 308 |
| 2026-06-22 | marketplace | 0.1111111111111111 | 8 | 72 |
| 2026-06-22 | mobile_app | 0.08176100628930817 | 13 | 159 |
| 2026-06-22 | social | 0.07142857142857142 | 2 | 28 |
| 2026-06-22 | web | 0.12654320987654322 | 41 | 324 |
| 2026-06-29 | marketplace | 0.0967741935483871 | 6 | 62 |
| 2026-06-29 | mobile_app | 0.06626506024096386 | 11 | 166 |
| 2026-06-29 | social | 0.07407407407407407 | 2 | 27 |
| 2026-06-29 | web | 0.11384615384615385 | 37 | 325 |
| 2026-07-06 | marketplace | 0.10714285714285714 | 6 | 56 |
| 2026-07-06 | mobile_app | 0.07103825136612021 | 13 | 183 |
| 2026-07-06 | social | 0.038461538461538464 | 1 | 26 |
| 2026-07-06 | web | 0.096875 | 31 | 320 |
| 2026-07-13 | marketplace | 0.08955223880597014 | 6 | 67 |
| 2026-07-13 | mobile_app | 0.10650887573964497 | 18 | 169 |
| 2026-07-13 | social | 0.14814814814814814 | 4 | 27 |
| 2026-07-13 | web | 0.10557184750733138 | 36 | 341 |
| 2026-07-20 | marketplace | 0.15873015873015872 | 10 | 63 |
| 2026-07-20 | mobile_app | 0.10596026490066225 | 16 | 151 |
| 2026-07-20 | social | 0.03225806451612903 | 1 | 31 |
| 2026-07-20 | web | 0.09686609686609686 | 34 | 351 |
| 2026-07-27 | marketplace | 0.11594202898550725 | 8 | 69 |
| 2026-07-27 | mobile_app | 0.045714285714285714 | 8 | 175 |
| 2026-07-27 | social | 0.10714285714285714 | 3 | 28 |
| 2026-07-27 | web | 0.06857142857142857 | 24 | 350 |
| 2026-08-03 | marketplace | 0.06779661016949153 | 4 | 59 |
| 2026-08-03 | mobile_app | 0.03389830508474576 | 6 | 177 |
| 2026-08-03 | social | 0.038461538461538464 | 1 | 26 |
| 2026-08-03 | web | 0.0375 | 12 | 320 |
| 2026-08-10 | marketplace | 0.0 | 0 | 83 |
| 2026-08-10 | mobile_app | 0.004629629629629629 | 1 | 216 |
| 2026-08-10 | social | 0.0 | 0 | 27 |
| 2026-08-10 | web | 0.0032679738562091504 | 1 | 306 |

## Caveats

- EXPLORATORY (T3): this is an ad-hoc result — no reviewed definition, no named owner, and no promise it means the same thing next week. Not decision-grade. If this question recurs, promote it to a governed T2 metric (kattfot:promote).
- FIXTURE DATA: source mode is fixture_sql — KATTFOT_ENGINE=sqlite — the SQL is running against the committed fixture CSVs loaded into in-memory sqlite. These numbers come from committed synthetic fixtures and describe a seeded random process, not any real business. Every downstream rendering must carry the watermark.
- CAVEAT (weekly_aggregate_is_a_narrower_interface): This view is pre-summed at cohort_week x channel x payment_method x market x category x checkout_version. It cannot express a percentile, a terminal-branch decomposition, or "did ANY of this customer's carts reach X" — those need the session spine. That narrowness is deliberate: R5 (no individual-level data) is easier to honour on a surface with no individuals on it.

## Provenance

Executed SQL (canonical BigQuery dialect):

```sql
SELECT cohort_week, channel, SAFE_DIVIDE(SUM(orders_returned), SUM(orders_confirmed)) AS confirmed_to_returned_rate, SUM(orders_returned) AS numerator, SUM(orders_confirmed) AS denominator FROM `${KATTFOT_BIGQUERY_PROJECT}.analytics.cart_checkout_funnel_weekly` WHERE cohort_week >= @start_week GROUP BY cohort_week, channel ORDER BY cohort_week, channel
```

Bound parameters:

```json
{"start_week": "2026-04-06"}
```
