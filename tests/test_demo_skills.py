"""The offline gate itself: does `scripts/demo_skills.py` still cover all fifteen?

`demo_skills.py` is the mechanical form of this template's central claim — every
functional skill runs with no network and no cloud credentials. That claim degrades
in a way nobody notices: a sixteenth skill lands and the gate silently checks
fifteen; a step's assertion gets softened; the environment scrubbing loses a
variable and the "no credentials" line stops being true.

So the gate gets its own tests. Running all fifteen subprocesses here would roughly
double the suite's runtime, so this file checks the gate's *structure* — that it
knows about every skill on disk, that its environment really is scrubbed, and that a
failing step is reported as a failure — and runs a representative slice end to end.
The full sweep is the command in the README, not a unit test.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import demo_skills

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "plugins" / "kattfot" / "skills"


# --- coverage of the skill surface ------------------------------------------


def test_the_gate_covers_every_skill_that_exists_on_disk():
    """The test that catches a new skill quietly escaping the gate.

    Derived from the directory listing rather than a second hardcoded list, so
    adding a skill and forgetting to wire it up fails here. A skill on disk must
    land in exactly one of `SKILL_ORDER` (the gate runs it for real) or
    `OUTLINE_ONLY_SKILLS` (it ships as a documented outline with no runnable
    helper) — never both, and never neither. That "never both" arm is what stops
    a future skill from being quietly forgotten by adding it to the exclusion set
    without ever actually wiring it into the gate.
    """
    on_disk = {
        p.parent.name
        for p in SKILLS_DIR.glob("*/SKILL.md")
    }
    assert on_disk, "found no skills on disk"
    accounted = set(demo_skills.SKILL_ORDER) | demo_skills.OUTLINE_ONLY_SKILLS
    missing = sorted(on_disk - accounted)
    extra = sorted(accounted - on_disk)
    overlap = sorted(set(demo_skills.SKILL_ORDER) & demo_skills.OUTLINE_ONLY_SKILLS)
    assert not missing, (
        f"skills on disk that are neither run by the gate nor declared "
        f"outline-only: {missing}"
    )
    assert not extra, f"the gate or OUTLINE_ONLY_SKILLS names skills that do not exist: {extra}"
    assert not overlap, (
        f"skill(s) claimed as both runnable and outline-only: {overlap}"
    )


def test_there_are_exactly_sixteen_functional_skills():
    """The number this repo commits to. If it changes, the README, the plugin
    description, and this gate all need to change together — so make that a decision
    rather than a drift. (It last changed deliberately: the 2026-08 research pass
    promoted funnel-nowcast from outline to functional, 15 -> 16, once a
    run-time-estimated chain ladder dissolved the fit-the-generator objection.)

    Sixteen is the count of *functional* skills specifically — the ones with a real
    helper the gate runs end to end. It is not the count of skill directories on
    disk: three more ship as honest outlines (see `OUTLINE_ONLY_SKILLS`), so
    `plugins/kattfot/skills/*/SKILL.md` currently totals nineteen. Both numbers are
    correct at once; they are counting different things.
    """
    assert len(demo_skills.SKILL_ORDER) == 16
    assert len(demo_skills.STEPS) == 16
    assert set(demo_skills.STEPS) == set(demo_skills.SKILL_ORDER)
    assert len(demo_skills.OUTLINE_ONLY_SKILLS) == 3


def test_every_skill_has_a_skill_md_and_the_gate_runs_a_real_helper():
    """A step that ran nothing would pass the gate and prove nothing."""
    for skill in demo_skills.SKILL_ORDER:
        assert (SKILLS_DIR / skill / "SKILL.md").exists(), f"{skill} has no SKILL.md"
        scripts = list((SKILLS_DIR / skill / "scripts").glob("*.py"))
        assert scripts, f"{skill} has no helper script for the gate to run"


def test_the_compose_chain_is_ordered_before_its_consumers():
    """`verify` needs an artifact and the renderers need a chart spec, so ordering is
    a correctness property of the gate, not a presentation choice."""
    order = list(demo_skills.SKILL_ORDER)
    for producer, consumer in (
        ("query-metric", "visualize-metric"),
        ("query-metric", "verify"),
        ("query-metric", "export-report"),
        ("visualize-metric", "render-chart"),
    ):
        assert order.index(producer) < order.index(consumer)


# --- the environment really is scrubbed -------------------------------------


def test_the_runner_scrubs_every_credential_path(tmp_path):
    """The "no credentials" claim, checked rather than stated.

    `HOME` is the load-bearing one: the identity guard looks for ADC at
    `~/.config/gcloud/application_default_credentials.json`, so pointing HOME at an
    empty directory is what makes a warehouse query impossible rather than merely
    unattempted.
    """
    home = tmp_path / "home"
    home.mkdir()
    runner = demo_skills.Runner(tmp_path / "out", home)
    env = runner.env
    assert env["HOME"] == str(home)
    assert not (home / ".config" / "gcloud").exists()
    assert env["KATTFOT_ENGINE"] == "sqlite"
    for var in (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "KATTFOT_BIGQUERY_PROJECT",
    ):
        assert var not in env, f"{var} survived the scrub"


def test_the_runner_scrubs_config_overrides_so_the_gate_tests_the_shipped_config(tmp_path):
    """A stray `KATTFOT_FUNNEL_SPEC` or `KATTFOT_METRICS_DIR` in the operator's shell would
    make the gate certify somebody's local overrides instead of the committed
    template."""
    home = tmp_path / "home"
    home.mkdir()
    env = demo_skills.Runner(tmp_path / "out", home).env
    for var in ("KATTFOT_FUNNEL_SPEC", "KATTFOT_METRICS_DIR", "KATTFOT_COVERAGE_FILE",
                "KATTFOT_GUARDRAILS_FILE"):
        assert var not in env


def test_a_failing_step_is_reported_as_a_failure(tmp_path, monkeypatch):
    """The gate has to be able to fail. A summary that reports `ok` for a step whose
    assertion blew up would be worse than no gate."""
    def boom(runner, state):
        raise AssertionError("deliberate")

    monkeypatch.setitem(demo_skills.STEPS, "catalog", boom)
    monkeypatch.setattr(sys, "argv", ["demo_skills", "--only", "catalog", "--json"])
    monkeypatch.setenv("KATTFOT_OUTPUTS_DIR", str(tmp_path))
    code = demo_skills.main()
    assert code == 1


def test_an_unknown_skill_name_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["demo_skills", "--only", "not-a-skill"])
    with pytest.raises(SystemExit) as exc:
        demo_skills.main()
    assert "unknown skill" in str(exc.value)


# --- a representative slice, end to end -------------------------------------


@pytest.mark.parametrize("slice_", [
    "catalog,coverage,load-context",
    "funnel-dropoff,funnel-pulse",
])
def test_a_slice_of_the_gate_runs_green_in_a_scrubbed_environment(slice_, tmp_path):
    """Runs the real subprocesses, in the real scrubbed environment, out of process —
    which is the only way to check that a skill does not depend on something the test
    session happens to have imported or set."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo_skills.py"),
         "--only", slice_, "--json"],
        capture_output=True, text=True, cwd=ROOT,
        env={**{k: v for k, v in __import__("os").environ.items()},
             "KATTFOT_OUTPUTS_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["failed"] == 0
    assert summary["ran"] == len(slice_.split(","))
    assert summary["engine"] == "sqlite"
    assert "no ADC" in summary["credentials"]
    for r in summary["results"]:
        assert r["status"] == "ok", r


def test_the_spine_artifacts_record_their_maturity_cutoff(tmp_path):
    """The Phase-2 contract: any skill writing an artifact records its cutoff, because
    `kattfot:verify` reads it and an artifact without one reports drift every time.

    Checked on the composed file rather than on the JSON, since the frontmatter is
    where the contract actually lives.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo_skills.py"),
         "--only", "funnel-dropoff,stage-timing,segment-compare,funnel-pulse", "--json"],
        capture_output=True, text=True, cwd=ROOT,
        env={**{k: v for k, v in __import__("os").environ.items()},
             "KATTFOT_OUTPUTS_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifacts = sorted(tmp_path.glob("*.md"))
    assert len(artifacts) == 4, [p.name for p in artifacts]
    for path in artifacts:
        text = path.read_text()
        assert "maturity_cutoff: 2026-" in text, f"{path.name} records no maturity cutoff"
        assert "fixture_backed: true" in text, f"{path.name} is not watermarked"
        assert "FIXTURE DATA" in text
