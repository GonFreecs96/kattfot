"""Promotion: exploration -> reviewed draft, with the R4 gate as the review gate.

The claims under test: a SUM/SUM single-surface exploration pre-fills a real
draft; any other shape scaffolds honestly with placeholders; a tampered
artifact is refused, not repaired; and the draft is inert — refused by
query-metric — until a human edits the tier, at which point the same
definition runs. That last pair is the design: promotion adds a paper trail,
never a bypass.
"""

import json
import sys

import pytest

import demo_skills
import kattfot_explore
import kattfot_query as aq
import promote_metric

WEEKLY = "analytics.cart_checkout_funnel_weekly"
SPINE = "analytics.cart_checkout_session_spine"

RATIO_SQL = (
    f"SELECT cohort_week, channel, "
    f"SAFE_DIVIDE(SUM(orders_returned), SUM(orders_confirmed)) AS confirmed_to_returned_rate, "
    f"SUM(orders_returned) AS numerator, SUM(orders_confirmed) AS denominator "
    f"FROM {WEEKLY} WHERE market = @m GROUP BY cohort_week, channel ORDER BY cohort_week"
)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setenv("KATTFOT_AUDIT_DISABLE", "1")


def explore(argv, capsys, monkeypatch) -> dict:
    monkeypatch.setattr(sys, "argv", ["kattfot_explore"] + argv)
    assert kattfot_explore.main() in (0, None)
    return json.loads(capsys.readouterr().out)


def make_artifact(tmp_path, capsys, monkeypatch, sql=RATIO_SQL, params=("m=US",)):
    out = explore(
        ["--question", "Of confirmed orders, what share is returned, by channel?",
         "--sql", sql]
        + [x for p in params for x in ("--param", p)],
        capsys, monkeypatch,
    )
    art = tmp_path / "explore-artifact.md"
    demo_skills.compose_explore_artifact(out, art)
    return art


def promote(argv, capsys, monkeypatch) -> dict:
    monkeypatch.setattr(sys, "argv", ["promote_metric"] + argv)
    assert promote_metric.main() in (0, None)
    return json.loads(capsys.readouterr().out)


def promote_refused(argv, capsys, monkeypatch, code=5) -> str:
    monkeypatch.setattr(sys, "argv", ["promote_metric"] + argv)
    with pytest.raises(SystemExit) as exc:
        promote_metric.main()
    if code is not None:
        assert exc.value.code == code
    # exit-5 refusals write stderr before exiting; a SystemExit("message")
    # carries its text in the exception itself. Return both.
    return capsys.readouterr().err + str(exc.value)


# --- the happy path: a promotable shape --------------------------------------


def test_a_ratio_exploration_prefills_the_draft(tmp_path, capsys, monkeypatch):
    art = make_artifact(tmp_path, capsys, monkeypatch)
    out = promote(
        ["--artifact", str(art), "--name", "confirmed_to_returned_rate",
         "--out-dir", str(tmp_path / "drafts")],
        capsys, monkeypatch,
    )
    assert out["prefilled"] is True
    draft = (tmp_path / "drafts" / "confirmed_to_returned_rate.md").read_text()
    assert "accuracy_tier: T3" in draft
    assert "status: draft" in draft
    assert f"source_view: {WEEKLY}" in draft
    assert "numerator_column: orders_returned" in draft
    assert "denominator_column: orders_confirmed" in draft
    assert "time_column: cohort_week" in draft
    assert "- channel" in draft           # the GROUP BY dimension
    assert "- market" in draft            # the bound-filter column
    assert "module: checkout" in draft    # resolved from the register entry
    assert "Promotion checklist" in draft
    assert "promoted_from:" in draft
    # what stays a human decision, stated as such
    assert set(out["needs_decision"]) >= {"owner", "entity", "maturity_stage", "accuracy_tier"}


def test_the_draft_is_refused_by_query_metric_until_the_tier_is_edited(
    tmp_path, capsys, monkeypatch
):
    """The whole promotion design in one test: T3 draft -> R4 refusal (with the
    EXPLORABLE referral); tier edited to T2 by 'review' -> the same definition
    runs. Promotion never serves; the review gate opens it."""
    art = make_artifact(tmp_path, capsys, monkeypatch)
    drafts = tmp_path / "drafts"
    promote(
        ["--artifact", str(art), "--name", "confirmed_to_returned_rate",
         "--out-dir", str(drafts)],
        capsys, monkeypatch,
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(drafts))
    monkeypatch.setattr(
        sys, "argv",
        ["kattfot_query", "--metric", "confirmed_to_returned_rate",
         "--today", "2026-08-17"],
    )
    with pytest.raises(SystemExit) as exc:
        aq.main()
    msg = str(exc.value)
    assert "ERROR (R4)" in msg
    assert "EXPLORABLE --" in msg

    # The human review: tier -> T2. The same draft now runs.
    path = drafts / "confirmed_to_returned_rate.md"
    path.write_text(path.read_text().replace("accuracy_tier: T3", "accuracy_tier: T2"))
    capsys.readouterr()
    assert aq.main() in (0, None)
    served = json.loads(capsys.readouterr().out)
    assert served["accuracy_tier"] == "T2"
    assert served["row_count"] > 0


# --- honest scaffolds for unpromotable shapes --------------------------------


def test_a_non_ratio_shape_scaffolds_with_placeholders(tmp_path, capsys, monkeypatch):
    art = make_artifact(
        tmp_path, capsys, monkeypatch,
        sql=f"SELECT market, COUNT(*) AS n FROM {SPINE} GROUP BY market",
        params=(),
    )
    out = promote(
        ["--artifact", str(art), "--name", "sessions_by_market",
         "--out-dir", str(tmp_path / "drafts")],
        capsys, monkeypatch,
    )
    assert out["prefilled"] is False
    assert "not a SAFE_DIVIDE" in out["shape_note"]
    draft = (tmp_path / "drafts" / "sessions_by_market.md").read_text()
    assert "NEEDS-DECISION" in draft
    assert "## Shape note" in draft


def test_a_multi_surface_shape_points_at_a_curated_view(tmp_path, capsys, monkeypatch):
    sql = (
        f"SELECT w.cohort_week, "
        f"SAFE_DIVIDE(SUM(w.orders_returned), SUM(w.orders_confirmed)) AS r, "
        f"SUM(w.orders_returned) AS numerator, SUM(w.orders_confirmed) AS denominator "
        f"FROM {WEEKLY} w JOIN {SPINE} s ON w.cohort_week = s.cohort_week "
        f"GROUP BY w.cohort_week"
    )
    art = make_artifact(tmp_path, capsys, monkeypatch, sql=sql, params=())
    out = promote(
        ["--artifact", str(art), "--name", "joined_rate",
         "--out-dir", str(tmp_path / "drafts")],
        capsys, monkeypatch,
    )
    assert out["prefilled"] is False
    assert "2 surfaces" in out["shape_note"]
    assert "curated view" in out["shape_note"]


# --- refusals ----------------------------------------------------------------


def test_a_tampered_artifact_is_refused(tmp_path, capsys, monkeypatch):
    art = make_artifact(tmp_path, capsys, monkeypatch)
    art.write_text(art.read_text().replace("orders_returned", "orders_shipped"))
    err = promote_refused(
        ["--artifact", str(art), "--name", "tampered",
         "--out-dir", str(tmp_path / "drafts")],
        capsys, monkeypatch,
    )
    assert "ERROR (PROMOTE-TAMPER)" in err
    assert "query_sha" in err
    assert not (tmp_path / "drafts" / "tampered.md").exists()


def test_a_non_explore_artifact_is_refused(tmp_path, capsys, monkeypatch):
    art = tmp_path / "governed.md"
    art.write_text(
        "---\nskill: query-metric\naccuracy_tier: T2\nmanifest:\n  query_sha: abc\n---\n"
        "## Provenance\n\n```sql\nSELECT 1\n```\n"
    )
    err = promote_refused(
        ["--artifact", str(art), "--name", "nope", "--out-dir", str(tmp_path)],
        capsys, monkeypatch,
    )
    assert "ERROR (PROMOTE-INPUT)" in err
    assert "already governed" in err


def test_an_existing_draft_is_not_overwritten_without_force(tmp_path, capsys, monkeypatch):
    art = make_artifact(tmp_path, capsys, monkeypatch)
    drafts = tmp_path / "drafts"
    args = ["--artifact", str(art), "--name", "confirmed_to_returned_rate",
            "--out-dir", str(drafts)]
    promote(args, capsys, monkeypatch)
    err = promote_refused(args, capsys, monkeypatch, code=None)
    assert "already exists" in err
    promote(args + ["--force"], capsys, monkeypatch)  # explicit overwrite works


def test_a_bad_metric_name_is_a_clean_error(tmp_path, capsys, monkeypatch):
    art = make_artifact(tmp_path, capsys, monkeypatch)
    err = promote_refused(
        ["--artifact", str(art), "--name", "Not-Snake", "--out-dir", str(tmp_path)],
        capsys, monkeypatch, code=None,
    )
    assert "snake_case" in err


def test_register_drift_warns_but_does_not_refuse(tmp_path, capsys, monkeypatch):
    art = make_artifact(tmp_path, capsys, monkeypatch)
    text = art.read_text()
    # simulate the register having changed since the exploration ran
    import re as _re
    # non-numeric on purpose: an all-digit fake would YAML-parse as an int
    art.write_text(_re.sub(r"register_sha: \w+", "register_sha: deadbeefdeadbeef", text))
    out = promote(
        ["--artifact", str(art), "--name", "confirmed_to_returned_rate",
         "--out-dir", str(tmp_path / "drafts")],
        capsys, monkeypatch,
    )
    assert any("REGISTER DRIFT" in w for w in out["warnings"])
