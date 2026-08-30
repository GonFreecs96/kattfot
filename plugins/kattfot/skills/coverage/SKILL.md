---
name: coverage
description: >
  Triggers on: "can I measure X", "is X instrumented", "do we have data for X",
  "what can't I measure yet", "coverage for X", "is there a feed for X". Answers
  whether a field, event, or stream is measurable today — and if not, why not and
  who owns the blocker — from the instrumentation coverage register. The
  stakeholder-facing companion to the metric catalog: catalog says what you CAN
  ask, coverage says what you cannot yet and what to watch. Never queries anything.
version: 0.1.0
---

# Coverage

You are running the `coverage` skill. Answer **"can I measure X yet?"** from the
register, so a stakeholder learns — without the question routing through an analyst
who then answers it from memory — whether something is instrumented, and if not, why
and who owns the blocker.

**You never execute a query.** For a live number the user wants a metric, a spine
skill, or an explicitly exploratory ad-hoc read.

## Run the helper

```bash
# the whole register, grouped by status — the triage view
python3 plugins/kattfot/skills/coverage/scripts/coverage_lookup.py --format markdown

# match a stakeholder's phrasing
python3 plugins/kattfot/skills/coverage/scripts/coverage_lookup.py \
  --term "why was the cart abandoned" --format markdown

# resolve one entry exactly
python3 plugins/kattfot/skills/coverage/scripts/coverage_lookup.py \
  --id cart_abandon_reason --format markdown
```

Register: `config/coverage.yaml` (override: `KATTFOT_COVERAGE_FILE`). Loader and
matcher: `_shared/coverage.py`. Exit 0 on a hit, 1 on a clean no-match, so a script
can branch.

## Status meanings

| status | means | what to tell the user |
|---|---|---|
| `live` | queryable now | **Yes.** Point at the registered metric or spine skill in `serve`. |
| `partial` | queryable, known coverage hole | **Yes, but.** Give the limit from `blocker` verbatim — it is a subset or a lower bound, not the population. |
| `empty` | schema wired, zero rows in production | **Not yet.** Name the gap and the thing to watch from `blocker`. |
| `blocked` | not instrumented | **No.** Give the blocker and the owner; cite the ref. |
| `declined` | product/engineering declined it | **No, and it is not coming.** Record the decision so the ask stops recurring. |
| `flag_gated` | behind a flag or a dated launch | **Not yet.** Give the gating flag or launch from `blocker`. |

## Process

1. **Parse the ask** — the thing the user wants to measure: a field, an event, a
   stream, or a plain-English capability ("why do carts get abandoned").
2. **No argument → list everything.** Present the helper's status-grouped table.
   This doubles as the "what is blocked and on whom" triage view.
3. **With an ask → match.** `--term` does a case-insensitive substring match over
   each entry's `id`, `asks`, `fields`, and `streams`. Try the most specific phrase
   first; if nothing matches, retry with the key noun. If the user names an id, use
   `--id`.
4. **Answer per the status.** Always:
   - state the verdict in the first line (Yes / Yes-but / Not yet / No);
   - give the `blocker` **verbatim** for anything not `live` — it carries the owner,
     the ref, and the specific limit, and a summarised blocker is how a hard "no"
     becomes a soft "probably later";
   - surface the `blocker_ref` and `owner` so they can follow the upstream work;
   - note the `verified:` date, so they know how current the answer is.
   If several entries match, list them briefly and let the user pick.
5. **No match → route, do not guess.** Say plainly that it is not in the register,
   then give the two real paths: `kattfot:catalog` for the governed metrics that *are*
   available, and an explicitly exploratory ad-hoc read for anything else. **Never
   invent a coverage verdict for something unlisted** — "not in the register" and
   "not measurable" are different statements, and conflating them turns a gap in the
   register into a false fact about the data.

## The trap this skill exists to prevent

A `partial` entry has a number behind it, and that number is the dangerous kind: it
looks complete. `return_reason_codes` in this register is the worked example — reason
codes exist for the ~31% of returns opened through the self-service portal, and the
missing 69% are not random, they skew toward the segments that do not self-serve. So
the reason mix is real *within the portal cohort* and misleading as "why customers
return".

Similarly, never present a plausible substitute as the thing itself. The register
spells this out for `cart_abandon_reason`: `terminal_branch = '2 Checkout Started, No
Payment'` is the *shape* of abandonment, not a reason for it, and reporting it as one
is how a stalled payment form gets read as price sensitivity.

## What this skill must NOT do

- **Never run a query or call a query helper.** This skill reads the register only.
- **Never invent coverage** for an unlisted field — return the no-match routing.
- **Never present a `partial` entry's number as complete, or an `empty` entry as
  live.** The whole point is to stop a caveat-laden number being read as truth.
- **Never soften a `declined`.** It is a recorded decision, and re-opening it is a
  conversation with the owner, not an analytics task.

## Notes

- Owners in this template are role placeholders and the ticket keys are fictional —
  a public template has no business carrying names. A real register names a person,
  because "who owns this" is the whole value of the field.
- A missing or unparseable register yields an empty result rather than an error,
  because advisory metadata that can break a caller is worse than none. That does
  mean a typo'd register degrades to silence, which is why
  `tests/test_coverage.py` asserts the shipped file parses and is non-empty rather
  than only testing the matchers in isolation.
