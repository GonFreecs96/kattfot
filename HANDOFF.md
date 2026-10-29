# Handoff — 2026-08-31

Pause point after a multi-agent session that shipped four review-gated features
plus a committed example pair. Everything below is **merged to `main` and pushed**
(`origin/main` == `1d06b63`). Full suite green at **1405 tests** (was 1249 at the
session's base commit `c22a5b8`). Python 3.9; run with
`.venv/bin/python -m pytest`.

## What shipped this session

Each task ran as a lower-model implementer in an isolated worktree, gated by a
higher-model reviewer, with findings routed back until clean. Merge commits keep
the per-feature fix history.

| Feature | Merge | What it added |
|---|---|---|
| `kattfot:verify` learns explore artifacts | `49eb0d2` | Recomputes `query_sha` from an explore artifact's Provenance fences (via a parser **shared** with `promote` — `_shared/explore_artifact.py`) and flags register drift. Sentinels: `PROVENANCE_TAMPERED`, `REGISTER_DRIFT`, `REGISTER_MISSING`, `PROVENANCE_ABSENT`. |
| Adversarial SQL-lane suite | `9e2f211` | 123 attacker-shaped tests in `tests/test_explore_adversarial.py`; **found and closed a real register bypass** (see below). |
| `--dry-run` cost preview | `cdaaf65` | New `CAP_DRY_RUN` adapter capability; BigQuery `dry_run=True` byte estimate with identity still asserted; guards-before-cost ordering; honest no-cost-dimension path on sqlite that doubles as an offline governance lint. |
| Exploratory-lane research grounding | `de8c9b7` | New §6 in `docs/reference/research-review.md`, web-verified citations only. |
| Browsable explore→promote example pair | `1d06b63` | `examples/explore-artifact.md`, `explore-chart.html` (EXPLORATORY+MOCK stamped), `explore-chart-spec.json`, `promoted-metric-draft.md`. `kattfot:verify examples/explore-artifact.md` → `VERIFY_OK`. |

### The security fix worth knowing about
The adversarial suite exposed a genuine register bypass in
`_shared/sqlguard.py::referenced_tables` — a table comma-joined after a
paren-bearing FROM element escaped the register/PII/select-star checks and could
read an **unregistered warehouse surface** (proven end-to-end, and live on
BigQuery). It took **four rounds** to fully close because each fix exposed a
sibling: `(subquery), t` → `UNNEST(...), t` → `JOIN…ON…, t` →
`` `order`, t `` (backticked reserved word). The final fix is a root-cause
redesign — `_resync_to_comma` scans to the next depth-0 comma or real clause
boundary — that closes the whole family structurally. **This is the key lesson
for next steps (#1).**

## Next steps (prioritized)

### 1. Retire the lexical-guard bypass class — DESIGN DECISION NEEDED FIRST
`referenced_tables` is a deliberately *lexical* table-extractor
(`sqlguard.py` docstring: "lexical, blunt, honest about it"), backstopped by
per-user identity + warehouse ACLs. The four-round fix showed patching is
whack-a-mole. Two directions — **pick one before coding** (per the
align-before-implementing norm):
- **(a) Parser-based extraction** (e.g. `sqlglot`) for `referenced_tables` and
  the R6 identifier scan. Eliminates the whole bypass class *and* the two gaps
  still pinned-not-fixed this session: the `strip_noise` BigQuery-vs-sqlite
  lexer desync (`test_explore_adversarial.py`, section ~2 pins) and the
  JSON-path PII gap. Cost: a real philosophy shift + a new dependency.
- **(b) Fail-closed variant (recommended, lighter):** when the guard cannot
  confidently parse a FROM clause, **refuse** rather than silently
  under-report. The adversarial reviewer named this the acceptable fallback. It
  removes the "silent bypass" property without a parser rewrite.

### 2. Real BigQuery smoke test
Everything warehouse-facing (`--dry-run`, `assert_user_identity`, the adapter)
is tested offline against a **fake** client — the venv has no
`google-cloud-bigquery`. One opt-in integration test against a live project
would confirm the real `dry_run=True` job statistics match what the fake
assumes (`bigquery_adapter.py::dry_run` reads `job.total_bytes_processed`
without `.result()`).

### 3. Egress leg of the lethal trifecta
`tests/test_adversarial_injection.py` (section 5) still pins the
exfiltration/egress channel as an **open** gap — the one security gap the suite
explicitly asserts is unclosed. Deployment-level (network egress allow-list); if
ever added, that test flips from "assert not caught" to "assert blocked."

### 4. Pre-existing roadmap (unchanged this session, from README)
- Three outline-only skills: `release-scorecard`, `customer-journey`, `publish`.
- `browse` module: `declared` → `live` (needs a view/fixture + funnel spec).

### 5. Small loose end — closed 2026-10-28 (`tier-0-research-pass`)
Pre-existing Zhong et al. over-reach in `research-review.md` **§5 (~line 227)** —
out of scope for this session's §6 diff, but it's the same misattribution §6
fixed. Dropped the attribution the same way §6 did; the paper stays in Sources
as a text-to-SQL evaluation reference.

## How to resume the review-gated workflow
Implementer (Sonnet) in an isolated git worktree → reviewer (Opus) on the diff →
route findings back → merge with `--no-ff` → full suite → repeat. For a genuine
guarantee (like #1's security property), have the reviewer *re-attack* the fix,
not just read it — that's what caught rounds 2–4.
