---
name: funnel-nowcast
description: >
  OUTLINED, NOT IMPLEMENTED — see the status banner below. Cohort-maturation
  now-cast for the checkout funnel's deep stages. Would project an IMMATURE
  cohort's eventual MATURE conversion from what it has realized so far, using a
  development-factor / chain-ladder estimator (familiar from actuarial loss
  development): r_hat = r_obs(t) / f_s(t), with P10/P50/P90 intervals combining
  the cohort-to-cohort spread of development factors with the eventual-rate
  level dispersion. Scoped to the two stages that genuinely withhold a tail —
  Delivered (~14d) and Return Window Complete (~44d). Every output would be
  FORECAST (exploratory lane), would carry intervals, and would reconcile to
  governed T2 as cohorts mature.
version: 0.1.0
---

# Skill: funnel-nowcast

## ⚠️ Status: outlined, not implemented

**This skill ships as a documented outline only.** There is no helper script
behind it — `scripts/funnel_nowcast.py` in this directory exits immediately
with a clear, non-zero-exit refusal (see [Status in this template](#status-in-this-template)).
Nothing here computes a number, and no artifact carrying the name
`funnel-nowcast` is ever written. `plugins/assay/config/modules.yaml` records
this exclusion against the `checkout/session` spine with the same reason given
below, so the registry and this file cannot drift out of agreement.

**Why outline it rather than build it, or drop it:** this is the one skill in
the set whose *reason for existing* is a real limitation of the fixture data,
not a scoping shortcut — see [Why this is not a stub away from working](#why-this-is-not-a-stub-away-from-working).
Read that section before assuming a few hours of scripting would close the
gap.

**What implementing it for real would need:**
- **Dependencies.** `numpy` for the bootstrap and array work, `scipy.stats`
  for the interval quadrature, no `statsmodels` needed here (that is
  `release-scorecard`'s dependency, for BSTS). All three offline — no network
  calls, matching the rest of this template's "runs on a fresh clone" promise.
- **A `nowcast:` block** in `config/checkout-funnel-spec.yaml` — target stage
  pairs, the age grid in days, bootstrap sample count and seed, and a
  `level_dispersion_min_denom` floor (see [The method](#the-method-development-factor--chain-ladder)).
  `checkout-funnel-spec.yaml` currently omits this block **on purpose**, with
  a comment explaining why (search it for `nowcast:`).
- **Judgment calls calibrated against a real book.** The development curve
  `f_s(t)`, the bootstrap sample count, and the level-dispersion floor are not
  facts you derive from a schema — they are choices tuned against how *your*
  cohorts actually mature. Fitting them to this repo's synthetic generator
  would produce a curve that describes the generator, not any business (see
  below).
- **A backtest harness** (`scripts/backtest.py` in the private original) that
  holds out mature cohorts, now-casts them at several ages, and reports MAPE
  and P10–P90 interval coverage — the check that stops a plausible-looking
  band from being a wrong one. No now-cast should be trusted before this has
  run once per target/segment.

## Purpose (what this skill would answer)

Answer *"this recent cohort can't be measured on Delivered or Return Window
Complete yet — what will its mature rate be?"*, without waiting the full
settle time. In a working deployment this would be the **keystone forecast
model**: it removes the maturity blind spot for the deep stages and is the
natural prerequisite for a leading read on a recent release
(`assay:release-scorecard`, itself also outlined) and for any roadmap-level
forward projection.

It would be the forecast complement to `assay:funnel-pulse`, which is real and
running today: `funnel-pulse` tells you what already moved on **settled**
cohorts; `funnel-nowcast` would project what a **not-yet-settled** cohort is
heading toward, with honest intervals. Until it exists, the honest answer to
"what will this week's cohort do" is: wait for `funnel-pulse` to see it once
it is mature, or read `assay:stage-timing` for how long that wait typically
is.

## Why this is not a stub away from working

Three other skills in this repository (`funnel-dropoff`, `stage-timing`,
`segment-compare`, `funnel-pulse`) run today, offline, against the committed
fixtures, and produce real numbers. This one is different in kind, not just
in effort:

The chain-ladder estimator below needs a **development curve** — the shape of
how a cohort's realized rate climbs toward its eventual rate as it ages — and
that shape is a property of **real customer behavior**, not of the funnel's
schema. `scripts/generate_fixtures.py` draws each cart's stage timestamps from
a seeded random process with a hand-authored delay distribution
(`stage_maturity.lag_days` in `checkout-funnel-spec.yaml`, explicitly flagged
`illustrative: true` / `calibrated_at: null`). Fitting a development curve to
that generator would not be wrong the way a bug is wrong — it would fit
perfectly, produce a beautiful P10/P50/P90 band, and describe **the random
number generator's own delay distribution** rather than anything a shopper
did. A reader has no way to tell that artifact-fit curve apart from a real one
by looking at the output; the intervals would look exactly as calibrated
either way. That is a worse failure mode than refusing outright, which is why
`config/modules.yaml` excludes this skill with a reason rather than shipping
a script that runs cleanly and lies quietly.

## The method (development-factor / chain-ladder)

For a target stage `s` and segment, estimate the **development curve**
`f_s(t)` = fraction of the cohort's *eventual* conversions realized by cohort
age `t` (days since the cart was created), from fully-mature cohorts. Given an
immature cohort observed at age `t` with realized rate `r_obs(t)`, project:

```
r_hat = r_obs(t) / f_s(t)            (development factor  λ_s(t) = 1 / f_s(t))
```

This is the chain-ladder method from actuarial loss development, applied to a
checkout funnel instead of an insurance claims triangle: both problems are
"a cohort is still reporting outcomes, and I need its eventual total before
it has finished."

Intervals would combine **two independent uncertainty sources** in
quadrature:

1. **Maturation** — the cohort-to-cohort spread of the development factor
   (bootstrap the mature cohorts → distribution of `f_s(t)` → P10/P50/P90 on
   `r_hat`). Dominates at young ages and widens as `t` shrinks (a younger
   cohort carries less information).
2. **Level** — the cohort-to-cohort spread of the *eventual* mature rate
   itself (reported as `level_sd` in the output). This is the honest floor
   that does **not** vanish as `f_s(t) → 1`. Without it, the maturation
   spread collapses toward zero width on **fast-saturating** segments (every
   mature cohort is fully developed by the read age) and on **thin** segments
   (too few cohorts to bootstrap a spread) — so the band would look confident
   while badly under-covering. The level term needs a `min_denom` gate with a
   pooled-binomial-SE floor, the same shape as `nowcast.level_dispersion_min_denom`
   would be in the (currently absent) spec block.

The calibrated per-stage settle lag (`stage_maturity.lag_days` in the spec) is
already a known point on `f_s(t)` — it is roughly `f_s(lag_days) ≈ 1.0` — so a
real implementation would extend in-production calibration rather than
estimating a curve from zero.

## Scope: Delivered and Return Window Complete only

The fast stages (Checkout Started ~0d, Payment Decisioned ~2d, Order
Confirmed ~3d) settle within days — read those on governed actuals
(`assay:query-metric` / `assay:stage-timing`), never here. This skill would
exist only for the two stages with a real tail:

| Target | From | To | Illustrative settle lag |
| --- | --- | --- | --- |
| `start_to_delivered` | Cart Created | Delivered | ~14d |
| `shipped_to_delivered` | Shipped | Delivered | ~7d (shipping leg only) |
| `start_to_return_window_complete` | Cart Created | Return Window Complete | ~44d — **the keystone target**, since this is the stage the return-window structure exists to make hard |

("Illustrative" because `checkout-funnel-spec.yaml`'s `stage_maturity` block
carries `illustrative: true`, `calibrated_at: null` — these lags are
hand-authored to be plausible, not measured off a real book. A real
implementation regenerates them, and this skill's development curves, from
observed data.)

## Composition matters

Now-cast **per segment**, then re-aggregate over governed numerator/denominator
counts — a blended curve drifts with segment mix (Simpson's paradox). Any
future helper would need to enforce per-segment computation whenever
`--group-by` is set, the same discipline `assay:segment-compare` already
applies to its A/B reads.

## Would-be invocation

```
assay:funnel-nowcast project start->delivered for the last 6 immature cohorts
assay:funnel-nowcast nowcast start->return-window-complete, by channel
assay:funnel-nowcast shipped->delivered nowcast for market=uk
```

Today, running any of these returns the refusal in
[Status in this template](#status-in-this-template) — no cohort, target, or
segment parsing happens.

## Would-be auth model

Same as every other spine-backed skill: individual human identity via
Application Default Credentials on `ASSAY_ENGINE=bigquery` (service accounts
refused in code, exit 4); the committed fixtures with `fixture_backed: true`
on the default `sqlite` engine. This part needs no new design — it would
reuse `_shared/identity.py` and `_shared/spine.py` unchanged.

## Would-be flow (for the implementer)

1. **Load the spec.** Read the `nowcast:` block (`targets`, `age_grid_days`,
   `bootstrap_samples`, `bootstrap_seed`, `level_dispersion_min_denom`) — once
   it exists — and the `stage_maturity` table, both from
   `config/checkout-funnel-spec.yaml` via `config/modules.yaml`.
2. **Parse the question** — target stage pair, segment filters (`col=value`
   against `allowed_filters`), an optional single group-by dimension (against
   `allowed_dimensions`), and the mature-curve estimation window.
3. **Refuse before computing** — group-by not allow-listed → R2; filter
   column not allow-listed → R3; PII → R6, no override; unknown target → list
   the configured target keys, same voice as every other spine skill's
   allow-list refusal.
4. **Run the (not-yet-written) helper**, `scripts/funnel_nowcast.py`, over the
   session-grain spine, bootstrapping the development curve from mature
   cohorts and applying it to each immature cohort in scope.
5. **Write the artifact** to
   `<cwd>/outputs/assay/{YYYY-MM-DD}-funnel-nowcast-{target}-{slug}.md`,
   stamped **FORECAST** in the header and on every projected value — never as
   a governed T2 actual.
6. **Backtest before trusting it.** The first time a target/segment is
   now-cast, run `scripts/backtest.py` and report MAPE + interval coverage. A
   now-cast whose P10–P90 band does not cover mature truth at roughly 80% is
   not decision-grade, and the artifact should say so rather than presenting
   the band as calibrated by default.

## Would-be output format

```markdown
---
skill: funnel-nowcast
accuracy_tier: FORECAST   # exploratory lane — reconciles to T2 at maturity
target: {target}
source_view: {source_project}.{source_view}
generated_at: {iso}
maturity_stage: {stage}  ({lag_days}d illustrative settle)
---

# Funnel now-cast (FORECAST) — {target_label}{ , by <dim>}

**Lane:** FORECAST (exploratory) — projected, interval-bearing, would reconcile
to governed T2 as cohorts mature. **Not** a governed actual.
**Source:** `{source_project}.{source_view}` (session-grain spine)
**Target stage:** {stage} (illustrative settle ~{lag_days}d) · curve from
mature cohorts {curve_window}

## Projected mature rate by immature cohort{ , per <dim>}

| {dim?} | Cohort week | Age (d) | Realized so far | f(t) | Now-cast P50 | P10–P90 | n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| … | {week} | {t} | {r_obs} | {f} | **{p50}** | {p10}–{p90} | {n} |

## Development curve f(t)

| {dim?} | t=7 | t=14 | t=28 | t=44 | settle |
| --- | --- | --- | --- | --- | --- |

## Narrative
[Lead with the headline projection + interval for the most recent cohort(s).
State the curve window and n. Flag any cohort whose r_obs(t) falls outside the
historical development band.]

## Caveats
[The standing caveats below.]

## Provenance
- **SQL:** (parameterized, from the helper)
- **Spec:** `config/checkout-funnel-spec.yaml` (`nowcast:` block)
- **Backtest:** {MAPE / coverage if run}
```

## Would-be standing caveats

- **FORECAST, not actual.** Would reconcile to the governed T2 number at
  maturity; a now-cast is never quoted as a settled figure.
- **Intervals widen for young cohorts.** A cohort observed at 7d on a 44d
  settle stage carries little information — a wide P10–P90 there is the
  honest signal, not a defect.
- **Development curve assumes a stable maturation shape.** A genuine
  mid-maturation behavior change (a carrier slowdown, a policy change on
  returns) can masquerade as a development anomaly, and vice versa — the
  anomaly flag and the backtest calibration are the guard, not a substitute
  for watching both.
- **Per-segment then re-aggregate**, always — a blended now-cast drifts with
  segment mix.
- **Discontinuities in-window.** `config/guardrails.yaml`'s `feed_cutovers`
  register carries dated discontinuities for this book — `checkout_v2_rollout`
  (2025-05-12) and `return_window_extended_to_30d` (2025-11-03) are the two
  most likely to fall inside a curve-estimation window and bias a development
  curve fit across them. A real implementation would carry these as
  intervention terms rather than silently fitting one curve across a step
  change.
- **Fixture-backed runs are synthetic**, and for this skill specifically, a
  now-cast run against the committed fixtures would be fitting the generator's
  own hand-authored delay distribution — see
  [Why this is not a stub away from working](#why-this-is-not-a-stub-away-from-working).

## Out of scope (even once implemented)

- A state-space / BSTS forward model for the multi-week-ahead forecast — that
  belongs to `assay:release-scorecard`'s causal lane, not here.
- Posting anywhere. The artifact is the deliverable; hand it to
  `assay:publish` or `assay:export-report` yourself.
- Individual or cart-level rows (R5) and PII columns (R6).
- Registering any output as a governed metric — now-casts are FORECAST
  artifacts, never T2.

## Refusals

Defer to `query-metric/doctrine/accuracy-tiers.md` for the R2/R3/R5/R6 voice.
State the rule and the offending input; write no artifact on a refusal.

## Status in this template

There is no `funnel_nowcast.py` behind this skill. `scripts/nowcast_stub.py`
in this directory is the only thing that runs — it prints the refusal below
to stderr and exits non-zero, so invoking this skill fails loudly rather than
silently returning nothing or, worse, a plausible-looking number:

```
$ python3 plugins/assay/skills/funnel-nowcast/scripts/nowcast_stub.py
funnel-nowcast is not implemented in this template.
See SKILL.md in this directory for the method, its scope, and exactly
what implementing it would require.
$ echo $?
9
```

`config/modules.yaml` excludes `funnel-nowcast` on the `checkout/session`
spine with the matching reason, and `checkout-funnel-spec.yaml` deliberately
omits the `nowcast:` block this skill would read — both are checked by
`tests/test_module_registry.py`, so this outline cannot silently drift into
claiming more than it does.
