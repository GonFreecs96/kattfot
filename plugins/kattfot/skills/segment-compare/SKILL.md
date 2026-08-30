---
name: segment-compare
description: >
  Spine-backed A/B comparison of two checkout segments across the full funnel, the
  terminal-branch leak mix, and stage timing — presented as A | B | delta. Two
  modes: filter-compare (Segment A vs Segment B, e.g. card_instant vs
  bank_transfer_review within one channel) and pre/post (one segment split at a
  ship date — "did the checkout change move the funnel?"). Descriptive only, no
  significance test, no causal claim. Aggregate-only, allow-listed, per-user
  identity on the warehouse path. T2.
version: 0.1.0
---

# Segment compare

You are running the `segment-compare` skill. The question is comparative — "A vs
B", or "before vs after" — and the answer is three tables of deltas:

- **Funnel** — cumulative-from-start conversion at each stage, A | B | Δpp.
- **Leak mix** — terminal-branch share, A | B | Δpp, biggest shift first.
- **Timing** — the headline percentile per transition, A | B | Δ.

Two modes:

- **filter-compare** — `--a-filter …` versus `--b-filter …`, optionally narrowed by
  a shared `--filter`, over one window. "Within the web channel, card versus bank
  transfer."
- **pre/post** — one segment split at `--split-date` into pre (`cohort_week <
  date`) and post (`cohort_week >= date`). "Did the change ship on the 1st move
  the funnel?"

Use `funnel-dropoff` or `stage-timing` for a single segment; use this when the
question has two sides.

## Why this is a skill and not two runs of funnel-dropoff

Both segments are resolved against **one** `today`, so every stage and transition
is gated to the same mature window on both sides. Two separate invocations minutes
apart can straddle a Monday and silently compare different cohort windows — and the
resulting delta looks exactly like a real movement. One `today`, one cutoff set, one
comparison.

## What this skill will not claim

**No significance test, no confidence interval, no causal verdict.** A pre/post
delta is a description of two windows. Seasonality, channel mix, a promotion, and
the change you are asking about all moved in between, and nothing here separates
them. Say "the funnel is 2.1pp lower after the split date"; do not say the change
caused it. The registry marks `release-scorecard` — the skill that *would* make a
causal claim — as excluded precisely because doing it honestly needs parameters
calibrated against a real book.

## The pre/post maturity trap

This is the one thing to get right when narrating a recent change.

Each stage is gated to its own mature cohort. In a *recent* post window, a deep
stage (Delivered, Return Window Complete) may have **little or no settled cohort
behind it** — so it reads as a small `n`, not as a low rate. That is correct
withholding, not a regression.

The right reading: lean on the stages that mature in days (checkout start, payment
submit, decision) for a recent change, and come back for the deep stages once the
post cohort settles. Check each side's `n` before reporting any deep-stage delta.
`--maturity flag` puts both sides on a single deepest cohort, which makes the deep
stages comparable and the shallow ones stale — a trade, not a fix.

## Auth model

Individual human identity via ADC on `KATTFOT_ENGINE=bigquery` (service accounts
refused in code, exit 4); the committed fixtures with `fixture_backed: true` on the
default `sqlite` engine.

## Flow

### Step 1 — Load the spec
`stages`, `transitions`, `terminal_branches`, and the allow-lists come from the
module's funnel spec via `config/modules.yaml`.

### Step 2 — Pick the mode
- A date or a ship event in the question → **pre/post**: `--split-date YYYY-MM-DD`
  plus a shared `--filter` naming the segment.
- Two named things → **filter-compare**: `--a-filter` / `--b-filter`, plus an
  optional shared `--filter` for "within X".
- Both, or neither → the helper refuses. That is deliberate: doing both at once
  would move a filter and a window together, and the delta could not be attributed
  to either.

Optional: `--a-label` / `--b-label` (label the sides in human terms — the artifact
is easier to read as "Card" and "Bank transfer" than as raw filter strings),
`--transitions`, `--percentiles` (default `50`, the first is the headline),
`--time-range`, `--maturity`, `--include-legacy`.

### Step 3 — Refuse before querying
Any filter column not in `allowed_filters` → **R3**. Individual-level → **R5**.
PII → **R6**, no override. Ambiguous mode → refuse.

### Step 4 — Run the helper

```bash
# filter-compare
python3 plugins/kattfot/skills/segment-compare/scripts/segment_compare.py \
  --filter channel=web \
  --a-filter payment_method=card_instant \
  --b-filter payment_method=bank_transfer_review \
  --time-range last_16_weeks --today 2026-08-17

# pre/post
python3 plugins/kattfot/skills/segment-compare/scripts/segment_compare.py \
  --filter channel=mobile_app \
  --split-date 2026-06-01 --time-range 2026-03-02:2026-08-10 --today 2026-08-17
```

Returns JSON: `segment_a` / `segment_b` (each with `started`, `deep_started`,
`stages` with `mature_through`, `branches`, `timing`, `branch_coherent`, and its
own SQL), plus `comparison` (stage Δpp, branch Δpp sorted by magnitude, timing Δ at
the headline percentile), `maturity`, `maturity_cutoff`, `legacy`,
`percentile_basis`, and `warnings`.

### Step 5 — Write the artifact
Render the markdown below to
`<cwd>/outputs/kattfot/{YYYY-MM-DD}-segment-compare-{slug}.md`. Tell the user the
path. Lead the narrative with the end-to-end delta and both sides' `n`.

## Output format

```markdown
---
skill: segment-compare
mode: {filter_compare | pre_post}
accuracy_tier: {tier}
source_view: {source_project}.{source_view}
engine: {engine}
fixture_backed: {true|false}
generated_at: {iso}
maturity_mode: {stage|flag|off}
maturity_cutoff: {maturity.deep_cohort_through}
segment_a: {label}
segment_b: {label}
split_date: {date or null}
---

# Segment compare — {A} vs {B}

**Accuracy tier:** {tier} (operational — not regulator-facing)
**Source view:** `{source_project}.{source_view}` (session-grain spine)
**A:** {label} · n={started_a} · {window_a}
**B:** {label} · n={started_b} · {window_b}
**Maturity:** {mode}, per-stage cutoffs · {legacy.detail}

## Funnel (cumulative from start)

| Stage | A | B | Δ (pp) | Mature through |
| --- | --- | --- | --- | --- |
| Cart Created | 100% | 100% | — | {week} |
| … | …% | …% | +/−… | {week} |

## Where the leaks shifted (terminal branch)

| Terminal branch | A | B | Δ (pp) |
| --- | --- | --- | --- |
| {biggest shift first} | …% | …% | +/−… |

## Timing (p{N})

| Transition | A | B | Δ | unit | A n | B n |
| --- | --- | --- | --- | --- | --- | --- |
| … | … | … | +/−… | min | … | … |

## Narrative
[Two or three paragraphs: the end-to-end delta with both n, where the leak mix
moved, any timing shift. For pre/post, state the maturity caveat explicitly. Deltas
are descriptive — no causal language.]

## Caveats
[The standing caveats below.]

## Provenance
- **Per-segment SQL** (parameterized, from the helper)
- **Percentile basis:** {basis}
- **Spec:** `config/{spec file}`
```

## Standing caveats (always include)

- **Deltas are descriptive, not significance-tested.** No confidence interval is
  computed. Treat a small Δpp on a small `n` as noise, and cite both sides' `n`.
- **No causal claim.** A pre/post delta is two windows described, not an effect
  measured. Anything that moved in between is inside the number.
- **pre/post maturity.** A deep stage in a recent post window may have little or no
  settled cohort behind it, which reads as a low `n`, not a low rate. Lean on the
  stages that mature in days for a recent change.
- **Mix is not controlled for.** The comparison normalises to rates, but if A and B
  differ in channel or category mix, some of the delta is that mix. Segment further
  if it matters.
- **Asymmetric windows.** In pre/post the two sides can differ in length and
  seasonality; in filter-compare they can differ enormously in size.
- **Flag-based stages are not strictly nested**, so a stage rate can read
  marginally above the previous one. Read cumulative-from-start.
- **The maturity lags are hand-authored and illustrative, not measured.**
- **Fixture-backed runs are synthetic** when `fixture_backed` is true.

## Refusals

Defer to `query-metric/doctrine/accuracy-tiers.md` for the R3/R5/R6 voice. State
the rule and the offending input; write no artifact on a refusal.

## Out of scope

- More than two segments in one call — run it pairwise.
- Individual or cart-level rows (R5) and PII columns (R6).
- A causal verdict, a significance test, or a now-cast of an immature post window.
- Columns or transitions outside the spec.
