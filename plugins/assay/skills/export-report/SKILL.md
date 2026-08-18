---
name: export-report
description: >
  Export a governed query-metric artifact (with its visualize-metric chart) to a
  single self-contained PDF on disk that a user can download and distribute as-is
  — chart inline, Results table, Narrative, and verbatim Caveats, stamped with the
  accuracy tier and source. Local only: no upload, no publishing pipeline. The
  markdown artifact is the other portable form.
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

Two portable forms come out of this chain, both local:
- **Markdown** — the `query-metric` artifact itself (`outputs/assay/{date}-{slug}.md`),
  with the chart embedded by `visualize-metric`. Editable source; travels with its
  `charts/<file>.png` sibling.
- **PDF** — what this skill produces: one file, chart inline, everything baked in.

A mature publishing/distribution workflow (wiki, drive, email, …) is intentionally
deferred until there's a demonstrated need to distribute visualizations at scale.

## Invocation

```
/export-report [artifact path]
```

Defaults to the most recently modified artifact in `$ASSAY_OUTPUTS_DIR`
(`outputs/assay/`). Run `/visualize-metric` first so the artifact carries a
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
Use the given path, else the newest `*.md` in `$ASSAY_OUTPUTS_DIR`. If none, tell the
user to run `/query-metric` (and `/visualize-metric`) first.

### Step 2 — Validate
Confirm governed frontmatter (`metric`, `accuracy_tier`, `source_view`, `generated_at`).
If it isn't a governed artifact, **stop** and say so — this skill exports governed output
only.

### Step 3 — Render the PDF
```
<python> "$ER_ROOT/scripts/report_pdf.py" \
  --artifact <artifact_path> \
  [--published-by "<name>"] \
  [--out <path.pdf>]
```
`report_pdf.py` reads the artifact's chart (from `## Visualization`), Results table,
Narrative, and Caveats and writes a PDF next to the artifact
(`outputs/assay/{date}-{slug}.pdf` by default). It reproduces numbers and caveats
**verbatim**, stamps the accuracy tier + source + generated-at, renders a **MOCK DATA**
banner when `source_mode == mock_csv`, and quantizes the chart so the file stays small. It
prints `{"pdf_path", "title"}`.

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
