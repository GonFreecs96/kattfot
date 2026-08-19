# Authoring a governed metric

A metric definition is a markdown file with YAML frontmatter. The frontmatter is the
machine-readable contract — the query builder, the allow-list enforcement, the
maturity gate, and the provenance receipt all read it. The body is what a human
reads, and its `## Caveats` section is reproduced **verbatim** into every artifact
this metric ever produces.

That last point is the one to hold on to while writing. A caveat here travels into
every chart, PDF, and pasted table forever. It is the highest-leverage prose in the
repository, and it is also where the temptation to be flattering does the most damage.

## Where files go

```
skills/query-metric/metrics/<module>/<metric-slug>.md
```

The directory name must equal the `module:` value in the frontmatter —
`tests/test_metric_catalog.py` enforces it, so the filesystem cannot disagree with
the metadata.

A consumer can override any bundled metric by defining the same `name:` under
`$ASSAY_METRICS_DIR` (default `context/assay/metrics/`). Consumer definitions win, so
a team can adjust one metric without forking the catalog.

## Required frontmatter

Every field below is checked by the catalog linter. A missing one fails CI.

| Field | Type | Meaning |
|---|---|---|
| `name` | slug | Unique. The SQL alias for the value column, and what a caller passes to `--metric`. |
| `display_name` | string | Human-readable title, used in artifact headings. |
| `accuracy_tier` | `T2` | The only tier this runtime serves. T1 belongs to the system of record; T3 to an ad-hoc lane. |
| `source_view` | `dataset.view` | Must contain a dot. The curated view, never a raw table. |
| `source_project` | string | `${ASSAY_BIGQUERY_PROJECT}` in this template — expanded from the environment at query time. |
| `time_column` | column | The cohort column. Bucketing is by cohort, not activity time. |
| `numerator_column` | column | Pre-aggregated count on the view. |
| `denominator_column` | column | Likewise. |
| `value_format` | `percent` \| `count` \| `currency` \| `duration` | How the value column renders. Applied in `_shared/numfmt.py`, never by hand. |
| `allowed_dimensions` | list | Exactly what may be grouped by. Anything else refuses under R2. |
| `allowed_filters` | list | Exactly what may be filtered on. Anything else refuses under R3. |
| `default_lookback_weeks` | int | Window when the question does not specify one. |
| `status` | `live` \| `dummy` \| `deprecated` | `dummy` routes to the CSV-stub oracle. |
| `entity` | slug | The registered entity the numerator and denominator measure. |
| `module` | slug | Must match the containing directory and a module in `config/modules.yaml`. |

### Optional but usually worth setting

| Field | Meaning |
|---|---|
| `maturity_stage` | Keys into the funnel spec's `stage_maturity.lag_days`. **Set this.** See below. |
| `maturity_lag_days` | An explicit lag, overriding the stage table. Use only when the metric's stage genuinely is not in the ladder. |
| `column_label` | Friendly header for the value column. Display-only; the SQL alias stays `name`. |
| `time_column_type` | `DATE` (default), `DATETIME`, or `TIMESTAMP`. A DATETIME cohort column needs its range widened to whole days, which the helper does. |
| `owner` | Who to ask when the definition looks wrong. See `doctrine/ownership-governance.md`. |
| `relationships` | Registered entity slugs the metric also touches. |
| `business_rules` | Rule ids from the entity registry or the module's `LINEAGE.md`. |

## On `maturity_stage`

Set it. A metric without one has **no maturity gate at all** — it will happily report
a return-window rate for last week's cohort as though the answer were in, and the
number will be wrong in the flattering direction.

The mechanism: maturity is a property of a (cohort × stage) pair, not of a cohort. A
week that has settled its cart-to-checkout outcome (minutes) must not be withheld for
the six weeks a return-window outcome needs. `maturity_stage` names which stage this
metric's *numerator* depends on; the lag for that stage lives in the module's funnel
spec, and the query layer adds `{time_column} <= @maturity_cutoff` accordingly.

Declaring a `maturity_stage` that the spec has no lag for is a **hard error**, not a
silent no-op. That is intentional: it is a governance gap a reviewer must see.

## On allow-lists

Keep them tight. An allow-list is not a list of columns that exist, it is a list of
slices you are willing to defend.

- **Only list columns the source view actually has.** For a pre-aggregated view, that
  means only its group-by dimensions. Listing a column the view lacks produces a
  runtime error, not a graceful refusal.
- **Watch cardinality.** A dimension with hundreds of values makes every cell too thin
  for a rate to mean anything. The honest fix is a rollup dimension, not a wider list.
- **PII is refused regardless.** `_shared/pii.py` checks *before* the allow-lists, so
  allow-listing a personal column does not authorize it. That ordering is the point.

## On caveats

The section that matters. Guidance, in rough order of how often it is skipped:

**Name the mix confounders.** If the blended value moves with a dimension mix, say so
and name the dimension. This is the single most common way a correct number produces a
wrong conclusion.

**Say what the metric is not.** "Approval is not a quality measure." "Delivered means a
carrier scan, not a customer receiving something." Readers import assumptions from the
metric's name; the caveats are where you take them back.

**State the direction of a bias, not just its existence.** "Reads low on immature
cohorts" is useful; "may be affected by maturity" is noise. A reader who knows which
way a number is wrong can still use it.

**Include the unflattering ones.** The caveat you would rather a stakeholder did not
read is the one that most needs to be in the artifact. A caveat a reader has to
discover for themselves was a caveat you were hiding — and it will surface at the worst
possible moment, in a meeting, from someone else.

**Say when something is genuinely unanswerable here.** "Sizing the retry rescue needs
the session spine and a customer-token join — not answerable from this view." Naming a
limit is more useful than an approximation nobody can calibrate.

## Body structure

Keep the section headings, so downstream renderers can find them:

```markdown
# {display_name}

## Definition
## Numerator
## Denominator
## Value formula
## Caveats            <- reproduced verbatim into every artifact
## Accuracy tier
## SMEs
## Example queries
```

## Before you commit

```bash
pytest                            # catalog linter, SQL regression, maturity contract
python3 scripts/scrub_check.py    # no private-original names leaked in
```

Then run the metric end to end and read the artifact as a stakeholder would. If the
Caveats section does not change how you would interpret the Results table, it is not
finished.
