"""Offline tests for the warehouse-bypass hook (shared matcher + both runtime adapters).

Verifies (a) real vetted skill-script invocations and ordinary dev commands are
never denied, and (b) the documented bypass vectors are denied — including
adversarial chained/nested-shell cases. Also exercises the Claude Code and Cursor
adapter stdin/stdout contracts.

The matcher is a pure blocklist: it never allow-lists by path. So every entry in
MUST_ALLOW passes because it does not *look* like warehouse access, not because
its path was recognized. That is why this test is meaningful even for skill
scripts this template has not built yet — the assertion is about command shape.

Run:  python3 -m pytest tests/test_bq_bypass_hook.py -v
  or: python3 tests/test_bq_bypass_hook.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATCHER_PATH = (
    ROOT / "plugins" / "kattfot" / "hooks" / "scripts" / "bq_path_matcher.py"
)
CLAUDE_HOOK = (
    ROOT / "plugins" / "kattfot" / "hooks" / "scripts" / "enforce_bq_path.py"
)
CURSOR_HOOK = ROOT / ".cursor" / "hooks" / "enforce_bq_path.py"

_spec = importlib.util.spec_from_file_location("bq_path_matcher", MATCHER_PATH)
matcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(matcher)

deny_reason = matcher.deny_reason

# --- Commands that must be ALLOWED ------------------------------------------

MUST_ALLOW = [
    # query-metric — every documented plugin-root resolution form.
    'python3 plugins/kattfot/skills/query-metric/scripts/kattfot_query.py --metric cart-to-checkout-started-conversion --time-range last_8_weeks --output-format json',
    'python3 "${CLAUDE_PLUGIN_ROOT}/skills/query-metric/scripts/kattfot_query.py" --metric foo --output-format json',
    'python3 .cursor/plugins/kattfot/skills/query-metric/scripts/kattfot_query.py --metric foo',
    '.venv/bin/python plugins/kattfot/skills/query-metric/scripts/kattfot_query.py --metric foo',
    # Spine-backed skills.
    'python3 plugins/kattfot/skills/funnel-dropoff/scripts/funnel_dropoff.py --funnel checkout --time-range last_4_weeks',
    'python3 plugins/kattfot/skills/funnel-pulse/scripts/funnel_pulse.py --funnel checkout',
    'python3 plugins/kattfot/skills/stage-timing/scripts/stage_timing.py --kpi time-to-order-confirmed-days --time-range last_8_weeks',
    'python3 plugins/kattfot/skills/stage-timing/scripts/calibrate_maturity.py --kpi time-to-order-confirmed-days',
    'python3 plugins/kattfot/skills/segment-compare/scripts/segment_compare.py --mode ab --segment-a channel=paid_social --segment-b channel=email',
    'python3 plugins/kattfot/skills/reconcile/scripts/reconcile_number.py --metric payment_approval_rate --their-value 0.81 --time-range last_8_weeks',
    # setup doctor (offline + live probe).
    'python3 plugins/kattfot/skills/setup/scripts/doctor.py',
    'python3 plugins/kattfot/skills/setup/scripts/doctor.py --probe',
    # Chained sanctioned scripts.
    'python3 plugins/kattfot/skills/setup/scripts/doctor.py && python3 plugins/kattfot/skills/query-metric/scripts/kattfot_query.py --metric foo',
    # Required auth / deps — must never be blocked.
    "gcloud auth application-default login",
    "gcloud config set project my-warehouse-uat",
    "gcloud --version",
    "pip install --user google-cloud-bigquery",
    "pip3 install --user google-cloud-bigquery pyyaml",
    # Non-warehouse plugin scripts (command text never names the client library).
    "python3 plugins/kattfot/skills/verify/scripts/verify_artifact.py outputs/kattfot/foo.md",
    "python3 plugins/kattfot/skills/visualize-metric/scripts/chart_spec.py --artifact foo.md",
    "python3 plugins/kattfot/skills/render-chart/scripts/render_html.py --spec charts/foo.spec.json",
    "python3 plugins/kattfot/skills/render-dashboard/scripts/render_dashboard.py --spec foo.dashboard.json",
    # Offline test suites.
    "python3 -m pytest tests/test_pii_refusal.py",
    "python3 tests/test_bq_bypass_hook.py",
    # The repo's own scrub gate.
    "python3 scripts/scrub_check.py",
    # Ordinary dev commands.
    'git commit -m "fix kattfot skill wording"',
    'git commit -m "Harden BigQuery access docs"',
    "git status",
    "ls -la plugins/kattfot/skills/query-metric",
    "cat plugins/kattfot/skills/query-metric/scripts/kattfot_query.py",
    'grep -rn "bigquery" README.md',
    "curl -s https://api.github.com/repos/markreid-lab/kattfot",
    "wget https://example.com/file.txt",
    # Path fragments that merely contain "dbt"/"mf" as a substring or a path
    # component, never as a command token, must stay allowed.
    "cat vendor/data-dbt/dbt/profiles.yml",
    "cat vendor/data-dbt/dbt/dbt_project.yml",
    "find vendor/data-dbt/dbt/models -iname '*checkout*'",
    "grep -rn semantic_model vendor/data-dbt/dbt",
    "ls vendor/data-dbt",
    "cd vendor/data-dbt && git log --oneline -5",
    # Prose that merely NAMES the tool, with no real subcommand after it. The
    # first two are the shapes that tripped an earlier `dbt\\s+\\S` matcher while
    # documenting a roadmap: a plain space satisfies the boundary class, so any
    # "dbt <english word>" denied itself.
    'gh pr create --body "| dbt promotion | Shipped |"',
    'grep -n "dbt\\|mf " tests/test_bq_bypass_hook.py',
    'git commit -m "docs: promote the views into the dbt DAG"',
    'git commit -m "docs(kattfot): dbt-ize the two hand-deployed views"',
    'git commit -m "note that dbt models now carry schema tests"',
    "echo 'the dbt project lives in a separate repo'",
    # Flags without a subcommand never reach the warehouse.
    "dbt --version",
    "mf --help",
    # Prose naming a tool AND a real subcommand, inside a quoted argument to a
    # command that does not execute it. Previously denied ("accepted false
    # positive"); now allowed, because the token is not at a command position.
    'git commit -m "bq query typo fix"',
    'git commit -m "fix the dbt run failure"',
    'git commit -m "document the bq ls fallback"',
    'gh pr create --title "hook: stop denying bq query in prose"',
    'gh pr create --body "we no longer run mf query by hand"',
    'git commit -m "note the gcloud bigquery auth flow"',
    "echo 'run bq query only via the vetted script'",
]

# --- Commands that must be DENIED -------------------------------------------

MUST_DENY = [
    'bq query --use_legacy_sql=false "SELECT email FROM `proj.ds.t`"',
    "bq ls",
    "bq show proj:dataset.table",
    "bq head -n 10 proj:dataset.table",
    'gcloud alpha bq query "SELECT 1"',
    "gcloud services enable bigquery.googleapis.com",
    "python3 -c \"from google.cloud import bigquery; bigquery.Client().query('SELECT email FROM t').result()\"",
    'python3 -c "import bigquery; print(1)"',
    "python3 -c \"print('bigquery is great')\"",
    "python3 my_script.py  # imports google.cloud.bigquery directly",
    "curl https://bigquery.googleapis.com/bigquery/v2/projects/x/datasets",
    "wget https://www.googleapis.com/bigquery/v2/projects/x/queries",
    # Adversarial: chain a real sanctioned call with a bypass afterward.
    'python3 plugins/kattfot/skills/query-metric/scripts/kattfot_query.py --metric foo && bq query "SELECT email FROM t"',
    'python3 plugins/kattfot/skills/setup/scripts/doctor.py --probe; bq ls',
    'bash -c "bq query \'SELECT email FROM t\'"',
    'sh -c "bq ls"',
    "bash -c \"python3 -c 'from google.cloud import bigquery; bigquery.Client()'\"",
    'sh -c "python3 -c \'import bigquery\'"',
    # Command position via an operator, a subshell, or a newline — none of these
    # rely on a bare space, so narrowing to `_CMD_POS` must not lose them.
    "echo start; bq ls",
    "echo start && bq ls",
    "echo start || bq ls",
    "false | bq query 'SELECT 1'",
    "(bq ls)",
    "$(bq ls)",
    "`bq ls`",
    "echo start\nbq ls",
    "  bq ls",
    # Prefix commands and env assignments in front of the real invocation: the
    # bypass surface that narrowing to a command position could have opened.
    "env bq ls",
    "env FOO=1 bq ls",
    "FOO=1 bq ls",
    "sudo bq ls",
    "sudo -u someone bq ls",
    "time bq query 'SELECT 1'",
    "timeout 30 bq ls",
    "nohup bq ls",
    "watch -n 5 bq ls",
    "command bq ls",
    "exec bq ls",
    "for f in a b; do bq ls; done",
    "env dbt run",
    "sudo mf query --metrics foo",
    # Compound constructs whose command position is not a plain operator. Each
    # of these leaked when the command-position narrowing first went in, and was
    # found by adversarially attacking the new matcher rather than by review.
    "{ bq ls; }",
    "case x in y) bq ls;; esac",
    "if true; then bq ls; fi",
    "while true; do bq ls; done",
    "sleep 1 & bq ls",
    "echo a && \\\n  bq ls",
    "\tbq ls",
    "echo $(bq ls)",
    "cat <(bq query 'SELECT 1')",
    "{ dbt run; }",
    "{ gcloud alpha bq query 'SELECT 1'; }",
    # Prefix stacking, and flags that take a space-separated value.
    "nohup env bq ls",
    "sudo -u x env FOO=1 bq ls",
    "env -i bq ls",
    "nice -n 10 bq ls",
    "PATH=/x:/y bq ls",
    "stdbuf -oL bq query 'SELECT 1'",
    "sudo gcloud services enable bigquery.googleapis.com",
    # Shell wrappers execute their quoted argument, so the blunt boundary class
    # still applies inside them.
    'eval "bq ls"',
    "xargs -I{} bq query {}",
    'ssh host "bq ls"',
    'zsh -c "bq ls"',
    'bash -lc "dbt run"',
    "sh -ec 'bq ls'",
    "find . -name '*.sql' -exec bq query {} \\;",
    "find . -execdir bq ls \\;",
    "parallel bq ls ::: a b",
    # dbt/mf CLI invocations reach the warehouse outside the plugin's
    # identity/PII helpers, same class of risk as bare `bq`/`gcloud`.
    "dbt run --select stg_checkout_events",
    "dbt debug",
    "dbt show --inline \"select 1\"",
    "cd vendor/data-dbt && dbt run",
    "mf query --metrics cart_to_checkout_started_conversion --group-by metric_time",
    "mf health-checks",
    'python3 plugins/kattfot/skills/query-metric/scripts/kattfot_query.py --metric foo && dbt run',
    'bash -c "dbt run --select foo"',
    'sh -c "mf query --metrics foo"',
    # Global flags before the subcommand, including a flag taking a
    # space-separated value — the shape the subcommand list must still catch.
    "dbt --profiles-dir vendor/data-dbt run",
    "dbt --debug run --select checkout_funnel_weekly",
    "dbt -t prod build",
    "dbt --profiles-dir ./x --target prod test",
    "mf --quiet query --metrics cart_to_checkout_started_conversion",
    # Other warehouse-touching subcommands.
    "dbt compile",
    "dbt parse",
    "dbt docs generate",
    "dbt run-operation my_macro",
    "mf validate-configs",
    "mf list metrics",
]


def test_must_allow():
    failures = []
    for cmd in MUST_ALLOW:
        reason = deny_reason(cmd)
        if reason is not None:
            failures.append(f"allowed: {cmd!r} (got denial: {reason!r})")
    assert not failures, "\n".join(failures)


def test_must_deny():
    failures = []
    for cmd in MUST_DENY:
        reason = deny_reason(cmd)
        if reason is None:
            failures.append(f"denied: {cmd!r} (got allow)")
    assert not failures, "\n".join(failures)


def _run_hook(hook_path: Path, payload: dict) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout.strip()


def test_claude_adapter_deny_and_allow():
    code, out = _run_hook(
        CLAUDE_HOOK,
        {"tool_name": "Bash", "tool_input": {"command": "bq ls"}},
    )
    assert code == 0
    assert out
    parsed = json.loads(out)
    decision = parsed.get("hookSpecificOutput", {})
    assert decision.get("permissionDecision") == "deny"
    assert decision.get("hookEventName") == "PreToolUse"
    assert decision.get("permissionDecisionReason")

    code, out = _run_hook(
        CLAUDE_HOOK,
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 plugins/kattfot/skills/query-metric/scripts/kattfot_query.py --metric foo",
            },
        },
    )
    assert code == 0
    assert out == ""

    code, out = _run_hook(
        CLAUDE_HOOK,
        {"tool_name": "Write", "tool_input": {"file_path": "x.py"}},
    )
    assert code == 0
    assert out == ""


def test_cursor_adapter_deny_and_allow():
    code, out = _run_hook(CURSOR_HOOK, {"command": "bq ls"})
    assert code == 0
    parsed = json.loads(out)
    assert parsed.get("permission") == "deny"
    assert parsed.get("user_message")
    assert parsed.get("agent_message")

    code, out = _run_hook(
        CURSOR_HOOK,
        {
            "command": "python3 plugins/kattfot/skills/funnel-dropoff/scripts/funnel_dropoff.py --funnel checkout",
        },
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed.get("permission") == "allow"


def test_malformed_stdin_fail_open():
    for hook_path in (CLAUDE_HOOK, CURSOR_HOOK):
        proc = subprocess.run(
            [sys.executable, str(hook_path)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
