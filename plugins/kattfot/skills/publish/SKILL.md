---
name: publish
description: >
  OUTLINED, NOT IMPLEMENTED — see the status banner below. Would publish a
  governed kattfot artifact to a wiki as a read-only page, through a pluggable
  wiki-adapter seam rather than a hardcoded vendor. Would resolve the target
  via a deterministic page registry, preview the composed page, and — only
  after explicit human approval — write it. Triggers on "publish this
  metric", "publish to the wiki", "push this report to the team wiki",
  "update the funnel pulse page", "seed the reports space".
version: 0.1.0
---

# Skill: publish

## ⚠️ Status: outlined, not implemented

**This skill ships as a documented outline only.** There is no wiki client
behind it — `scripts/publish_stub.py` in this directory exits immediately
with a clear, non-zero-exit refusal (see [Status in this template](#status-in-this-template)).
No page is ever composed against a live target, no write call is ever made,
and the approval gate below is never exercised because there is nothing on
the other side of it to write to.

**What implementing it for real would need:**
- **A wiki adapter.** This is the one piece of genuine integration work
  across all four outlined skills — everything else in `publish`'s design
  (the registry, the approval gate, the composition rules) is orchestration
  this outline already specifies in full. What is missing is a small client
  for **your** wiki that can do three things: fetch a page's current state,
  create a page, and update a page's content with a version message. See
  [Adapter seam, not a vendor choice](#adapter-seam-not-a-vendor-choice)
  below for why this is designed as a seam rather than a hardcoded
  integration.
- **Somewhere real to publish to.** A public template has no shared team
  wiki of its own — every page ID in `wiki-registry.example.yaml` in this
  directory is `TBD` on purpose, and the approval gate refuses a `TBD`
  target outright (see [Never](#never)). Implementing the adapter and having
  a destination are two separate prerequisites; either one missing is enough
  to keep this outlined.
- **No new statistics or fixtures** — unlike `funnel-nowcast`,
  `release-scorecard`, and `customer-journey`, this skill's gap is pure
  integration, not modeling or data. It needs no new dependency beyond
  whatever HTTP client your chosen wiki's API expects.

## Purpose (what this skill would do)

Turn a governed kattfot artifact into a **read-only** page in a team wiki. The
plugin would own these pages; people would read them, not hand-edit them —
the same "the tool owns this surface" posture `kattfot:query-metric` already
takes toward `outputs/kattfot/`.

> **Hard rule — approval gate.** Never call a wiki write operation (create or
> update) until the human has seen the composed page **and** the exact
> target (space, page, version message) and has explicitly approved. Every
> published number is reviewed before it goes live. No exceptions, and no
> "I'll just update it" on a re-run.

## Adapter seam, not a vendor choice

This template already has one precedent for exactly this shape of decision:
`_shared/adapters/` gives the warehouse layer one BigQuery adapter and one
sqlite adapter behind a shared interface, so the governance code above it
never hardcodes a query dialect. `publish` would use the same idea for the
write side — a thin `WikiAdapter` interface:

```
get_page(page_id)        -> current title + body + version, or NotFound
create_page(space, title, body) -> page_id
update_page(page_id, body, version_message)
```

A real deployment would implement this once for whatever wiki it actually
uses — a hosted wiki product, a self-hosted one, or a git-backed docs site —
and everything else in this SKILL.md (the registry, the composition rules,
the approval gate) would work unchanged against any of them. No adapter
ships with this template; the interface above is the whole contract an
implementer needs to satisfy; a `NullAdapter` that renders the composed page
to a local file instead of a network call would be a reasonable first
milestone; it exercises every step up to the approval gate with nothing to
authenticate against.

## Inputs (once implemented)

- **target** (required) — a registry key, e.g. `overview`,
  `checkout/funnel-pulse`, or a metric slug like
  `cart_to_checkout_start_conversion`. Resolves to a wiki page via the
  registry below.
- **source** (optional) — path to the artifact to publish.
  - For metric targets: a `query-metric` output markdown file. Default = the
    most recent file in `<cwd>/outputs/kattfot/` (or `$KATTFOT_OUTPUTS_DIR`).
  - For `overview`: defaults to `space-overview-seed.md` in this directory
    (not yet written — see [Status in this template](#status-in-this-template)).

## Step 0 — Load the page registry

Read the registry, first match wins:
1. `<cwd>/config/kattfot-wiki-registry.yaml` (or `$KATTFOT_WIKI_REGISTRY`) —
   consumer override, carrying real page IDs for a real deployment.
2. else `${CLAUDE_PLUGIN_ROOT}/config/wiki-registry.yaml` — a plugin-local
   **live** registry, if present.
3. else `wiki-registry.example.yaml` in **this directory** — a template.
   Warn the user it carries placeholder page IDs and no adapter is wired to
   it.

Extract the `wiki.adapter` name, `wiki.space` (key + name), and the `pages`
map.

## Step 1 — Resolve the target

- Look up `target` in `pages` → get `page_id` and `title`.
- If `page_id` is `TBD` or missing: the page does not exist yet. Offer to
  **create** it via the adapter's `create_page` (which also needs the space
  to exist and explicit approval). Do not proceed silently, and do not invent
  a page id.
- If `target` matches no key: list the available registry keys and stop.
  Never guess a page.

## Step 2 — Load + validate the source

Read the source artifact. Every governed kattfot artifact carries
`accuracy_tier`, `source_view`, and `generated_at` in its frontmatter — that
triple is the gate. If the source lacks it, **stop** and say so; this skill
publishes governed output only.

Artifact kinds (the frontmatter `skill:` / `metric:` field distinguishes
them):
- **metric target** — a `query-metric` artifact (`metric:` frontmatter).
  Compose Results / Narrative / Caveats / Provenance (Step 3a).
- **`checkout/funnel-pulse` target** — a `funnel-pulse` digest
  (`skill: funnel-pulse`). Compose Flags table + end-to-end table /
  Narrative / Caveats / Provenance (Step 3b).
- **`overview`** — the seed file is pre-trusted (Step 3a layout, no Results
  table).

Default source for the `checkout/funnel-pulse` target = the most recent
`*-funnel-pulse.md` in `<cwd>/outputs/kattfot/` (or `$KATTFOT_OUTPUTS_DIR`).

## Step 3 — Compose the page body

Every page would carry the same chrome, regardless of artifact kind:
- **Read-only banner**: "📋 Read-only — maintained by the `kattfot` plugin. Ask
  questions with `kattfot:query-metric`; do not edit this page directly."
- **Header stamp**: accuracy-tier badge, `source_view`, `generated_at`,
  **published-by** (the human running this skill), and the source artifact
  filename.
- Never drop the accuracy-tier stamp or any caveat.

### Step 3a — query-metric artifact / overview
- The artifact's **Results** table, **Narrative**, **Caveats** (verbatim —
  never trimmed), and a **collapsible Provenance** section (parameterized SQL
  + bound params, collapsed by default so the page reads clean and the
  receipt is still one click away).

### Step 3b — funnel-pulse digest
- A one-line **window stamp**: recent window vs baseline window + maturity-lag
  note.
- The **🚩 Flags** table (regressions first), verbatim from the digest.
- The **end-to-end by {dimension}** table.
- The **Narrative** and **Caveats** (verbatim — the maturity-lag and
  routing-shift caveats must survive), and a **collapsible Provenance**
  (the digest's stage / branch SQL).

## Step 4 — PREVIEW + approval gate

Present to the human, in chat:
- target **space** + page **title** + **page_id**
- the composed body (a rendered preview is fine)
- the exact **version message** you will use (date + what changed)

Then ask explicitly: **"Publish to {title} ({page_id})? — yes / no"**. Do not
call any wiki write operation until an explicit yes. A denial ends the run
with nothing written.

## Step 5 — Publish

On approval, call the adapter's `update_page` (or `create_page` for a new
target) with the full body and a dated version message. Return the page URL
the adapter reports.

## Step 6 — Record (optional)

If the consumer registry has a `last_published` field for that page, update
it with today's date.

## Never

- Never write to the wiki without explicit approval (Step 4).
- Never publish an artifact lacking governed frontmatter.
- Never strip the accuracy-tier stamp or caveats.
- Never write to a `page_id` of `TBD`.
- Never edit a published page's content outside this skill — these pages are
  plugin-owned.

## Refusals

A source lacking governed frontmatter, an unresolvable `target`, or a `TBD`
page id all refuse cleanly before Step 4 — none of them is a case the
approval gate should ever see.

## Out of scope (even once implemented)

- Choosing a wiki product on the user's behalf — the adapter seam exists so
  this skill never has an opinion about which one.
- Publishing a `T1` or `T3` artifact (refused upstream — nothing not T2 ever
  reaches this skill with governed frontmatter in the first place).
- Any edit to a page's content outside a full recompose-and-approve cycle —
  no partial patches.

## Status in this template

There is no wiki adapter, live or otherwise, behind this skill.
`scripts/publish_stub.py` in this directory is the only thing that runs:

```
$ python3 plugins/kattfot/skills/publish/scripts/publish_stub.py
publish is not implemented in this template.
See SKILL.md in this directory for the adapter interface and exactly
what implementing it would require.
$ echo $?
9
```

`wiki-registry.example.yaml` in this directory is the ported, genericized
form of the private original's page registry — real in shape, but every
`page_id` in it is `TBD`, and Step 1 above refuses to write to a `TBD` page
regardless of what an implementer eventually wires up. `space-overview-seed.md`
(the default `overview` source) is not included in this outline; an
implementer would author one for their own space the way this template's
README describes the project.
