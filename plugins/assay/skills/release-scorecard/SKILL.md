---
name: release-scorecard
description: >
  OUTLINED, NOT IMPLEMENTED — see the status banner below. Pre/post release
  success scorecard for checkout launches. Would accept a pre-registered
  hypothesis artifact, run a governed pre/post segment-compare read, attach
  immature-cohort now-cast context, and issue a causal verdict via a BSTS
  counterfactual (always) plus difference-in-differences when a control
  segment is specified. FORECAST lane for the causal and now-cast outputs;
  descriptive pre/post would be shown alongside for transparency.
version: 0.1.0
---

# Skill: release-scorecard

## ⚠️ Status: outlined, not implemented

**This skill ships as a documented outline only.** There is no helper script
behind it — `scripts/scorecard_stub.py` in this directory exits immediately
with a clear, non-zero-exit refusal (see [Status in this template](#status-in-this-template)).
No pre-registration is validated, no BSTS or DiD model is fit, and no artifact
carrying the name `release-scorecard` is ever written.
`plugins/assay/config/modules.yaml` records this exclusion against the
`checkout/session` spine, and `config/checkout-funnel-spec.yaml` deliberately
omits the `scorecard:` block this skill would read — both say why, and both
are checked by `tests/test_module_registry.py`.

**What implementing it for real would need:**
- **Dependencies.** `numpy` and `scipy` for the estimators generally, plus
  `statsmodels` specifically for the structural time-series (BSTS-style)
  counterfactual and the panel difference-in-differences regression. All
  three offline.
- **This skill's prerequisite is `assay:funnel-nowcast`**, itself outlined
  and not implemented (see that skill's SKILL.md). A release verdict on a
  deep stage (Delivered, Return Window Complete) needs now-cast context for
  any cohort still inside its settle window, or the "post" side of a recent
  launch is silently biased toward whatever has settled fastest.
- **A `scorecard:` block** in `config/checkout-funnel-spec.yaml` — minimum
  pre/post window weeks, a minimum weekly-denominator floor below which an
  estimator is withheld, and BSTS prior/seed defaults. Deliberately absent
  today for the same reason `nowcast:` is: these are judgment calls tuned
  against a real book's variance, not facts derivable from a schema.
- **A validated pre-registration format** — this outline ports the concept
  (see `templates/pre-registration.template.yaml` and
  `templates/pre-registration.schema.json` in this directory) but no code
  here parses or validates an instance of it yet.
- **A causal-estimator library** — the BSTS local-level counterfactual and
  the DiD panel regression are genuinely nontrivial statistics, not glue code;
  budget for them as their own reviewed component, not a corner of this
  skill's helper script.

## Purpose (what this skill would answer)

Answer *"did the release we shipped move the funnel the way we said it
would?"* — with a **causal verdict**, not a descriptive delta alone. In a
working deployment this would compose:

1. **Pre-registration** — hypothesis, primary metric, treatment segment,
   launch date, expected direction, and an optional control segment for DiD,
   captured **before** the release ships (see [Pre-registration](#pre-registration-the-part-that-has-to-happen-before-you-ship)).
2. **`assay:segment-compare` pre/post** — the real, running skill that gives
   the governed descriptive funnel / leak-mix / timing A|B read across the
   launch date. This part could be composed from an existing skill today.
3. **`assay:funnel-nowcast`** — FORECAST context for any deep-stage cohort in
   the post window that has not settled yet. Also outlined, not implemented.
4. **A BSTS-style counterfactual** on the primary metric's weekly series —
   would run always, regardless of whether a control segment is given.
5. **Difference-in-differences** — would run only when a `control_segment` is
   present in the pre-registration.

**Why a descriptive delta is not enough:** `assay:segment-compare` already
computes a pre/post A|B delta today, and its own SKILL.md is explicit that it
makes **no causal claim** — seasonality, channel mix, and whatever else
shipped in the same window are all inside that number. A release scorecard
exists specifically to *not* stop there. Shipping this skill with the
descriptive read alone and calling it a "scorecard" would quietly launder a
`segment-compare` delta into something that reads as a verdict — which is a
worse outcome than not shipping it at all.

## Pre-registration (the part that has to happen *before* you ship)

Copy `templates/pre-registration.template.yaml`, fill every field, and
validate it against `templates/pre-registration.schema.json` before the
release ships. The schema pins:

- `release.id` / `release.label` — a stable slug and a human name.
- `hypothesis` — what you expect to change, and the mechanism, in prose.
- `primary_metric` — either `stage_cumulative` (a funnel stage from
  `checkout-funnel-spec.yaml`'s ladder: `checkout_started` … through
  `return_window_complete`) or `nowcast_target` (one of `funnel-nowcast`'s
  targets, for a deep stage that will not have settled by review time).
- `segment` — the treatment segment, as allow-listed filters only.
- `control_segment` (optional) — a segment that did **not** receive the
  change; its presence is what turns DiD on.
- `evaluation` — launch date, minimum pre/post window weeks.
- `expected_direction` — `increase` / `decrease` / `neutral`.

Pre-registering the hypothesis and the expected direction *before* the data
is in is what makes the verdict a test rather than a post-hoc story fitted to
whatever the data happened to show.

## Would-be verdict criteria

| Verdict | When |
| --- | --- |
| `supports_hypothesis` | BSTS 95% CI excludes 0 **and** the lift's sign matches `expected_direction` |
| `contrary` | CI excludes 0 but the lift opposes `expected_direction` |
| `mixed` | BSTS supports the hypothesis but a significant DiD disagrees on sign |
| `inconclusive` | BSTS CI includes 0, or a pre/post precondition (minimum window, minimum weekly denominator) fails |

DiD would be **confirmatory** when a control segment is present; its absence
skips DiD with a documented reason, and BSTS alone drives the verdict. A
descriptive-only verdict is explicitly not an acceptable output — the whole
reason this skill is distinct from `assay:segment-compare` is the causal
step.

## Would-be invocation

```
assay:release-scorecard run scorecard for pre-reg outputs/assay/checkout-v3-cta.pre-reg.yaml
assay:release-scorecard pre-register a launch using the template
```

Today, either of these returns the refusal in
[Status in this template](#status-in-this-template).

## Would-be auth model

Same as every other spine-backed skill: individual human identity via ADC on
`ASSAY_ENGINE=bigquery`; the committed fixtures with `fixture_backed: true`
on the default `sqlite` engine.

## Would-be flow (for the implementer)

1. **Validate the pre-registration** against the schema; a validation failure
   refuses before touching the warehouse.
2. **Run `segment-compare` in pre/post mode** over the treatment segment (and
   the control segment, if given), split at `launch_date`.
3. **Run `funnel-nowcast`** for the primary metric if it targets a deep stage
   and the post window has not fully settled.
4. **Fit the BSTS counterfactual** on the primary metric's weekly series —
   pre-period as training, post-period compared against the model's
   counterfactual projection.
5. **Fit DiD** if `control_segment` is present.
6. **Apply the verdict table** above and write the artifact to
   `<cwd>/outputs/assay/{YYYY-MM-DD}-release-scorecard-{release.id}.md`,
   stamping **FORECAST** on the causal and now-cast sections.

## Would-be output format

```markdown
---
skill: release-scorecard
accuracy_tier: FORECAST
release_id: {id}
launch_date: {launch_date}
generated_at: {iso}
---

# Release scorecard — {label}

**Pre-reg:** `{pre_reg_path}` · **Launch:** {launch_date}
**Verdict:** {verdict} ({basis})

## Causal lift (BSTS) — FORECAST

| | Point lift (pp) | 95% CI (pp) | Pre weeks | Post weeks |
| --- | --- | --- | --- | --- |
| Primary metric | {lift_pp} | {ci_lo}–{ci_hi} | {pre} | {post} |

## DiD (if control specified)
…

## Descriptive pre/post (segment-compare)
…

## Now-cast context (immature cohorts) — FORECAST
…

## Narrative
…

## Caveats
…
```

## Would-be standing caveats

- **FORECAST causal lane.** BSTS/DiD outputs are model-based and would
  reconcile to governed T2 as cohorts mature — never presented as a settled
  number.
- **BSTS assumes stable pre-period dynamics.** A concurrent shock in the
  pre-period (a feed cutover, a mix shift) can bias the counterfactual; a
  real implementation would cross-reference `config/guardrails.yaml`'s
  `feed_cutovers` register and caveat any that fall inside the pre-period.
- **DiD needs a genuinely untreated control.** Parallel-trends is not tested
  automatically; picking a control that received a correlated change quietly
  breaks the whole method.
- **Low volume withholds the estimator, not just the number.** A weekly
  series below the (currently unauthored) `scorecard.min_weekly_denom` floor
  would report `inconclusive` rather than a noisy point estimate.
- **Deep-stage post windows lean on now-cast, which is itself FORECAST.** Two
  layers of interval-bearing estimate compound; say so rather than presenting
  a now-cast-fed verdict with the same confidence as one built entirely on
  settled cohorts.

## Refusals

Pre-registration schema-validation failure, an allow-list violation in
`segment` / `control_segment`, or any helper failure → refuse with the rule
and the offending input; write no artifact.

## Out of scope (even once implemented)

- Any multi-release portfolio forecast — a single scorecard is one release,
  one hypothesis.
- Posting to a wiki or chat — hand the artifact to `assay:publish` (also
  outlined) or distribute it yourself.
- Customer-grain propensity or targeting — this is a funnel-level verdict,
  not a per-customer model.

## Status in this template

There is no `release_scorecard.py`, no BSTS estimator, and no DiD estimator
behind this skill. `scripts/scorecard_stub.py` in this directory is the only
thing that runs:

```
$ python3 plugins/assay/skills/release-scorecard/scripts/scorecard_stub.py
release-scorecard is not implemented in this template.
See SKILL.md in this directory for the method, its scope, and exactly
what implementing it would require.
$ echo $?
9
```

The pre-registration **format** is real — `templates/pre-registration.template.yaml`
and `templates/pre-registration.schema.json` are complete and would validate
an instance today if something called a JSON Schema validator against them —
but nothing in this repo does that yet, and no verdict logic exists to
consume a validated instance.
