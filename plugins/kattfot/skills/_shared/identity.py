"""Shared user-identity enforcement for skills in the kattfot plugin.

Every BigQuery-touching skill in this plugin runs as the individual human user
who authenticated via `gcloud auth application-default login`. Service-account
credentials are refused at the helper level so all queries are attributable to
a person, not a shared key.

Public functions:
  assert_user_identity()  -> Refuse non-user credentials. Returns ADC dict on success.
                             Exits 2 if ADC missing, 4 if a service account.
  lookup_email()          -> Return (email, scopes) for the authenticated user.

Exit codes (consumed by SKILL.md flows):
  2 = ADC missing — user must run `gcloud auth application-default login`
  3 = Python deps missing — install `google-cloud-bigquery`
  4 = Credentials are a service account / impersonation — REFUSED
"""

from __future__ import annotations

import json
import os
import sys


def assert_user_identity() -> dict:
    """Refuse non-user credentials. Returns ADC dict on success.

    Guards against:
      - GOOGLE_APPLICATION_CREDENTIALS env var (points at SA key file)
      - ADC file of type 'service_account'
      - Impersonated service accounts (type 'impersonated_service_account')
      - External / workload-identity accounts
    """
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        sys.stderr.write(
            "REFUSED: GOOGLE_APPLICATION_CREDENTIALS is set, which would make "
            "queries run as a service account instead of you.\n"
            "Unset it (`unset GOOGLE_APPLICATION_CREDENTIALS`) and run "
            "`gcloud auth application-default login` to authenticate as yourself.\n"
        )
        sys.exit(4)

    adc_path = os.path.expanduser(
        "~/.config/gcloud/application_default_credentials.json"
    )
    if not os.path.exists(adc_path):
        sys.stderr.write(
            "No Application Default Credentials found.\n"
            "Run: gcloud auth application-default login\n"
        )
        sys.exit(2)

    try:
        with open(adc_path) as f:
            adc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"Could not read ADC file at {adc_path}: {e}\n")
        sys.exit(2)

    cred_type = adc.get("type", "")
    if cred_type == "service_account":
        sys.stderr.write(
            "REFUSED: Application Default Credentials are a service account "
            f"({adc.get('client_email', 'unknown')}).\n"
            "This skill only runs as the individual human user. Run:\n"
            "  gcloud auth application-default login\n"
        )
        sys.exit(4)
    if cred_type == "impersonated_service_account":
        sys.stderr.write(
            "REFUSED: ADC is configured to impersonate a service account.\n"
            "This skill only runs as the individual human user. Run:\n"
            "  gcloud auth application-default login --no-impersonate-service-account\n"
        )
        sys.exit(4)
    if cred_type != "authorized_user":
        sys.stderr.write(
            f"REFUSED: ADC type '{cred_type}' is not a standard user login.\n"
            "Run: gcloud auth application-default login\n"
        )
        sys.exit(4)

    return adc


def lookup_email() -> tuple[str, list[str]]:
    """Return (email, scopes) for the currently-authenticated user.

    Uses the `requests` library (a transitive dep of google-cloud-bigquery)
    so SSL roots come from `certifi` rather than the system store, which
    is unreliable on macOS Python builds.
    """
    try:
        import google.auth
        import google.auth.transport.requests
        import requests
    except ImportError:
        sys.stderr.write(
            "Missing dependencies. Install with:\n"
            "  pip install google-cloud-bigquery\n"
        )
        sys.exit(3)

    creds, _ = google.auth.default()
    creds.refresh(google.auth.transport.requests.Request())
    try:
        r = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"access_token": creds.token},
            timeout=10,
        )
        r.raise_for_status()
        info = r.json()
    except Exception as e:
        sys.stderr.write(f"Could not reach Google tokeninfo endpoint: {e}\n")
        sys.exit(1)
    email = info.get("email") or info.get("sub") or "(unknown)"
    scopes = (info.get("scope") or "").split()
    return email, scopes
