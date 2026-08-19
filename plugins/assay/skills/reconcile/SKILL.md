---
name: reconcile
description: >
  Triggers on: "reconcile this number", "why doesn't this match", "my dashboard says
  X but assay says Y", "explain the difference between", "these numbers disagree",
  "why is the conversion rate different in the BI tool". Explains WHY a governed
  metric differs from an external number (a dashboard, a spreadsheet, an older
  report) by re-running the metric under one tweaked convention at a time until one
  reproduces their number — or reporting honestly that none does.
version: 0.1.0
---

# Reconcile

You are running the `reconcile` skill. Someone has a number that disagrees with the
governed one ("the dashboard says 81%, assay says 84%"). Your job is to **explain the
gap** — attribute it to a specific, named cause — not merely to confirm that the two
differ.

The mechanism mirrors a valuation engine's equivalence harness: compute the governed
number, then re-run it under one tweaked convention at a time (a different window,
the maturity cutoff, a timezone shift, a filter, a grain) and find the one that
**reproduces their number**. If none does, say so. An attributed cause that was not
reproduced is worse than an admitted gap, because it ends the investigation.

## Gather inputs first — guessing wastes re-runs

Each hypothesis is one more query, so the inputs you collect up front directly reduce
the work:

- **Their value** (required) — as a rate, e.g. `0.81`.
- **The metric** (required) — which governed metric this should correspond to.
- **Our window** (required) — the window the governed number should cover.
- **Their window** (if known) — the single most common cause, by a wide margin.
- **Their filters / segment** — any slice they applied that we did not, or vice
  versa.
- **Their grain** — if they may have measured something else entirely (carts versus
  customers), the sibling metric name.
- **Their source** — if it is an older assay artifact, its file path. That enables an
  exact, offline definition-drift check that needs no re-run at all.

If the user has not given their window or their filters, **ask**.

## Run

```bash
python3 plugins/assay/skills/reconcile/scripts/reconcile_number.py \
  --metric <name> --their-value <float> --time-range <ours> \
  [--their-time-range <theirs>] [--filter col=value ...] \
  [--their-filter col=value ...] [--their-metric <sibling>] \
  [--their-artifact <path>] [--today YYYY-MM-DD] [--engine sqlite|bigquery]
```

Every re-run goes through the same `query-metric` helper as a first-class query: the
same allow-lists, the same PII refusal, the same tier gate, and on the warehouse path
the same individual-human ADC identity. A reconciliation that reached around
governance to "just check something" would be the most dangerous read in the system —
it is precisely the read nobody is watching.

## Interpreting the verdict

| `cause` | Meaning |
|---|---|
| `AGREE` | The numbers already match within tolerance — there is no real gap to explain. |
| `DATE_WINDOW` | They measured a different explicit window. |
| `MATURITY_CUTOFF` | Their range reaches into recent, still-immature cohorts that the governed per-stage cutoff withholds. |
| `TIMEZONE` | A one-day boundary shift — UTC versus a local day-cut. |
| `FILTER_DIFF` | They applied, or omitted, a filter we did not. |
| `GRAIN` | They measured a different grain, expressed as a different metric. |
| `UNKNOWN` | No single known convention reproduced their number. Needs manual investigation. |

`definition_drift` is reported separately when an artifact path was given: it flags
that their figure was built on a *different version* of the metric definition. That is
the one cause the numeric search cannot find — two runs whose definition changed in
between can agree numerically and still not mean the same thing.

**Report it in plain language, leading with the cause and the reproducing value:**

> The gap is **MATURITY_CUTOFF**. Your range runs through last week, which includes
> cohorts whose return window has not closed yet — the governed number withholds
> those. Re-running our metric over your wider window, without the cutoff,
> reproduces your 37%.

`MATURITY_CUTOFF` is worth recognising on sight, because on a deep-stage metric the
gap it produces is enormous rather than marginal: a return-window rate reads ~88%
over settled cohorts and ~37% over a window running to today. Two people can both be
"looking at the return rate" and be 50 points apart with neither making an error.

For `UNKNOWN`, say plainly that no known convention explained it, list what was tried
(from `tried`), and name the next thing to check — usually a filter or window detail
you did not have.

## Boundaries

- **Do not claim a cause the helper did not find.** If it says `UNKNOWN`, report
  `UNKNOWN`. The catalog of causes is the conventions two correct pipelines most often
  disagree about; it is not exhaustive, and pretending otherwise turns a real
  discrepancy into a closed ticket.
- **Tolerance matters.** A sub-tolerance difference is `AGREE`. Do not manufacture a
  cause for floating-point noise.
- **Read-only.** Reconcile re-runs queries and reports. It never edits a metric, a
  view, or an artifact, and fixing the discrepancy is a separate human decision.
- **One metric per invocation.** Reconciling two at once makes the causes
  uninterpretable.
- **On the fixture engine the verdict demonstrates the mechanism**, not a real
  disagreement — the helper marks that in `warnings`, and so should you.

## Out of scope

- Reconciling against a source you cannot characterise at all (no window, no filters,
  no value).
- Deciding which number is *right*. This explains why they differ; which convention
  should win is a governance conversation with the metric's owner.
