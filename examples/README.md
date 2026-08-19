# Examples

Eight sample outputs, committed so a reader can see what `assay` actually
produces without cloning the repo or running anything.

**These are real generated output, not mockups.** Every file here came out of
actually running the corresponding skill end-to-end — `scripts/demo.py --all`
and `scripts/demo_skills.py` — against the committed fixture CSVs on the
`sqlite` engine, then copying the result here verbatim. Nothing was
hand-written. The only edits made to any file below are two relative link
paths inside `query-metric-artifact.md` (pointing at `query-metric-chart.png`
and `chart-spec.json` instead of their original `outputs/assay/charts/...`
locations), so the artifact's embedded image and spec link resolve from this
flat folder. No numbers, text, or structure were touched.

**`${ASSAY_BIGQUERY_PROJECT}` in these files is a literal, unexpanded
placeholder, not a broken value.** This template ships with no real project
id, so every artifact's `source_view` / `source_project` carries the
environment-variable name verbatim rather than a substituted (and
inevitably fake-looking) project string — point `ASSAY_BIGQUERY_PROJECT` at
a real project and it resolves normally.

Because they're fixture-backed rather than warehouse-backed, every one of
them is **watermarked**: a diagonal "MOCK DATA" mark on every chart, a `T2`
accuracy-tier pill/stamp on every artifact, and an explicit "FIXTURE DATA"
banner in the markdown and PDF. That watermarking is the point — it's the
same governance chrome a real deployment would show for an unreviewed or
low-tier number, just permanently on here because the fixtures are synthetic
by construction (see the repo's `LINEAGE.md`).

## What's here

| File | Form | Produced by |
| --- | --- | --- |
| [`query-metric-artifact.md`](query-metric-artifact.md) | Governed metric artifact | `query-metric` → composed by `scripts/demo.py` |
| [`query-metric-chart.png`](query-metric-chart.png) | Static chart (embedded above) | `visualize-metric` (matplotlib) |
| [`chart-spec.json`](chart-spec.json) | Chart-spec provenance artifact | `visualize-metric`'s `chart_spec.py` |
| [`interactive-chart.html`](interactive-chart.html) | Interactive self-contained chart | `render-chart` (Apache ECharts, inlined) |
| [`dashboard.html`](dashboard.html) | Self-contained dashboard | `render-dashboard` |
| [`query-metric-report.pdf`](query-metric-report.pdf) | Portable PDF export | `export-report` (fpdf2) |
| [`funnel-dropoff.md`](funnel-dropoff.md) | Spine-grain funnel + leak attribution | `funnel-dropoff` |
| [`funnel-dropoff-report.pdf`](funnel-dropoff-report.pdf) | Portable PDF export of a spine artifact | `export-report` (fpdf2) |

**`query-metric-artifact.md`, `query-metric-chart.png`, `chart-spec.json`,
`interactive-chart.html`, and `query-metric-report.pdf` are five views of the
same run** — `cart_to_checkout_start_conversion` by `channel`, last 16 cohort
weeks as of the fixtures' pinned "today" (2026-08-17) — so you can compare the
governed markdown, the static chart, the machine-readable spec behind it, the
interactive twin, and the PDF export side by side. The artifact has
everything the format promises: YAML frontmatter with the accuracy tier and
manifest fingerprints, a Results table, a `## Visualization` section, a
`## Narrative`, verbatim `## Caveats` and run warnings, and a `## Provenance`
section with the parameterized SQL and the manifest receipt (`assay:verify`
checks these fingerprints offline, without touching a warehouse).

`funnel-dropoff.md` is included because it demonstrates something a plain
`SUM/SUM` metric artifact structurally cannot: the **terminal-branch
decomposition** — of every cart created, the share that ended in each
mutually exclusive branch (Payment Declined, Stale Pre-Checkout, Delivered
then Returned, ...), checked to sum back to the cohort's cart count. That's
the session-spine capability the module's ratio metrics can't express.

`funnel-dropoff-report.pdf` is `funnel-dropoff.md` exported through the same
`export-report` skill as the metric PDF above — `report_pdf.py` accepts a
`skill:`-keyed spine-analysis artifact exactly like a `metric:`-keyed one, so
the compose chain works uniformly regardless of which family produced the
artifact. It has no chart (spine-analysis skills don't render one), so the
PDF is the stage-funnel and terminal-branch tables plus the governance stamp,
chart-less by design rather than by omission.

`dashboard.html` is rendered from `render-dashboard`'s own bundled example
spec (a hand-authored spec that ships with the skill to demonstrate the
dashboard *contract* — KPI cards, a funnel + trend chart row, a ranked-bar
leak chart, a table), not from a live query. The rendering itself is real;
only the input numbers are the skill's own illustrative example rather than a
fresh `query-metric` run.

## Regenerating these

Nothing here is checked in as a substitute for running the tool — it's a
preview. To reproduce (or refresh) it yourself:

```bash
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
.venv/bin/pip install -r plugins/assay/skills/visualize-metric/scripts/requirements.txt
.venv/bin/pip install -r plugins/assay/skills/export-report/scripts/requirements.txt

# every skill, including render-dashboard and the spine analyses — run this FIRST
.venv/bin/python scripts/demo_skills.py

# the metric chain: query-metric -> artifact -> chart-spec -> PNG/SVG + interactive HTML -> PDF
.venv/bin/python scripts/demo.py --all

# a PDF export of a spine-analysis artifact (demo_skills.py builds the .md, not the PDF)
.venv/bin/python plugins/assay/skills/export-report/scripts/report_pdf.py \
  --artifact outputs/assay/2026-08-17-funnel-dropoff.md \
  --out outputs/assay/2026-08-17-funnel-dropoff.pdf --published-by "assay demo"
```

Run `demo_skills.py` before `demo.py --all`, not after: both write the same
metric-chain artifact filename, but only `demo.py --all` re-injects the
`## Visualization` section into it — running `demo_skills.py` second would
silently strip the chart back out.

Both scripts write into `outputs/assay/` (git-ignored, since it's
regenerated), with the real "as of" date in each filename. The files in this
folder are copies of that run, renamed for a stable layout so they can be
linked to directly.
