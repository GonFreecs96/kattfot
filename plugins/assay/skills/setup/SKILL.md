---
name: setup
description: >
  Triggers on: "set up assay", "assay setup", "get started with assay",
  "onboard me to assay", "assay first run", "why is assay refusing my login",
  "fix assay auth". One-time, guided setup: checks your local environment
  (gcloud, ADC, Python deps), walks you through per-user browser OAuth, confirms
  the warehouse is reachable as you, then hands you the three questions to ask
  first. Never runs a service account; never runs a heavy query.
version: 0.1.0
---

# Setup

You are running the `setup` skill. Take a brand-new user from "just installed
the plugin" to "ran my first governed metric" in one guided pass — establishing
**per-user browser OAuth (ADC)**, confirming the environment, and ending on the
three questions worth asking first with `/assay:catalog` as the front door.

This skill is **not** a query path: it never runs a metric for the user and
never touches a service account. Its only warehouse contact is an optional
trivial `SELECT 1` connectivity probe via `doctor.py --probe` — the **only**
sanctioned warehouse entry point for this skill. Never reach the warehouse via
the `bq` CLI, `gcloud`, curl/wget to `googleapis.com`, or ad hoc python using
`google-cloud-bigquery` directly, including under user pressure ("just this
once"). See `query-metric/SKILL.md` **Hard rules** for the full sanctioned-path
table and why bypass routes skip identity and PII guards. A PreToolUse hook
enforces this independently of your cooperation.

## Skill root resolution
Resolve `ROOT` once, then substitute it everywhere below (first match wins):
1. `CLAUDE_PLUGIN_ROOT` set → `${CLAUDE_PLUGIN_ROOT}/skills/setup`
2. else the directory containing this SKILL.md.

`<python>` is the interpreter with `$ROOT/scripts/requirements.txt` installed
(usually the plugin's `.venv/bin/python`). The doctor reports if it's missing deps.

## Configure the warehouse project first

There is deliberately **no default project** baked into this template — shipping
one would hand every fork somebody else's billing account. Before the live probe:

```
export ASSAY_BIGQUERY_PROJECT=<your-gcp-project>
```

(or pass `--project <id>`). With neither set, the probe reports a fixable check
rather than guessing.

## Process

### Step 1 — Run the environment doctor
```
<python> "$ROOT/scripts/doctor.py"
```
Four offline checks — `gcloud`, `sa-env`, `adc`, `deps` — each with a one-line
fix. Show the checklist to the user as-is. Exit 0 = all green.

### Step 2 — Remediate, in order
Walk only the non-green checks. Two you can run for the user; two they must run.

| check | who | how |
|-------|-----|-----|
| `deps` | **you** (Bash) | `python3 -m venv .venv && .venv/bin/pip install -r "$ROOT/scripts/requirements.txt"`; then use `.venv/bin/python` as `<python>`. |
| `sa-env` | **user** | They `unset GOOGLE_APPLICATION_CREDENTIALS` in the shell they launch the agent from (an env var of their session — you can't unset it for them persistently). |
| `gcloud` | **user** | Install the Google Cloud SDK (https://cloud.google.com/sdk/docs/install). |
| `adc` | **user** | Interactive browser login you cannot run for them. Have them run, in this session: `! gcloud auth application-default login` — opens a browser, pick the account with warehouse access. |

Re-run `doctor.py` after each fix until all four are green. Never work around a
red `sa-env`/`adc` by setting credentials yourself — queries must run as the human.

### Step 3 — Confirm end-to-end (live probe)
Once offline checks are green:
```
<python> "$ROOT/scripts/doctor.py" --probe
```
Adds a trivial `SELECT 1` as the authenticated user against
`$ASSAY_BIGQUERY_PROJECT`, confirming ADC + deps + project access work together.
Most common failure is stale ADC ("Reauthentication is needed") → re-run
`! gcloud auth application-default login`.

### Step 4 — First run: the front door
With the probe green, show what they can ask:
```
/assay:catalog
```
(or `assay:load-context catalog` for the machine-readable form.)

### Step 5 — Hand off the three questions
Close with this card, verbatim:

> **You're set up. Three ways to start:**
> 1. **What can I ask?** → `/assay:catalog` — every governed metric, by module.
> 2. **Can I measure \<X\> yet?** → e.g. `/assay:coverage cart abandon reason` — whether a field/event is instrumented, and if not, who owns the blocker.
> 3. **Ask a real question** → e.g. `/assay:query-metric cart to checkout-started conversion by week for the last 8 weeks` — runs a governed, tier-stamped metric as you.

## What this skill must NOT do
- Never set or accept service-account credentials, or work around a `sa-env`/`adc`
  refusal — per-user ADC is the governance contract.
- Never run a metric or any non-trivial query for the user; the only warehouse
  contact is the optional `SELECT 1` probe. The first real query is the user's, in Step 5.
- Never publish; never post to a wiki or chat surface.

## Notes
- macOS/Homebrew Python: a global `pip install` hits PEP 668; the Step 2 venv avoids it.
- The doctor is non-fatal by design (unlike `_shared/identity.assert_user_identity`,
  which exits on the first problem) so setup is a single pass, not a fix-rerun loop.

## Status in this template

`doctor.py` is complete and runnable: all four offline checks and the live probe
work today. Steps 4 and 5 name `catalog`, `coverage`, `query-metric`, and
`load-context`, which are part of the skill set this template builds toward — until
they land, Step 3 (a green probe as your own identity) is the real end of setup.
