#!/usr/bin/env python3
"""kattfot:setup environment doctor — non-fatal preflight checks.

Unlike `_shared/identity.assert_user_identity()` (which exits on the first
problem so a query never runs misauthenticated), this reports the FULL state of
the local setup at once so `/kattfot:setup` can walk a user through every gap
in a single pass. No offline check aborts; each returns a structured result.

Checks (all offline unless --probe):
  gcloud   — gcloud CLI on PATH
  sa-env   — GOOGLE_APPLICATION_CREDENTIALS not set (would force a service account)
  adc      — Application Default Credentials present and an `authorized_user`
  deps     — pyyaml + google-cloud-bigquery importable in this interpreter

With --probe (network): run a trivial `SELECT 1` as the authenticated user to
confirm ADC + deps + project access end-to-end. Auto-skipped if the offline
checks aren't all green.

There is deliberately **no default warehouse project**: this is a template, so
the project must come from `KATTFOT_BIGQUERY_PROJECT` or `--project`. Baking one
in would ship somebody else's billing account in the repo.

Exit code: 0 if every check is ok, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Make `_shared` importable when invoked directly (mirrors the query helper).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ADC_PATH = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
# No fallback on purpose — see the module docstring. None means "not configured",
# which the probe reports as a fixable check rather than guessing a project.
DEFAULT_PROJECT = os.environ.get("KATTFOT_BIGQUERY_PROJECT")

OK, FAIL, WARN = "ok", "fail", "warn"


def _r(check, status, detail, fix=None):
    return {"check": check, "status": status, "detail": detail, "fix": fix}


def check_gcloud():
    if shutil.which("gcloud"):
        return _r("gcloud", OK, "gcloud CLI found on PATH")
    return _r("gcloud", FAIL, "gcloud CLI not found on PATH",
              "Install the Google Cloud SDK: https://cloud.google.com/sdk/docs/install")


def check_sa_env():
    val = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not val:
        return _r("sa-env", OK, "GOOGLE_APPLICATION_CREDENTIALS is not set")
    return _r("sa-env", FAIL,
              f"GOOGLE_APPLICATION_CREDENTIALS is set ({val}) — this would run "
              "queries as a service account, which the plugin refuses.",
              "unset GOOGLE_APPLICATION_CREDENTIALS (in the shell you launch Claude from)")


def check_adc(path=ADC_PATH):
    if not os.path.exists(path):
        return _r("adc", FAIL, "No Application Default Credentials found.",
                  "gcloud auth application-default login")
    try:
        with open(path) as f:
            adc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return _r("adc", FAIL, f"Could not read ADC file at {path}: {e}",
                  "gcloud auth application-default login")
    ctype = adc.get("type", "")
    if ctype == "authorized_user":
        return _r("adc", OK, "ADC present and an authorized-user login")
    if ctype == "service_account":
        return _r("adc", FAIL,
                  f"ADC is a service account ({adc.get('client_email', 'unknown')}) — refused.",
                  "gcloud auth application-default login")
    if ctype == "impersonated_service_account":
        return _r("adc", FAIL, "ADC is configured to impersonate a service account — refused.",
                  "gcloud config unset auth/impersonate_service_account && "
                  "gcloud auth application-default login")
    return _r("adc", FAIL, f"ADC type '{ctype}' is not a standard user login.",
              "gcloud auth application-default login")


def check_deps():
    missing = []
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("pyyaml")
    try:
        import google.cloud.bigquery  # noqa: F401
    except ImportError:
        missing.append("google-cloud-bigquery")
    if not missing:
        return _r("deps", OK, "pyyaml and google-cloud-bigquery importable")
    return _r("deps", FAIL,
              f"Missing Python deps in this interpreter: {', '.join(missing)}",
              "create a venv and install the requirements.txt of the skill you are "
              "running, then invoke its helpers with that interpreter")


def run_checks():
    return [check_gcloud(), check_sa_env(), check_adc(), check_deps()]


def probe_bigquery(project=DEFAULT_PROJECT):
    """Live end-to-end check: SELECT 1 as the authenticated user (network)."""
    if not project:
        return _r("probe", FAIL, "No warehouse project configured.",
                  "export KATTFOT_BIGQUERY_PROJECT=<your-gcp-project> (or pass --project)")
    try:
        from _shared.identity import assert_user_identity
        from google.cloud import bigquery
    except ImportError as e:
        return _r("probe", FAIL, f"Cannot import deps for probe: {e}")
    assert_user_identity()  # exits 2/4 if somehow misauthenticated
    try:
        client = bigquery.Client(project=project)
        rows = list(client.query("SELECT 1 AS ok").result())
        if rows and rows[0]["ok"] == 1:
            return _r("probe", OK, f"BigQuery reachable as you (project {project})")
        return _r("probe", FAIL, "SELECT 1 returned an unexpected result")
    except Exception as e:  # noqa: BLE001
        return _r("probe", FAIL, f"BigQuery probe failed against {project}: {e}",
                  "Confirm project access and that your ADC is fresh "
                  "(gcloud auth application-default login).")


def render_text(results):
    glyph = {OK: "✓", FAIL: "✗", WARN: "!"}
    lines = ["kattfot — setup doctor", ""]
    for r in results:
        lines.append(f"  {glyph.get(r['status'], '?')} {r['check']:<8} {r['detail']}")
        if r["status"] != OK and r.get("fix"):
            lines.append(f"      fix: {r['fix']}")
    lines.append("")
    lines.append("All checks passed." if all(r["status"] == OK for r in results)
                 else "Some checks need attention (see fixes above).")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="kattfot setup doctor")
    ap.add_argument("--probe", action="store_true",
                    help="also run a live SELECT 1 (network) once offline checks pass")
    ap.add_argument("--project", default=DEFAULT_PROJECT,
                    help="warehouse project for --probe (default: $KATTFOT_BIGQUERY_PROJECT)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    results = run_checks()
    offline_ok = all(r["status"] == OK for r in results)
    if args.probe and offline_ok:
        results.append(probe_bigquery(args.project))
    elif args.probe:
        results.append(_r("probe", WARN, "skipped — fix the offline checks above first"))

    if args.format == "json":
        print(json.dumps({"results": results,
                          "ok": all(r["status"] == OK for r in results)}, indent=2))
    else:
        print(render_text(results))
    sys.exit(0 if all(r["status"] == OK for r in results) else 1)


if __name__ == "__main__":
    main()
