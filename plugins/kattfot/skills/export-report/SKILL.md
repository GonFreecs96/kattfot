---
name: export-report
description: >
  Export a governed artifact — a query-metric artifact (with its
  visualize-metric chart) or a spine-analysis artifact from funnel-dropoff,
  stage-timing, segment-compare, or funnel-pulse — to a single self-contained
  PDF on disk that a user can download and distribute as-is: chart inline
  when present, every table-bearing section, Narrative, and verbatim
  Caveats, stamped with the accuracy tier and source. Local only: no
  upload, no publishing pipeline. The markdown artifact is the other
  portable form.
---

# Skill: export-report

## Purpose

`query-metric` writes the numbers (markdown), `visualize-metric` adds the chart to that
markdown. **`export-report`** packages the result as a single, self-contained **PDF** the
user can download and hand to anyone — no hosting, no upload, no publishing surface.

```
query-metric  →  visualize-metric  →  export-report
 (the number)     (the chart)          (a portable PDF on disk)
```

It also exports the spine-analysis skills directly — `funnel-dropoff`,
`stage-timing`, `segment-compare`, and `funnel-pulse` write artifacts with the
same table(s)-plus-caveats-plus-tier-stamp shape, keyed by `skill:` instead of
`metric:` in frontmatter, and chart-less — each under its own skill-specific
heading(s) (`## Stage funnel`, `## Latency by transition`, …) rather than a
section literally named "Results". `report_pdf.py` renders **every**
table-bearing section it finds, in the artifact's own document order, so
neither shape needs a maintained per-skill heading map. Refusing to export
these would be an arbitrary gap in the compose chain — they are governed,
allow-listed, aggregate-only artifacts exactly like a query-metric run, just
without a chart:

```
funnel-dropoff / stage-timing / segment-compare / funnel-pulse  →  export-report
                    (the spine analysis)                             (a portable PDF)
```

Two portable forms come out of this chain, both local:
- **Markdown** — the `query-metric` artifact itself (`outputs/kattfot/{date}-{slug}.md`),
  with the chart embedded by `visualize-metric`. Editable source; travels with its
  `charts/<file>.png` sibling.
- **PDF** — what this skill produces: one file, chart inline, everything baked in.

A mature publishing/distribution workflow (wiki, drive, email, …) is intentionally
deferred until there's a demonstrated need to distribute visualizations at scale.

## Invocation

```
/export-report [artifact path]
```

Defaults to the most recently modified artifact in `$KATTFOT_OUTPUTS_DIR`
(`outputs/kattfot/`). Run `/visualize-metric` first so the artifact carries a
`## Visualization` section — without one the PDF is still produced, just chart-less.

## First-run setup

Reuse the plugin's `.venv`:
```
.venv/bin/pip install -r "$ER_ROOT/scripts/requirements.txt"
```
`fpdf2` is the only addition over what `visualize-metric` already installs. A missing
dependency is a hard error (exit 3).

## Skill root resolution

Resolve `ER_ROOT` once, first match wins:
1. `${CLAUDE_PLUGIN_ROOT}/skills/export-report/SKILL.md`
2. `<workspace>/.cursor/skills/export-report/SKILL.md`
3. the directory of the `SKILL.md` you loaded.

## Flow

### Step 1 — Locate the artifact
Use the given path, else the newest `*.md` in `$KATTFOT_OUTPUTS_DIR`. If none, tell the
user to run `/query-metric` (and `/visualize-metric`) first — or one of the
spine-analysis skills (`/funnel-dropoff`, `/stage-timing`, `/segment-compare`,
`/funnel-pulse`) for a chart-less spine artifact.

### Step 2 — Validate
Confirm governed frontmatter: `accuracy_tier`, `source_view`, `generated_at`, and
either `metric` (a query-metric artifact) or `skill` (a spine-analysis artifact).
If neither key is present, **stop** and say so — this skill exports governed output
only.

### Step 3 — Render the PDF
```
<python> "$ER_ROOT/scripts/report_pdf.py" \
  --artifact <artifact_path> \
  [--published-by "<name>"] \
  [--out <path.pdf>]
```
`report_pdf.py` reads the artifact's chart (from `## Visualization`, when present) and
**every table-bearing section, in the artifact's own document order** — not a single
hardcoded `## Results` lookup, so a metric artifact's one `## Results` table and a
spine artifact's several skill-specific tables (`## Stage funnel` +
`## Where it leaks (terminal branch)`, `## Latency by transition`, …) both render
uniformly with no per-skill heading map to maintain (see `table_sections()`) — plus the
Narrative and Caveats — and writes a PDF next to the artifact
(`outputs/kattfot/{date}-{slug}.pdf` by default). It reproduces numbers and caveats
**verbatim**, stamps the accuracy tier + source + generated-at, renders a **MOCK DATA**
banner when `fixture_backed` or `source_mode` is `mock_csv`/`fixture_sql`, and quantizes
the chart so the file stays small. It prints `{"pdf_path", "title"}`.

### Step 4 — Report
Tell the user the PDF path (and the markdown artifact path). Do not paste the file into
chat — point to it. If a MOCK DATA banner was rendered, remind the user the PDF is stamped
accordingly. The user downloads and distributes the file as needed.

## Governance

- **Faithful only.** Every number, caveat, and the chart come from the governed artifact;
  this skill adds nothing and re-queries nothing.
- **Stamps survive.** Accuracy tier, source view, generated-at, and the MOCK DATA banner are
  rendered into the PDF; caveats are reproduced verbatim and never trimmed.

## Out of scope

- Generating or modifying numbers — run `query-metric` / `visualize-metric` first.
- Uploading, emailing, or publishing anywhere — this skill only writes a local file.
- Editing the source artifact.
- Any T1 / T3 metric (refused upstream by `query-metric`).

## Status in this template

`report_pdf.py` is complete and runnable; it needs only a governed artifact on disk.
`query-metric` and `visualize-metric` — the upstream that produces one — are part of
the skill set this template builds toward rather than files you will find in this repo
yet. Point this at any markdown artifact carrying the governed frontmatter and it works.
