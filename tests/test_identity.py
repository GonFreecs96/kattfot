"""Governance: per-user identity enforcement (service-account refusal).

Every BigQuery-touching skill runs as the individual human user via ADC.
`assert_user_identity()` refuses anything else. These tests lock the exit-code
contract documented in `_shared/identity.py`:

  exit 2 = ADC missing
  exit 4 = service account / impersonation / non-user credential  (REFUSED)

No network or real gcloud is involved: we point HOME at a temp dir and craft
the ADC json by hand.
"""

import json

import pytest

import _shared.identity as identity


@pytest.fixture
def adc(tmp_path, monkeypatch):
    """Redirect identity at a temp ADC file; clear GOOGLE_APPLICATION_CREDENTIALS.

    Returns a writer that drops a crafted ADC json at the expected path.
    """
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    adc_path = tmp_path / ".config" / "gcloud" / "application_default_credentials.json"
    adc_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(obj):
        adc_path.write_text(json.dumps(obj))
        return adc_path

    _write.path = adc_path
    return _write


def _exit_code(func):
    with pytest.raises(SystemExit) as exc:
        func()
    return exc.value.code


def test_gac_env_var_refused(adc, monkeypatch):
    # GOOGLE_APPLICATION_CREDENTIALS would force a service-account identity.
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/to/sa-key.json")
    assert _exit_code(identity.assert_user_identity) == 4


def test_missing_adc_exits_2(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ADC file created
    assert _exit_code(identity.assert_user_identity) == 2


def test_service_account_refused(adc):
    adc({"type": "service_account", "client_email": "svc@proj.iam.gserviceaccount.com"})
    assert _exit_code(identity.assert_user_identity) == 4


def test_impersonated_service_account_refused(adc):
    adc({"type": "impersonated_service_account"})
    assert _exit_code(identity.assert_user_identity) == 4


def test_unknown_credential_type_refused(adc):
    adc({"type": "external_account"})
    assert _exit_code(identity.assert_user_identity) == 4


def test_malformed_adc_exits_2(adc):
    adc.path.write_text("{ not valid json")
    assert _exit_code(identity.assert_user_identity) == 2


def test_authorized_user_accepted(adc):
    payload = {"type": "authorized_user", "client_id": "c", "refresh_token": "r"}
    adc(payload)
    result = identity.assert_user_identity()
    assert result["type"] == "authorized_user"
