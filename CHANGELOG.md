# Changelog

Notable changes to kattfot, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
form. The version is the plugin's (`plugins/kattfot/.claude-plugin/plugin.json`); there
are no git tags yet.

## [Unreleased]

Branch `tier-0-research-pass`.

### Added
- CI: `.github/workflows/tests.yml` runs `pytest`, `scripts/scrub_check.py` and
  `scripts/demo_skills.py` on Python 3.9 and 3.12 for every push and pull request.
  The "this repo has no CI" notes in the docs and test docstrings now point at it.
- This changelog.
- Definition versioning: every metric carries `version:` and `last_reviewed:`, the
  catalog linter requires both, and `kattfot:catalog` / `load-context catalog` list
  each metric's `freshness` (`fresh` or `stale` against a 180-day window,
  `KATTFOT_REVIEW_WINDOW_DAYS`) — the `STALE:` marker
  `doctrine/ownership-governance.md` described but did not build. Missing or
  unparseable dates list as stale, never fresh. `catalog.py --today` pins the clock.
- `R1-AMBIGUOUS` is documented in `query-metric/SKILL.md` (trigger, exit code, what
  the user sees) and in the refusal table of `doctrine/accuracy-tiers.md`. The code
  has refused near-tie metric names since the research pass; the prose did not say so.
- Golden-question regression gate (Tier 1 item A): `tests/golden/questions.yaml` —
  31 cases, 93 phrasings, every bundled metric at least once, every refusal rule
  (R1, R1-AMBIGUOUS, R2–R6) at least twice, with explicit should-not-answer cases
  (T1, T3, out-of-domain, individual-level, PII). `tests/test_golden_questions.py`
  runs one test per (case, phrasing) — no pass rate — plus structural tests that
  fail by name when a metric lacks a case. Expectations are pinned by hand and
  never passed to the resolver. `query-metric/scripts/resolve_question.py` is the
  thin deterministic layer it drives (alias / keyword matching over `name`,
  `display_name` and the new per-metric `aliases:` frontmatter, then the helper's
  own `load_metric` / `validate_tier` / `validate_request`); not an LLM, and it
  queries nothing. `pytest -m regression` runs only the refusal guards.

### Changed
- The PreToolUse access-path hook declares `onFailure: block`, and both hook adapters
  (Claude Code, Cursor) deny when the matcher itself raises — a timeout or internal
  error now blocks the Bash call instead of counting as no decision.

### Fixed
- `docs/reference/research-review.md` §5 no longer attributes CI-validated metric SQL
  to Zhong et al. (a text-to-SQL evaluation methodology) — the same correction §6
  already carried. Closes HANDOFF item 5.

## [0.2.0] — 2026-10-17

The state of `main` when this changelog was started; `plugin.json` has read 0.2.0
since the exploratory lane landed on 2026-08-30. Condensed from the full history
since the first commit (2026-08-18).

- Foundation: the plugin skeleton, the governance engine — allow-listed metrics,
  dimensions and filters, accuracy tiers, refusal rules R1–R6, the PII denylist,
  per-user identity with service accounts refused in code — and the scrub gate that
  keeps a public template clean.
- The checkout module end to end on the sqlite adapter over committed fixtures; the
  spine analyses (funnel-dropoff, stage-timing, segment-compare, funnel-pulse), the
  `browse` module, and the instrumentation / coverage registers.
- Research pass: tamper-evident audit chain with PROV agent attribution, maturity
  calibration from measured settle curves, `funnel-nowcast` (Mack chain ladder with a
  self-scoring backtest), the `R1-AMBIGUOUS` refusal, an indirect-injection
  adversarial suite, and `docs/reference/research-review.md` grounding the design in
  the literature.
- Rename `assay` → `kattfot` (Sweden's assay hallmark since 1754).
- The exploratory lane: `kattfot:explore` (free-form SELECT over a fail-closed surface
  register, stamped T3) and `kattfot:promote` back to a reviewed T2 draft, plus the T3
  chart lane with the stamp riding along — 16 → 18 functional skills.
- `sqlguard` hardened: the comma-sibling register-bypass family closed by a general
  resync rather than more special cases, found by a 123-test adversarial suite for
  the free-form SQL lane.
- `kattfot:verify` learns explore artifacts (fence-recomputed `query_sha`, register
  drift); `--dry-run` cost preview on the BigQuery adapter with identity still
  asserted.
- Documentation suite, outlined skills, committed example outputs (including a
  browsable explore→promote pair), and the session handoff.
