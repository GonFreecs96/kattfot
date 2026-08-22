---
name: funnel-nowcast
description: >
  Cohort-maturation now-cast for the funnel's deep stages. Projects an IMMATURE
  cohort's eventual mature conversion from what it has realized so far, using
  Mack's distribution-free chain ladder over the session-grain spine: the
  cohort x development-week run-off triangle, volume-weighted development
  factors estimated at run time, closed-form MSEP, and lognormal P10/P50/P90
  capped at cohort size. Cohorts too young to have any arrivals fall back to an
  explicit level prior rather than a confident zero. Every output is
  FORECAST-lane (never a governed T2 actual) and --backtest reports empirical
  interval coverage next to nominal. Warehouse queries run as the individual
  human via browser OAuth — never a service account.
version: 0.2.0
---

# Skill: funnel-nowcast

## Purpose

Answer *"this recent cohort can't be measured on Delivered or Return Window
Complete yet — what will its mature rate be?"*, without waiting the full
settle time. This is the forecast complement to `assay:funnel-pulse`:
`funnel-pulse` tells you what already moved on **settled** cohorts;
`funnel-nowcast` projects what a **not-yet-settled** cohort is heading toward,
with intervals whose calibration is *measured* rather than presumed.

The maturity machinery everywhere else in this plugin withholds an immature
rate because presenting it as final is wrong in the flattering direction. This
skill is the governed way back into that blind spot: the projection is
lane-stamped **FORECAST** on every value, carries its interval, and reconciles
to the governed T2 number as the cohort matures.

## Status: implemented (it was an outline through Phase 4 — and why that changed)

This skill shipped as a documented outline for a principled reason: a
now-cast configured by *hand-authored* parameters — a fitted development
curve, bootstrap constants, dispersion floors — would, on this template's
synthetic fixtures, describe the fixture generator while looking exactly like
it described a business.

That argument was right about the design it applied to, and dissolves for the
design that shipped instead. Mack's distribution-free chain ladder (Mack
1993, ASTIN Bulletin 23(2) — the actuarial standard for "a cohort is still
reporting outcomes and I need its eventual total", and the baseline method
the epidemiological nowcasting literature measures newer models against)
**estimates every development factor and every variance from the triangle at
run time**. Nothing in this skill or its spec block is a fitted business
parameter; the `nowcast:` block in the funnel spec carries estimation
controls only (authorized target stages, quantiles, cohort-size floors). On
fixture data the estimated factors describe the generator and the output says
so — the same standing as every other skill's fixture-backed numbers — while
the mechanism is exactly what a real deployment runs.

Two disciplines guard the remaining honesty gaps:

- **The backtest is built in, not aspirational.** `--backtest` replays every
  fully-developed cohort at every development age using only the data that
  was observable at that pseudo as-of date, and reports empirical P10–P90
  coverage next to nominal. The only preregistered real-time comparison of
  nowcasting methods (Wolffram et al. 2023, PLOS Comp Bio) found model-based
  intervals almost universally too narrow; on the committed fixtures this
  skill's own backtest reproduces that finding (~56% empirical vs 80%
  nominal) and emits an `INTERVALS UNDERCOVER` warning saying so. A band that
  measures and reports its own miscalibration is the honest version of this
  method; a band that presumes calibration is the failure mode.
- **A zero is never dressed as a projection.** A stage behind a long pure
  delay (a return window) shows no arrivals for weeks; chain ladder projects
  zero from zero. Cohorts with nothing on the board fall back to an explicit
  **level prior** — the mean and spread of recent mature cohorts' eventual
  rates, labeled `level_prior_too_young` — and cohorts with neither history
  nor arrivals report `unprojectable` rather than a number.

## Invocation

```
/funnel-nowcast project the return-window-complete rate for the open cohorts
/funnel-nowcast nowcast delivered, by channel
/funnel-nowcast return window nowcast for market=uk, with the backtest
```

The helper:

```
python3 plugins/assay/skills/funnel-nowcast/scripts/funnel_nowcast.py \
  [--module checkout] [--spine session] [--stage return_window_complete] \
  [--group-by dim] [--filter col=value ...] [--time-range last_19_weeks] \
  [--today YYYY-MM-DD] [--backtest] [--engine sqlite|bigquery]
```

Reading the committed fixtures, always pass `--today 2026-08-17` (the
observation instant in `data/assay/sample/FIXTURES.md`) and a window inside
the trimmed spine (`--time-range last_19_weeks`).

## The method, concretely

1. **Triangle.** For the target stage, cumulative arrivals per cohort week x
   development week (`C_{i,j}` = arrivals within `j` weeks of the open
   anchor), masked by each cohort's age so a cell exists only once the whole
   cohort has aged through it. Built from two grouped queries (cohort sizes;
   arrival-delay histogram) — never row-level export.
2. **Factors.** Volume-weighted `f_j = ΣC_{i,j+1} / ΣC_{i,j}` over the
   cohorts that cover both cells (optionally only the trailing
   `factor_window_cohorts` — the standard defense against an old regime
   leaking into current factors), with Mack's unbiased `σ²_j` and his
   log-linear extrapolation for a thin tail age.
3. **Projection.** Ultimate = observed x remaining factors; Mack's MSEP
   recursion (process + estimation variance); lognormal quantiles matched by
   moments — then **capped at cohort size** (a funnel has a ceiling claims do
   not; raw chain ladder can breach it) and floored at the observed count.
4. **Fallback.** No arrivals yet → the level prior above. No prior either →
   `unprojectable`.
5. **Backtest** (`--backtest`). Pseudo as-of replays over the settled
   cohorts; per-horizon empirical coverage and median absolute percentage
   error, plus the overall nominal-vs-empirical comparison, in the output and
   in the warnings when the band undercovers.

Composition discipline: with `--group-by`, every group gets its own triangle,
factors, prior, and backtest — a blended development curve drifts with
segment mix (Simpson), so per-segment-then-reaggregate is enforced by
construction, the same discipline `assay:segment-compare` applies.

## Scope

Only stages declared in the spec's `nowcast.stages` are accepted — the deep
stages with a genuine maturation tail (`delivered`, `return_window_complete`
on the checkout spine). A fast-settling stage is refused: its governed actual
exists within days, and a forecast of a knowable number is a downgrade
wearing a confidence band. The development horizon derives from the stage's
calibrated `stage_maturity` lag, so the projection covers exactly the
maturity blind spot and hands off to governed actuals beyond it.

## Auth model

Same as every spine-backed skill: on `ASSAY_ENGINE=bigquery` the query runs
as the individual human via Application Default Credentials (service
accounts refused in code, exit 4); on the default `sqlite` engine the
committed fixtures serve with `fixture_backed: true`, which every downstream
renderer turns into a MOCK DATA watermark.

## Output artifact

Written by the agent from the helper's JSON to
`<cwd>/outputs/assay/{YYYY-MM-DD}-funnel-nowcast[-{slug}].md`, stamped
**FORECAST** in the frontmatter (`accuracy_tier: FORECAST` — the lane *is*
the tier here) and on every projected value. Include: the per-cohort table
(observed, method, P50, P10–P90), the development factors with their
stability read, the level prior when used, the backtest coverage table when
run, and the standing caveats below. Never register a now-cast as a governed
metric.

## Standing caveats (repeat in every artifact)

- **FORECAST, not actual.** Reconciles to the governed T2 number at maturity;
  never quoted as a settled figure, and the interval travels with the point.
- **Stability is an assumption with a name.** Mack assumption 1 (factors
  depend on age, not cohort) is violated by mid-maturation regime changes — a
  carrier slowdown, a returns-policy change. The `FACTOR INSTABILITY` warning
  and the guardrails register's dated cutovers are the guard; a detected
  regime change should truncate `factor_window_cohorts` rather than be
  averaged into the factors.
- **Model-based intervals run narrow** (Wolffram et al. 2023 — and this
  skill's own fixture backtest). Treat the band as optimistic unless the
  backtest shows otherwise for your book; the next honest upgrade is
  inflating to the backtest's horizon-specific error quantiles
  (conformal-style), which the output already gives you the numbers for.
- **Per-segment, then re-aggregate** — never quote a blended now-cast across
  a shifting mix.
- **Fixture runs describe the generator.** Watermarked accordingly; nothing
  about a fixture run calibrates this skill for any business.

## Out of scope

- Causal verdicts on releases — that is `assay:release-scorecard`'s outlined
  lane (BSTS/DiD), which must consume matured or now-casted series, never raw
  recent rates.
- Individual or cart-level rows (R5) and PII columns (R6).
- Posting anywhere; hand the artifact to `assay:export-report` or
  `assay:publish` yourself.

## Refusals

Same voice as every spine skill: name the rule and the offending input, write
no artifact. Off-list group-by → R2; off-list filter → R3; PII → R6 (no
override); a stage outside `nowcast.stages` → the allowed-targets refusal; a
missing `nowcast:` block or missing calibrated lag → a named configuration
refusal pointing at the spec.
