# The governance model

*What each control guarantees, and — just as important — what it honestly
does not. Model this doc on the doctrine files it summarizes:
`plugins/assay/skills/query-metric/doctrine/accuracy-tiers.md`,
`doctrine/ownership-governance.md`, and the code in `_shared/identity.py`,
`_shared/pii.py`, and `_shared/manifest.py`. Those files are the canonical
source; this doc is the fast overview.*

`assay` is not a framework for building dashboards. It is a worked example of
one specific claim: that an agent can be given real access to a warehouse and
still be structurally unable to produce a number nobody can defend. That
claim rests on a stack of controls, each enforced in code rather than in a
prompt. This doc walks the stack top to bottom, and for every control it
answers two questions — **what does this guarantee**, and **what does this
not guarantee** — because a control whose limits are undocumented gets
treated as stronger than it is.

## The stack, at a glance

| Layer | Control | Enforced in |
|---|---|---|
| 1 | Per-user identity | `_shared/identity.py` |
| 2 | One way to the warehouse | `hooks/scripts/bq_path_matcher.py` (+ `.cursor/hooks/`) |
| 3 | Allow-listed metrics, dimensions, filters (R1–R3, R5) | `query-metric`'s helper, per skill |
| 4 | PII refusal (R6) | `_shared/pii.py` |
| 5 | Accuracy tiers (R4) | doctrine + catalog linter |
| 6 | Per-stage maturity | funnel spec `stage_maturity`, `_shared/spine.py` |
| 7 | Content-addressed manifest | `_shared/manifest.py`, `assay:verify` |
| 8 | Warn-level guardrails | `config/guardrails.yaml`, `_shared/guardrails.py` |
| 9 | Instrumentation coverage register | `config/coverage.yaml`, `_shared/coverage.py` |
| 10 | The scrub gate | `scripts/scrub_check.py` |
| 11 | Audit trail | `query-metric`'s audit-log writer |

Layer 2 is the one that makes every layer above it more than advisory: skip
it, and an agent under pressure ("just this once") can reach the warehouse
through a path that has none of layers 1, 3–6 wired in at all.

## 1. Per-user identity

**Mechanism.** Every warehouse query on the `bigquery` engine authenticates
via Application Default Credentials — the individual human's own browser
OAuth login (`gcloud auth application-default login`). Before any query runs,
`_shared/identity.assert_user_identity()` reads the local ADC file and
refuses if it is a service account, an impersonated service account, or any
credential type other than `authorized_user`. If
`GOOGLE_APPLICATION_CREDENTIALS` is set at all, it refuses immediately,
because that variable's entire purpose is pointing at a service-account key.

**What this guarantees.** Every query on the warehouse path is attributable
to a real, individually-authenticated person. There is no shared key to leak,
and access is revoked the moment that person's own Google identity is.

**What this does not guarantee.**

- **It only runs on the `bigquery` engine.** On the default `sqlite` engine,
  `assert_user_identity()` is not called at all — an explicit branch in
  `_shared/adapters/sqlite_adapter.py`. A fixture read touches no warehouse
  and no credentials, so there is nothing to attribute, and requiring a cloud
  login to read a committed CSV would be theatre that also breaks
  `git clone && pytest` on a machine with no GCP account. Every fixture-backed
  result is marked `fixture_backed: true` in exchange, which becomes the MOCK
  DATA watermark on every downstream artifact — so a number produced this way
  can never pass itself off as measured. `tests/test_adapters.py` pins both
  halves of that trade: the sqlite path's exemption, and every other engine's
  refusal.
- **It does not check what the person is *allowed* to query within the
  warehouse.** IAM and BigQuery row-/column-level policy, if configured, are
  what actually bound a person's access; this control only ensures the
  identity presented is theirs.

## 2. One way to the warehouse — the access-path hook

**Mechanism.** A Claude Code `PreToolUse` hook (and a Cursor
`beforeShellExecution` hook, sharing the same matcher,
`bq_path_matcher.py`) inspects every Bash command before it runs and denies
anything that reaches BigQuery or GCP outside the plugin's vetted skill
scripts (`plugins/assay/skills/*/scripts/*.py`): the bare `bq` CLI, `gcloud`
referencing BigQuery, ad hoc `python -c` using `google-cloud-bigquery`
directly, and `curl`/`wget` to `googleapis.com` — including inside
`bash -c` and behind `sudo`/`env` prefixes.

**What this guarantees.** An agent cannot satisfy a request by routing
around identity (layer 1), the allow-lists (layer 3), or the PII denylist
(layer 4) by reaching for a raw CLI call or an inline script — the most
common way a well-intentioned "just this once" workaround defeats every
control above it. This runs **outside the model's control**: it is not a
system-prompt instruction the agent could be talked out of.

**What this does not guarantee.**

- **It is a blocklist, not a sandbox.** It matches on the text of the Bash
  command, not on what a script actually does. `python3 some_file.py` sails
  through if the string `bigquery` (or any other matched token) never
  appears in the command line invoking it — including a repo-root one-off in
  `scripts/` that nobody meant to be a governed path.
- **It only covers the `Bash` tool.** A tool call that reaches the warehouse
  some other way entirely (a different MCP server, a notebook cell run
  outside this hook's scope) is not something this matcher can see.
- **It is one shared matcher tested in both directions.**
  `tests/test_bq_bypass_hook.py` pins that real vetted-script invocations
  stay allowed and that known bypass shapes stay denied — but a denylist by
  its nature only catches shapes someone thought to write a test for.

## 3. Allow-listed metrics, dimensions, and filters (R1–R3, R5)

**Mechanism.** Every metric definition (and every spine-backed skill's
funnel spec) declares `allowed_dimensions` and `allowed_filters` explicitly.
A question that maps to no registered metric refuses under **R1**. A
requested group-by outside `allowed_dimensions` refuses under **R2**. A
filter column outside `allowed_filters` refuses under **R3**. A request for
individual-level rows ("show me each order", "by session id") refuses under
**R5** — aggregate-only is not a privacy flourish here, it is what keeps a
number reconcilable and re-derivable from its own definition, which an
individual row structurally cannot be.

**What this guarantees.** An off-list request gets a clean, named refusal —
which rule fired, the offending input, and the actual allow-list — rather
than the agent silently attempting a plausible-looking substitute. No
artifact is written on a refusal.

**What this does not guarantee.**

- **The allow-list is only as tight as whoever wrote it.** `AUTHORING.md`
  frames this directly: "an allow-list is not a list of columns that exist,
  it is a list of slices you are willing to defend." A high-cardinality
  dimension added carelessly makes every cell too thin for a rate to mean
  anything, and the allow-list mechanism has no opinion about that — it only
  enforces the boundary someone drew, not where they should have drawn it.
- **R1's nearest-match suggestion is a convenience, not a guarantee of
  intent.** A typo that happens to resemble a different registered metric
  will be *suggested*, and a careless accept could answer a different
  question than the one asked.

## 4. PII refusal — R6

**Mechanism.** `_shared/pii.py` is a pure, dependency-free denylist of
column-name patterns — personal names, contact details, government IDs,
financial account/card numbers, health records, dates of birth, street
addresses — matched against every requested dimension and filter column
**before** the allow-list check runs, not after. An explicit safe-list
(`customer_token`, `order_id`, `market`, `postal_code`, and similar) keeps
tokenized join keys and aggregate geography usable.

**What this guarantees.** A metric author who accidentally allow-lists a
personal column (say, `customer_email`, in a moment of carelessness) has
**not thereby authorized it** — R6 fires regardless, because it is checked
ahead of, and independently of, the metric's own frontmatter. This is a
**hard refuse with no override** — unlike every other rule here, there is no
code path that lets a caller argue past it.

**What this does not guarantee.**

- **It is defense-in-depth inside the vetted helpers only.** Reach the
  warehouse any other way (which layer 2 exists to prevent, but see that
  layer's own limits) and this check simply never runs.
- **It is a name-pattern denylist, not a data-classification system.** It
  catches columns whose *name* looks like the categories above. A personal
  column with an opaque or misleading name would not be caught by this layer
  — which is exactly why the doctrine treats warehouse-side column-level
  security (BigQuery policy tags) as the *durable* backstop and this denylist
  as a seatbelt on top of it, not a replacement. Confirm with whoever owns
  your data platform whether that policy-tag layer is actually configured
  before treating the code guard as complete.
- **The view itself has to cooperate.** `tests/test_view_ddl_pii.py` scans
  the shipped view DDL for R6-denylisted identifiers so a personal column
  cannot be exposed at the view layer in the first place — but that test
  only covers the DDL shipped in this repository, not whatever a fork
  writes.

## 5. Accuracy tiers — R4

**Mechanism.** Every metric declares an `accuracy_tier`. **T1**
(regulator-grade, reconciling to a system of record) is refused outright —
this runtime serves no T1 metric, because a governed convenience layer
cannot inherit a control it does not implement. **T2** (operational,
decision-grade, reviewed and version-pinned) is the only tier served, and
`tests/test_metric_catalog.py` enforces that every bundled metric is T2. **T3**
(exploratory, ad hoc, no stable definition) belongs to a lane this template
does not implement; a genuinely exploratory question should be named as one
rather than forced through a T2 metric it does not fit.

**What this guarantees.** A tier is a claim about the review a number has
survived, and it travels with the number into every artifact, chart, and PDF
— so a reader can tell at a glance whether they are looking at something
decision-grade or something that was never meant to be.

**What this does not guarantee.**

- **A tier is not a confidence interval.** It says a definition was reviewed
  and is version-pinned, not that the underlying data is accurate or that
  the sample size is adequate.
- **This template has never had a T1 or T3 metric to test the refusal
  against.** The catalog linter enforces "every bundled metric is T2" by
  construction; the R4 refusal path for a *hypothetical* T1/T3 metric is
  exercised by the `load-context` contract tests
  (`tests/test_read_apis.py`), not by a real regulator-grade metric living in
  this repository.

## 6. Per-stage maturity

**Mechanism.** A funnel stage is withheld only until *that stage* settles —
not until the whole funnel's slowest stage does. Each metric declares a
`maturity_stage`; the settle lag for that stage lives in the module's funnel
spec (`stage_maturity.lag_days`), and the query layer adds a
`cohort_week <= @maturity_cutoff` predicate accordingly. Spine-backed skills
apply the same idea per stage and per timing transition.

**What this guarantees.** An early-funnel rate (cart-to-checkout-start,
settling in minutes) stays fresh through last week's cohort, while a
deep-stage rate (delivered-to-return-window-complete, settling in ~44 days)
is honestly withheld for recent cohorts rather than reported as though the
outcome were already known. A metric with no `maturity_stage` set has **no
maturity gate at all** — `AUTHORING.md` calls this out explicitly, because
the resulting number is wrong in the flattering direction (it reports an
immature cohort's rate as final).

**What this does not guarantee — and this is the caveat to state loudest.**

- **The lags shipped in this template are hand-authored, not measured.** The
  funnel spec marks itself `illustrative: true` with `calibrated_at: null`,
  every artifact that reads it repeats that flag, and `tests/test_maturity.py`
  asserts those two flags stay set. In a real deployment, this block is
  *generated* by measuring open-anchored settle time on your own spine (the
  p95 of `DATE_DIFF(stage_date, open_date, DAY)`) and the flags are cleared.
  A hand-picked lag is an assumption wearing a measurement's clothes — the
  entire value of the mechanism is that the number was observed, and this
  template is explicit that, here, it was not.
- **A missing calibrated lag is a hard refusal, not a silent pass-through.**
  `funnel-pulse`'s window builder refuses (exit 5) rather than defaulting to
  an ungated window, because a monitor comparing two unsettled windows would
  report the tail arriving as though it were a real shift, and say nothing
  about it.

## 7. Content-addressed manifest (the receipt)

**Mechanism.** Every `query-metric` artifact carries a manifest:
`metric_def_sha` (the definition as used), `query_sha` (the exact SQL plus
bound parameters), and `result_sha` (the result volumes). `generated_at` is
deliberately excluded from every hash, so a faithful re-run over unchanged
inputs reproduces all three fingerprints exactly. `assay:verify` recomputes
the definition and query fingerprints from the *current* repo state and
compares them against what an old artifact recorded.

Every artifact also records the acting **agent** (`{skill, plugin_version}`) —
the software that produced it, the delegation counterpart to the human
principal recorded in the audit trail. In an agentic system "who made this
number" has two answers, and a receipt that kept only the human one could not
tell an analyst's own query from one an agent ran in their name.

**What this guarantees.** A number is replayable and tamper-evident. If a
metric's definition changes after an artifact was produced, `verify` catches
the drift (`DEFINITION_DRIFT`) without needing to re-run anything against a
warehouse.

**What this does not guarantee.**

- **`verify` never re-checks `result_sha`.** Confirming the *value* is still
  correct requires re-running the query against live data — out of scope for
  an offline check, and the skill says so explicitly rather than implying
  the number was re-validated.
- **A manifest only exists for `query-metric` artifacts.** Spine-backed
  skills (funnel-dropoff, stage-timing, segment-compare, funnel-pulse) do not
  yet carry a manifest, so there is nothing for `verify` to check on their
  output.
- **The receipt proves consistency, not correctness.** A metric definition
  that was wrong from the start produces a perfectly self-consistent,
  perfectly verifiable, perfectly wrong manifest. This layer catches drift,
  not original sin.

## 8. Warn-level guardrails

**Mechanism.** `config/guardrails.yaml` holds two kinds of caveats,
auto-attached to a query rather than requiring an analyst to remember them:
`feed_cutovers` (a dated discontinuity — a launch, a version rollout, a
policy change — that a crossing time window makes look like a behavior
change) and `notes` (column, unit, join, or view-shape caveats, matched
against the columns and views a query actually touches). Everything here is
**warn**-severity: it attaches a caveat and lets the query proceed. Only R6
(PII) is refuse-severity in this system.

**What this guarantees.** Institutional knowledge that would otherwise live
only in one analyst's memory — "don't compare across this date without
knowing why" — becomes something the query path surfaces automatically to
whoever is reading the number, not just to whoever happened to write the
query.

**What this does not guarantee.**

- **It degrades to empty on a parse error, on purpose.** Advisory metadata
  that can break a query outright is worse than no metadata, so a
  malformed `guardrails.yaml` yields silence rather than a crash. That does
  mean a typo'd register fails *silently* — which is exactly why
  `tests/test_guardrails.py` asserts the shipped file parses and is
  non-empty, rather than trusting that a matcher test alone would catch a
  register gone quiet.
- **A `notes` entry with neither `applies_to_columns` nor `applies_to_views`
  is reference-only** — captured because it is true and someone will need
  it, but never auto-injected, so it never adds noise to a query it does not
  concern.

## 9. The instrumentation coverage register

**Mechanism.** `config/coverage.yaml` answers "can I measure X yet?" with a
status (`live`, `partial`, `empty`, `blocked`, `declined`, `flag_gated`), a
`blocker` verbatim, and an owner. It is read by `assay:coverage` and
`assay:load-context coverage`, and it is never a gate on anything — a missing
or unparseable register yields an empty result, same reasoning as layer 8.

**What this guarantees.** "Not in the register" and "not measurable" are kept
as distinct statements. A `partial` entry — the dangerous kind, because it
has a real number behind it that looks complete — carries its blocker
verbatim rather than being read as the whole population.

**What this does not guarantee.**

- **It is entirely advisory, hand-curated data.** Nothing enforces that the
  register stays current with what is actually instrumented; it is only as
  trustworthy as the last person who edited it and set `verified:`.
- **In this template, owners are role placeholders and ticket refs are
  fictional** (`analytics-owner`, `CHK-377`, and similar) — a public template
  has no business shipping real names or real internal ticket prefixes. A
  real deployment names actual people; a register owned by a placeholder is,
  functionally, owned by nobody.

## 10. The scrub gate

**Mechanism.** `scripts/scrub_check.py` walks every git-tracked file and
matches a fixed deny-list of tokens — the name of the private repository this
template was derived from, carrier/partner names, internal warehouse
dataset/column identifiers, internal issue-tracker project-key prefixes, and
contact addresses — case-insensitively, line by line. `tests/test_scrub_check.py`
runs it as part of `pytest`.

**What this guarantees.** The specific, known-sensitive tokens this template
was scrubbed of cannot silently re-enter the repository through a later
contribution, because the check runs on every `pytest` invocation, not just
in a separate CI job someone might skip.

**What this does not guarantee.**

- **It is a blocklist, not a sandbox**, exactly like layer 2 — it catches
  the known-bad names, not every conceivable way to describe the private
  system this was derived from. `scripts/scrub_check.py`'s own module
  docstring says this plainly: "human review of new prose is still the
  durable control."
- **It matches line by line.** A token split across a line break is not
  caught — a deliberate, documented trade for output that can name a line
  number.

## 11. The audit trail

**Mechanism.** Every `query-metric` run appends one JSON line to
`audit-log.jsonl`: the metric, tier, engine, dimensions, filters, time range,
row count, the manifest, the acting software **agent** (skill + plugin
version), and — on the BigQuery path — the authenticated user's email. The
agent/email pair is a delegation record in the sense of W3C PROV: which
software acted, on behalf of which person. Each line is folded into a
**hash chain** (`_shared/audit_chain.py`): a sidecar `audit-log.jsonl.head`
records `head_i = SHA-256(head_{i-1} || line_i)` and the entry count, and
`assay:verify --audit-log` replays the file to detect any edit, reorder,
insertion, or truncation.

**What this guarantees.** A who-ran-what-when record exists for every
governed query, append-only, and it never contains row-level data — a trace,
not a copy. With the hash chain, the log and its committed head can no longer
*silently* diverge: falsifying history means rewriting both consistently, and
a head recorded anywhere outside the file (a git commit, a printed artifact)
pins the entire prefix behind it.

**What this does not guarantee.**

- **Writing is best-effort.** An audit-log failure never blocks a query, so
  the log's completeness depends on the filesystem being writable, not on
  the query having succeeded. The head sidecar is best-effort too; a missing
  head surfaces as `CHAIN_HEAD_ABSENT` at verify time rather than as a query
  failure. A *dropped* sidecar write (log line lands, head does not) surfaces
  as `CHAIN_HEAD_BEHIND` — "committed history intact through entry N; later
  entries unattested" — kept distinct from `CHAIN_HEAD_MISMATCH` (a line in
  the committed region altered) so an ordinary I/O hiccup cannot masquerade as
  tampering, and re-committed with `adopt_log` once the tail is confirmed.
- **Adoption is genesis-only, by design.** The chain extends only from an
  up-to-date sidecar or starts on a genuinely empty log; it never re-derives a
  head by replaying an existing non-empty file. So deleting or corrupting the
  sidecar and appending planted lines does not launder them into a valid head
  on the next query — that state verifies as `CHAIN_HEAD_ABSENT`, never
  `CHAIN_OK`. Re-establishing a commitment over an existing log is the explicit
  `adopt_log`, off the append path.
- **The chain needs an external witness to be fully tamper-*evident*.** Per
  Crosby & Wallach (USENIX Security 2009), a tamper-evident log without
  auditing proves nothing: an attacker who rewrites both the log and its
  sidecar in one motion is caught only by a head they cannot reach. This
  template ships the chain and the local sidecar; where the external
  commitment lives (a git-committed head, a transparency-log backend à la
  RFC 6962) is a deployment decision. The hash chain is the necessary first
  step, not the whole of it.
- **It is local to wherever `ASSAY_OUTPUTS_DIR` resolves.** This template
  does not ship a central log store; that is deployment infrastructure a fork
  would add.

## The one thing to remember

Every layer above states its own limit honestly, and that is the point of
this document existing at all: a control whose boundary is unstated gets
trusted past where it actually reaches. Read the doctrine files directly —
`accuracy-tiers.md` and `ownership-governance.md` — for the parts of this
model that are about *people* (who owns a definition, what review cadence a
metric needs) rather than code.
