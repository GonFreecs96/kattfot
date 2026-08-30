---
name: funnel-pulse
description: >
  Spine-backed recent-vs-baseline funnel regression monitor. Scans every value of
  one dimension (channel by default) and flags stage-conversion and terminal-branch
  shifts between a recent window and the baseline before it, ranked by impact
  (carts × delta). Stage-aware: each stage is monitored over its OWN mature window
  (cart-to-checkout ~0d lag → return window ~44d), so an early-funnel regression
  surfaces within days while the deep stages stay settled. Emits a digest artifact
  and never posts anywhere. Aggregate-only, allow-listed, per-user identity on the
  warehouse path. T2.
version: 0.1.0
---

# Funnel pulse

You are running the `funnel-pulse` skill. This is the **"what moved?"** monitor: for
each value of one dimension it compares a recent window against the baseline window
immediately before it and flags what shifted, ranked by impact (carts affected ×
Δpp).

It is the broad sweep that tells you where to point the other three skills. It
produces a digest artifact and **nothing else** — see the boundaries at the bottom.

## ⚠️ Stage-aware windows (read this before reading any number)

Each stage is compared over its **own** recent and baseline windows, lagged by that
stage's settle time:

- **checkout started, payment submitted** (~0–1 days): the recent window ends at
  the last complete week, so a cart-abandonment regression shows up within days.
- **shipped, delivered** (~7–14 days): lagged accordingly.
- **return window complete** (~44 days): the recent window is lagged that far,
  because you genuinely cannot measure it on last week's carts.

So an early-stage drop is **not** withheld for the six weeks the deep stages need —
which is what a single mature-cohort gate does, and why an early regression can sit
undetected for a month and a half under one.

This remains a **cohort monitor, not a within-day alarm.** Every window is a settled
cohort for its own stage. `--maturity flag` collapses everything onto the deepest
cohort's window; `--no-mature` is not available, and the helper says so rather than
silently running ungated — two unsettled windows compared against each other measure
the tail arriving, not a change.

## Per-group maturity overrides

Where a stage's settle time varies by the dimension being monitored, that group is
compared over its own longer window. The live example: `payment_decisioned` is
dominated book-wide by instant card authorization that decides in seconds, so the
default 2-day lag is badly wrong for a bank transfer routed through a clearing cycle
and a review queue. When `--by payment_method` matches the override, those groups get
their own windows — applied to the **denominator as well as the numerator**, or the
rate would be measured over two different populations.

Rows carrying `lag_override: true` are deliberately **not** on the same window as
their neighbours, which is why the output reports a per-row `mature_through` rather
than one window per stage. Say so in the digest; a reader comparing two rows with
different windows and no notice will read a window difference as a behaviour
difference.

## Why the monitor refuses rather than degrading

Windows are built through `spine.resolve_stage_cutoffs()`, which **hard refuses**
(exit 5) when a stage declares a `maturity_stage` with no calibrated lag. The
convenient alternative returns `None` for a missing lag, which here would produce an
ungated window — a monitor comparing two unsettled windows, reporting shifts that are
just the tail arriving, and saying nothing about it. A monitor running ungated
without announcing it is worse than one that will not start.

## Auth model

Individual human identity via ADC on `KATTFOT_ENGINE=bigquery` (service accounts
refused in code, exit 4); the committed fixtures with `fixture_backed: true` on the
default `sqlite` engine. Unattended-friendly: deterministic, no prompts, and
`--today` pins the window.

## Flow

### Step 1 — Load the spec
The helper reads the `pulse:` block (`by`, `recent_weeks`, `baseline_weeks`,
`min_started`, `flag_threshold_pp`), the stage ladder, and the terminal-branch
order. A spec with no `pulse:` block and no explicit `--by` is a clean refusal.

### Step 2 — Parse optional overrides
- `--by <dim>` — the monitoring dimension (default: the spec's `pulse.by`).
- `--filter col=value` — scope the sweep, e.g. `market=us`.
- `--recent-weeks` / `--baseline-weeks` / `--min-started` / `--threshold-pp`.
- `--today YYYY-MM-DD` — deterministic window, for tests and fixture reads.

### Step 3 — Refuse before querying
`--by` not in `allowed_dimensions` → **R2**. A filter column not in
`allowed_filters` → **R3**. Individual-level → **R5**. PII → **R6**, no override.

### Step 4 — Run the helper

```bash
python3 plugins/kattfot/skills/funnel-pulse/scripts/funnel_pulse.py \
  [--by channel] [--filter market=us] \
  [--recent-weeks 4] [--baseline-weeks 8] \
  [--min-started 200] [--threshold-pp 3.0] \
  [--maturity stage|flag] [--today YYYY-MM-DD] [--engine sqlite|bigquery]
```

Against the committed fixtures pass `--today 2026-08-17`. Note that the committed
spine is trimmed to 19 cohort weeks — exactly the span the default 4-week recent
plus 8-week baseline windows need at the deepest stage's lag — so a wider
`--baseline-weeks` offline will silently read a truncated baseline. Regrow the full
spine (`generate_fixtures.py --spine-weeks 0`) if you need more history.

Returns JSON: a `maturity` block with `per_stage` recent/baseline windows and any
`group_overrides`; `windows` (the freshest and deepest headline windows);
`thresholds`; `scanned` / `qualified` / `skipped_low_n`; `flags[]` sorted by impact
(`grp`, `kind` stage|branch, `name`, baseline → recent rate, `delta_pp`,
`direction`, `recent_n`, `impact`, `lag_override`); and `flows[]` (per-group
end-to-end plus every stage delta, each carrying its own `mature_through`).

### Step 5 — Write the digest artifact
Render the markdown below to `<cwd>/outputs/kattfot/{YYYY-MM-DD}-funnel-pulse.md`.
**Lead with regressions**, ranked by impact. Tell the user the path.

### Step 6 — Offer distribution, do not perform it
This skill never posts. Offer: "I can hand this artifact to `kattfot:export-report`
for a PDF, or you can publish it yourself — nothing is sent anywhere without you
doing it."

## Scheduling

To run this weekly, wire the helper into whatever scheduler you already use and
leave `--today` **unset** in the scheduled run, so each stage always uses its latest
mature window. Scheduling is set up explicitly by a human; this skill does not
self-schedule.

## Output format

```markdown
---
skill: funnel-pulse
accuracy_tier: {tier}
source_view: {source_project}.{source_view}
engine: {engine}
fixture_backed: {true|false}
generated_at: {iso}
by: {dimension}
maturity_mode: {stage|flag}
maturity_cutoff: {windows.deepest.recent.end}
freshest_recent_window: {start}..{end}
deepest_recent_window: {start}..{end}
---

# Funnel pulse — by {dimension} (stage-aware: recent vs baseline)

**Accuracy tier:** {tier} (operational — not regulator-facing)
**Source:** `{source_project}.{source_view}` · stage-aware maturity
**Freshest stage recent:** {windows.freshest.recent} (lag {n}d) ·
**Deepest stage recent:** {windows.deepest.recent} (lag {n}d)
**Thresholds:** flag ≥ {pp}pp · min carts {min_started} · scanned {n}, qualified
{n}, skipped (low n) {n}
**Maturity:** {legacy.detail}

Each stage below is compared over its own mature window. Rows marked *(override)*
are on a different window from their neighbours by design.

## 🚩 Flags (ranked by impact)

| {by} | What | Baseline → Recent | Δ (pp) | Direction | recent n |
| --- | --- | --- | --- | --- | --- |
| {regressions first} | … | …% → …% | +/−… | regression/improvement | … |

## End-to-end by {by} (cart → return window complete)

| {by} | Baseline | Recent | Δ (pp) | recent n |
| --- | --- | --- | --- | --- |
| … | …% | …% | +/−… | … |

## Skipped (below the volume floor)

| {by} | recent n | baseline n |
| --- | --- | --- |

## Narrative
[Lead with the biggest regressions and the group to investigate next. Note any
routing-shift artifacts (see caveats). Point at funnel-dropoff / stage-timing /
segment-compare for the deep dive.]

## Caveats
[The standing caveats below.]

## Provenance
- **Stage SQL / Branch SQL** (parameterized, from the helper)
- **Spec:** `config/{spec file}` (`pulse:` block)
```

## Standing caveats (always include)

- **Stage-aware windows.** Each stage's windows are lagged by its own settle time,
  so a fresh-week regression appears almost immediately while a deep-stage shift
  cannot surface until that stage settles. Read every stage against its own
  *Mature through* week.
- **Per-group overrides mean neighbouring rows are on different windows.** Rows
  flagged `lag_override` are compared over their own longer window, on both sides of
  the rate. That is the point, and it must be stated or a window difference reads as
  a behaviour difference.
- **Direction is a heuristic for intermediate stages.** A fall in Payment
  Decisioned paired with a rise in Order Confirmed is usually a routing or mix
  shift, not a regression. The end-to-end delta is the unambiguous signal; treat
  intermediate flags as pointers.
- **Terminal-branch direction** is derived from the spec: rising into the
  best-listed branch is an improvement, rising into any other exit is a regression.
- **Deltas are descriptive, not significance-tested.** Groups below `min_started`
  are listed under skipped rather than flagged on noise.
- **The maturity lags are hand-authored and illustrative, not measured.**
- **Fixture-backed runs are synthetic** when `fixture_backed` is true, and the
  committed spine is trimmed to 19 cohort weeks — enough for the default windows and
  not more.

## Refusals

Defer to `query-metric/doctrine/accuracy-tiers.md` (R2/R3/R5/R6). State the rule and
the offending input; write no artifact on a refusal. A missing calibrated lag is a
refusal too, not a degraded run.

## Out of scope

- **Posting anywhere.** No Slack, no wiki, no email, no webhook. The artifact is the
  deliverable; a human distributes it.
- Self-scheduling.
- Timing-regression flags — this version monitors conversion and branch mix only.
  Use `stage-timing` or `segment-compare` for latency shifts.
- Individual or cart-level rows (R5) and PII columns (R6).
- Any causal attribution for a flagged move.
