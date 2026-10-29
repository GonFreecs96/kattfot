"""Setup doctor: non-fatal environment checks for `/kattfot:setup`.

Unlike `_shared.identity.assert_user_identity` (which exits), the doctor reports
the full state at once. These tests lock the per-check verdicts that drive the
skill's remediation table — no network and no real gcloud:

  - check_adc:    authorized_user -> ok; service_account / impersonated /
                  non-user / missing -> fail (with a remediation `fix`)
  - check_sa_env: GOOGLE_APPLICATION_CREDENTIALS set -> fail, unset -> ok
  - check_gcloud: PATH presence via a stubbed shutil.which
  - render_text:  every non-ok check surfaces its fix line

`deps` is intentionally not asserted here: CI installs no cloud client on purpose,
and a bare test environment may have neither google-cloud-bigquery nor network
either way, so the doctor's value is the offline verdicts.
The `probe` path IS asserted for one thing only — that it refuses to guess a
warehouse project (see the last section).
"""

import json
import os

import pytest

import doctor


@pytest.fixture
def adc(tmp_path, monkeypatch):
    """Point HOME at a temp dir and write a crafted ADC json on demand."""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    adc_path = tmp_path / ".config" / "gcloud" / "application_default_credentials.json"
    adc_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(obj):
        adc_path.write_text(json.dumps(obj))
        return str(adc_path)

    _write.path = str(adc_path)
    return _write


# --- check_adc ----------------------------------------------------------------

def test_adc_authorized_user_ok(adc):
    path = adc({"type": "authorized_user", "client_id": "x"})
    r = doctor.check_adc(path)
    assert r["status"] == doctor.OK


def test_adc_service_account_fails(adc):
    path = adc({"type": "service_account", "client_email": "svc@proj.iam"})
    r = doctor.check_adc(path)
    assert r["status"] == doctor.FAIL
    assert "service account" in r["detail"]
    assert r["fix"]


def test_adc_impersonated_fails(adc):
    path = adc({"type": "impersonated_service_account"})
    r = doctor.check_adc(path)
    assert r["status"] == doctor.FAIL
    assert "impersonate" in r["fix"]


def test_adc_unknown_type_fails(adc):
    path = adc({"type": "external_account"})
    r = doctor.check_adc(path)
    assert r["status"] == doctor.FAIL


def test_adc_missing_fails(tmp_path):
    r = doctor.check_adc(str(tmp_path / "nope.json"))
    assert r["status"] == doctor.FAIL
    assert "application-default login" in r["fix"]


# --- check_sa_env -------------------------------------------------------------

def test_sa_env_unset_ok(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert doctor.check_sa_env()["status"] == doctor.OK


def test_sa_env_set_fails(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
    r = doctor.check_sa_env()
    assert r["status"] == doctor.FAIL
    assert "unset" in r["fix"]


# --- check_gcloud -------------------------------------------------------------

def test_gcloud_present_ok(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: "/usr/bin/gcloud")
    assert doctor.check_gcloud()["status"] == doctor.OK


def test_gcloud_absent_fails(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: None)
    r = doctor.check_gcloud()
    assert r["status"] == doctor.FAIL
    assert "cloud.google.com" in r["fix"]


# --- render_text --------------------------------------------------------------

def test_render_surfaces_fix_for_failures():
    results = [
        doctor._r("gcloud", doctor.OK, "found"),
        doctor._r("adc", doctor.FAIL, "missing", "gcloud auth application-default login"),
    ]
    out = doctor.render_text(results)
    assert "fix: gcloud auth application-default login" in out
    assert "need attention" in out


def test_render_all_ok_message():
    out = doctor.render_text([doctor._r("gcloud", doctor.OK, "found")])
    assert "All checks passed." in out


# --- no warehouse project is baked into the template -------------------------
# This repo is a public template, so shipping a default GCP project would ship
# somebody else's billing account. The project must come from the environment or
# the CLI, and the probe must say so rather than guessing.

def test_no_default_project_is_hardcoded():
    """DEFAULT_PROJECT is exactly the env var — never a literal fallback."""
    assert doctor.DEFAULT_PROJECT == os.environ.get("KATTFOT_BIGQUERY_PROJECT")


def test_probe_without_project_is_a_fixable_check():
    """No project configured is reported as a fixable check, not a crash, and not
    a query against a guessed project."""
    r = doctor.probe_bigquery(project=None)
    assert r["status"] == doctor.FAIL
    assert r["check"] == "probe"
    assert "KATTFOT_BIGQUERY_PROJECT" in r["fix"]
