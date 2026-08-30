#!/usr/bin/env python3
"""demo_skills.py — run all sixteen functional skills offline, and fail if any of them cannot.

`scripts/demo.py` walks the compose chain (a governed metric becomes an artifact,
becomes a chart, becomes a PDF) and is the best way to *see* what the plugin
produces. This is the companion that makes the broader claim mechanical: **every
functional skill in this template runs with no network and no cloud credentials.**

The claim is worth checking rather than asserting, because it is the one a reader
cannot verify by inspection and the one most likely to quietly stop being true — a
skill gains a dependency, or starts needing a project id, and nothing fails until
somebody clones the repo.

How "offline" is enforced, concretely
------------------------------------
Every step runs as a subprocess whose environment has been scrubbed:

  * `HOME` points at an empty temporary directory, so
    `~/.config/gcloud/application_default_credentials.json` does not exist and the
    identity guard would refuse a warehouse query outright;
  * `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, and
    `KATTFOT_BIGQUERY_PROJECT` are removed;
  * `KATTFOT_ENGINE` is pinned to `sqlite`, so every query runs the skill's real
    canonical SQL against the committed fixture CSVs.

Nothing here is mocked. Each skill's real entry point runs, and the two skills that
write numbers into an artifact record their maturity cutoff in the frontmatter — the
contract `kattfot:verify` reads.

Usage
-----
    python3 scripts/demo_skills.py                 # the gate: all sixteen
    python3 scripts/demo_skills.py --only catalog,funnel-pulse
    python3 scripts/demo_skills.py --allow-missing-optional
    python3 scripts/demo_skills.py --json

`visualize-metric` needs matplotlib and `export-report` needs fpdf2 — offline
dependencies, not network ones, so by default a missing one is a failure with the
install command. `--allow-missing-optional` downgrades that to a reported gap, and
the summary then says how many of the sixteen actually ran.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS = REPO_ROOT / "plugins" / "kattfot" / "skills"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

#: The fixtures' observation instant, recorded in data/kattfot/sample/FIXTURES.md.
#: Reading a fixed fixture at the real today withholds more of the tail every week
#: until the whole book looks immature, so every step pins it.
AS_OF = "2026-08-17"

#: A window that sits inside the committed (trimmed) spine.
SPINE_WINDOW = ["--time-range", "last_16_weeks", "--today", AS_OF]

DEMO_METRIC = "cart_to_checkout_start_conversion"

#: The eighteen functional skills, in dependency order — the compose chain first,
#: because `verify` needs an artifact to check, and `explore` before `promote`,
#: because promotion consumes the exploratory artifact.
SKILL_ORDER = (
    "setup",
    "catalog",
    "coverage",
    "load-context",
    "query-metric",
    "visualize-metric",
    "render-chart",
    "render-dashboard",
    "export-report",
    "verify",
    "reconcile",
    "funnel-dropoff",
    "stage-timing",
    "segment-compare",
    "funnel-pulse",
    "funnel-nowcast",
    "explore",
    "promote",
)

#: Skills that ship as a documented outline only — a `SKILL.md` exists on disk, but
#: there is deliberately no runnable helper behind it (see each skill's own
#: `SKILL.md` for why). The offline gate cannot run these, and must not silently
#: forget they exist either — `tests/test_demo_skills.py` asserts every on-disk
#: skill is in exactly one of `SKILL_ORDER` or this set, never both and never
#: neither.
OUTLINE_ONLY_SKILLS = {
    "release-scorecard",
    "customer-journey",
    "publish",
}

OPTIONAL_DEPS = {
    "visualize-metric": ("matplotlib", "plugins/kattfot/skills/visualize-metric/scripts/requirements.txt"),
    "export-report": ("fpdf", "plugins/kattfot/skills/export-report/scripts/requirements.txt"),
}


class Runner:
    """Subprocess runner over a scrubbed, offline environment."""

    def __init__(self, outputs: Path, home: Path):
        self.outputs = outputs
        self.env = dict(os.environ)
        for var in (
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "KATTFOT_BIGQUERY_PROJECT",
            "KATTFOT_FUNNEL_SPEC",
            "KATTFOT_METRICS_DIR",
            "KATTFOT_COVERAGE_FILE",
            "KATTFOT_GUARDRAILS_FILE",
        ):
            self.env.pop(var, None)
        self.env.update({
            "KATTFOT_ENGINE": "sqlite",
            "KATTFOT_OUTPUTS_DIR": str(outputs),
            "HOME": str(home),
            "MPLCONFIGDIR": str(home / "mpl"),
        })
        (home / "mpl").mkdir(parents=True, exist_ok=True)

    def run(self, args, *, allow_exit=(0,), extra_env=None) -> subprocess.CompletedProcess:
        env = {**self.env, **(extra_env or {})} if extra_env else self.env
        proc = subprocess.run(
            [sys.executable] + [str(a) for a in args],
            capture_output=True, text=True, cwd=REPO_ROOT, env=env,
        )
        if proc.returncode not in allow_exit:
            raise StepFailed(
                f"exit {proc.returncode} from {' '.join(str(a) for a in args[:2])}\n"
                f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
            )
        return proc

    def json(self, args, *, allow_exit=(0,)) -> dict:
        return json.loads(self.run(args, allow_exit=allow_exit).stdout)


class StepFailed(Exception):
    pass


def helper(skill: str, script: str) -> Path:
    return SKILLS / skill / "scripts" / script


# ---------------------------------------------------------------------------
# Spine-artifact composition
# ---------------------------------------------------------------------------
# The spine skills print JSON; in normal use the AGENT composes the markdown
# artifact, which is a judgment step. This writes a deliberately plain version so
# the artifact contract is mechanically checked rather than described — in
# particular `maturity_cutoff` in the frontmatter, which `kattfot:verify` reads and
# whose absence makes verify report drift on every artifact.


def compose_spine_artifact(result: dict, path: Path, body_lines: list) -> Path:
    fm = [
        "---",
        f"skill: {result['skill']}",
        f"accuracy_tier: {result['accuracy_tier']}",
        f"source_view: {result['source_project']}.{result['source_view']}",
        f"engine: {result['engine']}",
        f"fixture_backed: {str(result['fixture_backed']).lower()}",
        f"generated_at: {result['generated_at']}",
        f"maturity_mode: {result['maturity_mode']}",
        f"maturity_cutoff: {result['maturity_cutoff']}",
        "---",
        "",
        f"# {result['skill']} — fixture-backed demonstration",
        "",
        "> ⚠️ **FIXTURE DATA** — these numbers come from committed synthetic fixtures,",
        "> not from a warehouse. They describe a seeded random process.",
        "",
        f"**Engine:** {result['engine']} · **Maturity:** {result['maturity_mode']}, "
        f"settled through {result['maturity_cutoff']}",
        "",
    ]
    warnings = ["## Run warnings", ""] + [f"- {w}" for w in result.get("warnings", [])]
    note = [
        "",
        "## About this artifact",
        "",
        "Composed by `scripts/demo_skills.py`, which scripts a deliberately plain",
        "version of the narrative step so the artifact contract is checkable. In normal",
        "use the agent writes that section against the question actually asked — a",
        "script cannot know what was asked.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(fm + body_lines + [""] + warnings + note))
    return path


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.2f}%"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
# Each returns (detail, outputs). Raising StepFailed fails that skill only, so the
# summary reports every skill's state rather than stopping at the first problem.


def step_setup(r: Runner, state: dict):
    # Exit 1 is expected and correct here: the environment is deliberately scrubbed
    # of credentials, so the doctor's ADC and dependency checks SHOULD fail. What is
    # being demonstrated is that the skill runs and produces a diagnosis offline —
    # not that this machine is ready to query a warehouse.
    out = r.json([helper("setup", "doctor.py"), "--format", "json"], allow_exit=(0, 1))
    checks = {c["check"]: c["status"] for c in out["results"]}
    assert checks, "the doctor produced no checks"
    failed = [k for k, v in checks.items() if v != "ok"]
    return f"{len(checks)} checks reported; not-ok in the scrubbed env: {failed or 'none'}", {}


def step_catalog(r: Runner, state: dict):
    out = r.json([helper("catalog", "catalog.py")])
    assert out["metric_count"] >= 15, out["metric_count"]
    md = state["outputs"] / f"{AS_OF}-catalog.md"
    md.write_text(r.run([helper("catalog", "catalog.py"), "--format", "markdown"]).stdout)
    return (
        f"{out['metric_count']} ratio metrics, {out['kpi_count']} latency KPIs, "
        f"{len(out['spines'])} spines",
        {"catalog": md},
    )


def step_coverage(r: Runner, state: dict):
    listing = r.json([helper("coverage", "coverage_lookup.py")])
    hit = r.json(
        [helper("coverage", "coverage_lookup.py"), "--term", "why was the cart abandoned"]
    )
    assert hit["matches"], "the register did not match a phrase it registers"
    miss = r.run(
        [helper("coverage", "coverage_lookup.py"), "--term", "unregistered nonsense"],
        allow_exit=(1,),
    )
    assert json.loads(miss.stdout)["matches"] == []
    return (
        f"{listing['register_size']} entries across "
        f"{len(listing['by_status'])} statuses; matched "
        f"{hit['matches'][0]['id']} ({hit['matches'][0]['status']}), and a no-match "
        f"exited 1 rather than guessing",
        {},
    )


def step_load_context(r: Runner, state: dict):
    script = helper("load-context", "load_context.py")
    scopes = ["catalog", "entities", "spines", "coverage", "tiers", "governance",
              "lineage/checkout", f"metric/{DEMO_METRIC}", "entity/browse_session",
              "coverage/cart_abandon_reason"]
    for scope in scopes:
        assert r.run([script, scope]).stdout.strip(), f"{scope} returned nothing"
    # The sentinels, which are the part a consumer depends on.
    sentinels = {}
    for scope, expected, code in (
        ("nonsense", "SCOPE_NOT_FOUND", 1),
        ("glossary", "EMPTY_RESULT", 1),
        ("lineage/../../../../etc", "PATH_BOUNDARY", 5),
    ):
        proc = r.run([script, scope], allow_exit=(code,))
        assert proc.stdout.startswith(expected), proc.stdout[:200]
        sentinels[expected] = code
    return (
        f"{len(scopes)} scopes resolved; sentinels reached with their exit codes: "
        f"{sentinels}",
        {},
    )


def step_query_metric(r: Runner, state: dict):
    """The governed metric read, then the artifact the rest of the chain consumes."""
    import demo  # the compose-chain helpers, reused rather than reimplemented

    out = r.json([
        helper("query-metric", "kattfot_query.py"),
        "--metric", DEMO_METRIC, "--dimensions", "channel",
        "--time-range", "last_16_weeks", "--today", AS_OF,
    ])
    assert out["fixture_backed"] is True
    assert out["maturity"], "no maturity block on a metric that declares a stage"
    slug = f"{AS_OF}-{DEMO_METRIC.replace('_', '-')}"
    artifact = state["outputs"] / f"{slug}.md"
    demo.compose_artifact(out, artifact)
    assert "maturity_cutoff:" in artifact.read_text(), "the artifact must record its cutoff"
    state["artifact"] = artifact
    state["slug"] = slug
    return (
        f"{out['row_count']} rows · {out['source_mode']} · volume "
        f"{out['total_numerator_display']}/{out['total_denominator_display']} · "
        f"cohorts withheld after {out['maturity']['cutoff_week']}",
        {"artifact": artifact},
    )


def step_visualize_metric(r: Runner, state: dict):
    charts = state["outputs"] / "charts"
    spec = charts / f"{state['slug']}.spec.json"
    r.run([
        helper("visualize-metric", "chart_spec.py"),
        "--artifact", state["artifact"], "--flag-last-n", "1", "--out", spec,
    ])
    rendered = r.json([
        helper("visualize-metric", "render.py"),
        "--spec", spec, "--out-base", charts / state["slug"],
    ])
    state["spec"] = spec
    payload = json.loads(spec.read_text())
    assert payload["mock"] is True, "a fixture-backed chart must be watermarked"
    return f"{payload['chart_type']} → SVG + PNG (mock watermark on)", {
        "spec": spec, "svg": rendered["svg"], "png": rendered["png"],
    }


def step_render_chart(r: Runner, state: dict):
    html = state["outputs"] / "charts" / f"{state['slug']}.html"
    r.run([helper("render-chart", "render_html.py"), "--spec", state["spec"], "--out", html])
    assert html.stat().st_size > 10_000, "the self-contained HTML looks truncated"
    return f"self-contained interactive HTML ({html.stat().st_size // 1024} KB)", {"html": html}


def step_render_dashboard(r: Runner, state: dict):
    example = SKILLS / "render-dashboard" / "examples" / "example.dashboard.json"
    html = state["outputs"] / f"{AS_OF}-dashboard.html"
    r.run([helper("render-dashboard", "render_dashboard.py"), "--spec", example, "--out", html])
    assert html.stat().st_size > 10_000
    return f"dashboard spec → one self-contained HTML ({html.stat().st_size // 1024} KB)", {
        "html": html
    }


def step_export_report(r: Runner, state: dict):
    pdf = state["outputs"] / f"{state['slug']}.pdf"
    out = r.json([
        helper("export-report", "report_pdf.py"),
        "--artifact", state["artifact"], "--out", pdf, "--published-by", "kattfot demo",
    ])
    path = Path(out.get("pdf_path", pdf))
    assert path.exists() and path.stat().st_size > 1000
    return f"portable PDF ({path.stat().st_size // 1024} KB)", {"pdf": path}


def step_verify(r: Runner, state: dict):
    out = r.run([helper("verify", "verify_artifact.py"), state["artifact"]])
    status = out.stdout.strip().splitlines()[0]
    assert "VERIFY_OK" in status, out.stdout
    return f"{status} — definition and query fingerprints still match", {}


def step_reconcile(r: Runner, state: dict):
    """Reconcile against a number produced by dropping the maturity cutoff.

    The most instructive cause in the catalogue, and the one that was structurally
    unreachable until `Request` grew a `mature` flag.
    """
    deep = "delivered_to_return_window_complete_rate"
    theirs = "0.369"
    out = r.json([
        helper("reconcile", "reconcile_number.py"),
        "--metric", deep, "--their-value", theirs,
        "--time-range", "last_8_weeks", "--today", AS_OF,
    ])
    assert out["cause"] == "MATURITY_CUTOFF", out["cause"]
    return (
        f"cause {out['cause']} in {out['run_count']} re-runs: governed "
        f"{out['ours']:.1%} vs theirs {float(theirs):.1%}, reproduced "
        f"{out['reproduced']:.1%}",
        {},
    )


def step_funnel_dropoff(r: Runner, state: dict):
    out = r.json([helper("funnel-dropoff", "funnel_dropoff.py")] + SPINE_WINDOW)
    g = out["groups"][0]
    assert out["branch_coherent"] is True, g["branch_coherence"]
    rows = ["## Stage funnel", "",
            "| Stage | Count | Stage conv. | Cumulative | Mature through |",
            "| --- | --- | --- | --- | --- |"]
    for s in g["stages"]:
        rows.append(
            f"| {s['label']} | {s['count']:,} | {_pct(s['stage_conversion'])} | "
            f"{_pct(s['cumulative'])} | {s['mature_through']} |"
        )
    rows += ["", "## Where it leaks (terminal branch)", "",
             f"Over the single settled cohort through {out['maturity_cutoff']}; branches "
             f"sum to {g['branch_coherence']['carts_in_deep_window']:,} carts created.", "",
             "| Terminal branch | Count | % of carts |", "| --- | --- | --- |"]
    for b in g["branches"]:
        rows.append(f"| {b['terminal_branch']} | {b['count']:,} | {_pct(b['pct_of_started'])} |")
    artifact = compose_spine_artifact(
        out, state["outputs"] / f"{AS_OF}-funnel-dropoff.md", rows
    )
    biggest = max(g["branches"], key=lambda b: b["count"])
    return (
        f"{len(g['stages'])} stages, {len(g['branches'])} branches, coherent "
        f"({g['branch_coherence']['branch_total']:,} = "
        f"{g['branch_coherence']['carts_in_deep_window']:,}); biggest leak "
        f"{biggest['terminal_branch']} at {_pct(biggest['pct_of_started'])}",
        {"artifact": artifact},
    )


def step_stage_timing(r: Runner, state: dict):
    out = r.json(
        [helper("stage-timing", "stage_timing.py")] + SPINE_WINDOW
        + ["--group-by", "market,category"]
    )
    assert len(out["groups"]) == 12, len(out["groups"])
    whole = r.json([helper("stage-timing", "stage_timing.py")] + SPINE_WINDOW)
    rows = ["## Latency by transition", "",
            "| Transition | n | p50 | p90 | p99 | mean | unit | Mature through |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for t in whole["groups"][0]["transitions"]:
        p = t["percentiles"]
        rows.append(
            f"| {t['label']} | {t['n']:,} | {p['p50']} | {p['p90']} | {p['p99']} | "
            f"{t['mean']} | {t['unit']} | {t['mature_through']} |"
        )
    rows += ["", f"**Percentile basis:** {whole['percentile_basis']}"]
    artifact = compose_spine_artifact(
        whole, state["outputs"] / f"{AS_OF}-stage-timing.md", rows
    )
    return (
        f"{len(whole['groups'][0]['transitions'])} transitions; multi-key grouping "
        f"gave {len(out['groups'])} (market x category) rows with each dimension "
        f"under its own key; basis: {whole['percentile_basis']}",
        {"artifact": artifact},
    )


def step_segment_compare(r: Runner, state: dict):
    out = r.json([helper("segment-compare", "segment_compare.py")] + SPINE_WINDOW + [
        "--filter", "channel=web",
        "--a-filter", "payment_method=card_instant",
        "--b-filter", "payment_method=bank_transfer_review",
        "--a-label", "Card (instant)", "--b-label", "Bank transfer (review)",
    ])
    cmp = out["comparison"]
    rows = ["## Funnel (cumulative from start)", "",
            f"A: {out['segment_a']['label']} (n={out['segment_a']['started']:,}) · "
            f"B: {out['segment_b']['label']} (n={out['segment_b']['started']:,})", "",
            "| Stage | A | B | Δ (pp) |", "| --- | --- | --- | --- |"]
    for s in cmp["stages"]:
        d = s["delta_pp"]
        rows.append(
            f"| {s['label']} | {_pct(s['a_cumulative'])} | {_pct(s['b_cumulative'])} | "
            f"{'n/a' if d is None else f'{d:+.2f}'} |"
        )
    p = cmp["headline_percentile"]
    rows += ["", f"## Timing (p{p})", "",
             "| Transition | A | B | Δ | unit |", "| --- | --- | --- | --- | --- |"]
    for t in cmp["timing"]:
        d = t["delta"]
        rows.append(
            f"| {t['label']} | {t.get(f'a_p{p}')} | {t.get(f'b_p{p}')} | "
            f"{'n/a' if d is None else f'{d:+.1f}'} | {t['unit']} |"
        )
    artifact = compose_spine_artifact(
        out, state["outputs"] / f"{AS_OF}-segment-compare.md", rows
    )
    decision = next(t for t in cmp["timing"] if t["transition"] == "payment_to_decision")
    e2e = cmp["stages"][-1]
    return (
        f"{out['mode']}: end-to-end {e2e['delta_pp']:+.2f}pp, payment decision p50 "
        f"{decision['delta']:+.0f} min",
        {"artifact": artifact},
    )


def step_funnel_pulse(r: Runner, state: dict):
    """Run it twice: by channel (no override) and by payment_method (override active).

    The second is the one worth demonstrating — it is the only path that executes the
    per-group maturity `CASE`.
    """
    script = helper("funnel-pulse", "funnel_pulse.py")
    by_channel = r.json([script, "--by", "channel", "--today", AS_OF])
    by_method = r.json([script, "--by", "payment_method", "--min-started", "100",
                        "--today", AS_OF])
    overrides = by_method["maturity"]["per_stage"]["payment_decisioned"]["group_overrides"]
    assert overrides, "the per-group maturity override did not engage"
    assert by_channel["maturity"]["per_stage"]["payment_decisioned"]["group_overrides"] is None

    rows = [f"## 🚩 Flags by {by_method['by']} (ranked by impact)", "",
            "| Group | What | Baseline → Recent | Δ (pp) | Direction | recent n | window |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for f in by_method["flags"][:10]:
        rows.append(
            f"| {f['grp']} | {f['name']} | {_pct(f['baseline_rate'])} → "
            f"{_pct(f['recent_rate'])} | {f['delta_pp']:+.2f} | {f['direction']} | "
            f"{f['recent_n']:,} | {'override' if f['lag_override'] else 'stage default'} |"
        )
    rows += ["", "## Per-stage windows", "",
             "| Stage | Recent | Baseline | Lag (d) | Group overrides |",
             "| --- | --- | --- | --- | --- |"]
    for k, w in by_method["maturity"]["per_stage"].items():
        ov = w["group_overrides"]
        ov_text = ", ".join(
            f"{v}={d['lag_days']}d→{d['mature_through']}"
            for v, d in (ov or {}).get(by_method["by"], {}).items()
        ) or "—"
        rows.append(
            f"| {k} | {w['recent']['start']}..{w['recent']['end']} | "
            f"{w['baseline']['start']}..{w['baseline']['end']} | {w['lag_days']} | {ov_text} |"
        )
    artifact = compose_spine_artifact(
        by_method, state["outputs"] / f"{AS_OF}-funnel-pulse.md", rows
    )
    ov_groups = sorted(overrides[by_method["by"]])
    return (
        f"scanned {by_method['scanned']}, qualified {by_method['qualified']}, "
        f"{len(by_method['flags'])} flags; per-group maturity override applied to "
        f"{', '.join(ov_groups)}",
        {"artifact": artifact},
    )


def step_funnel_nowcast(r: Runner, state: dict):
    """Run the chain-ladder now-cast WITH its backtest, and require both halves:
    projections for the immature cohorts, and an empirical-coverage read for the
    interval. The backtest is not optional decoration here — a now-cast band
    whose calibration was never measured is the exact overconfidence this skill
    exists to avoid, so the gate demonstrates the measurement."""
    out = r.json([helper("funnel-nowcast", "funnel_nowcast.py"),
                  "--today", AS_OF, "--time-range", "last_19_weeks", "--backtest"])
    assert out["lane"] == "FORECAST" and out["accuracy_tier"] == "FORECAST"
    g = out["groups"][0]
    projected = [c for c in g["cohorts"] if not c["mature"] and c.get("quantiles")]
    assert projected, "no immature cohort produced a projection"
    assert all(
        c["quantiles"]["p10"] <= c["quantiles"]["p50"] <= c["quantiles"]["p90"]
        for c in projected
    )
    bt = g["backtest"]
    assert bt["replays"] > 0 and bt["overall_empirical_coverage"] is not None

    rows = ["## Now-cast by cohort (FORECAST lane)", "",
            "| Cohort | Age (w) | Observed | Method | P50 ult. | P10–P90 | Rate P50 |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for c in g["cohorts"]:
        if c["mature"]:
            rows.append(
                f"| {c['cohort_week']} | {c['age_weeks']} | {c['observed']:,} | "
                f"mature | {c['observed']:,} | — | {_pct(c['observed_rate'])} |"
            )
        elif c.get("quantiles"):
            q = c["quantiles"]
            rows.append(
                f"| {c['cohort_week']} | {c['age_weeks']} | {c['observed']:,} | "
                f"{c['method']} | {q['p50']:,} | {q['p10']:,}–{q['p90']:,} | "
                f"{_pct(c['ultimate_rate'])} |"
            )
    rows += ["", "## Backtest — nominal vs empirical interval coverage", "",
             f"Nominal {bt['nominal_coverage']:.0%} · empirical "
             f"{bt['overall_empirical_coverage']:.0%} over {bt['replays']} replays",
             "", "| Age (w) | n | Coverage | Median APE |", "| --- | --- | --- | --- |"]
    for h in bt["per_horizon"]:
        rows.append(
            f"| {h['age_weeks']} | {h['n']} | "
            f"{h['empirical_coverage']:.0%} | "
            f"{h['median_abs_pct_error']:.1%} |"
        )
    artifact = compose_spine_artifact(
        out, state["outputs"] / f"{AS_OF}-funnel-nowcast.md", rows
    )
    youngest = projected[-1]
    return (
        f"{out['target_stage']}: {len(projected)} immature cohorts projected "
        f"(youngest {youngest['cohort_week']} P50 "
        f"{_pct(youngest['ultimate_rate'])}); backtest coverage "
        f"{bt['overall_empirical_coverage']:.0%} vs nominal "
        f"{bt['nominal_coverage']:.0%} over {bt['replays']} replays",
        {"artifact": artifact},
    )


def compose_explore_artifact(result: dict, path: Path) -> Path:
    """The explore artifact, in the shape `explore/SKILL.md` specifies — the
    Provenance fences are load-bearing, because `kattfot:promote` re-parses the
    SQL and parameters from exactly those blocks for its tamper check."""
    fm = [
        "---",
        "skill: explore",
        f"accuracy_tier: {result['accuracy_tier']}",
        f"question: {json.dumps(result['question'])}",
        f"surfaces: {json.dumps(result['surfaces'])}",
        f"source_mode: {result['source_mode']}",
        f"source_project: {result['source_project']}",
        f"engine: {result['engine']}",
        f"fixture_backed: {str(result['fixture_backed']).lower()}",
        f"generated_at: {result['generated_at']}",
        "manifest:",
        f"  plugin_version: {result['manifest']['plugin_version']}",
        f"  register_sha: {result['manifest']['register_sha']}",
        f"  query_sha: {result['manifest']['query_sha']}",
        f"  result_sha: {result['manifest']['result_sha']}",
        "---",
        "",
        f"# Exploration — {result['question']}",
        "",
        "> **EXPLORATORY (T3) — NOT DECISION-GRADE.** Ad-hoc result: no reviewed",
        "> definition, no named owner, no promise it means the same thing next week.",
        "",
        "> ⚠️ **FIXTURE DATA** — these numbers come from committed synthetic",
        "> fixtures, not from a warehouse. They describe a seeded random process.",
        "",
        f"**Surfaces:** {', '.join(result['surfaces'])}   **Rows:** {result['row_count']}",
        "",
        "## Results",
        "",
    ]
    cols = list(result["rows"][0].keys()) if result["rows"] else []
    if cols:
        fm.append("| " + " | ".join(cols) + " |")
        fm.append("| " + " | ".join("---" for _ in cols) + " |")
        for row in result["rows"]:
            fm.append(
                "| " + " | ".join(
                    "—" if row[c] is None else str(row[c]) for c in cols
                ) + " |"
            )
    fm += [
        "",
        "## Caveats",
        "",
    ]
    fm += [f"- {w}" for w in result.get("warnings", [])]
    fm += [
        "",
        "## Provenance",
        "",
        "Executed SQL (canonical BigQuery dialect):",
        "",
        "```sql",
        result["query"],
        "```",
        "",
        "Bound parameters:",
        "",
        "```json",
        json.dumps(result["query_params"]),
        "```",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(fm))
    return path


def step_explore(r: Runner, state: dict):
    """The exploratory lane: a real analyst question no registered metric serves
    (confirmed-to-returned is not in the catalog), answered ad hoc, stamped T3."""
    sql = (
        "SELECT cohort_week, channel, "
        "SAFE_DIVIDE(SUM(orders_returned), SUM(orders_confirmed)) AS confirmed_to_returned_rate, "
        "SUM(orders_returned) AS numerator, SUM(orders_confirmed) AS denominator "
        "FROM analytics.cart_checkout_funnel_weekly "
        "WHERE cohort_week >= @start_week "
        "GROUP BY cohort_week, channel ORDER BY cohort_week, channel"
    )
    out = r.json([
        helper("explore", "kattfot_explore.py"),
        "--question", "Of confirmed orders, what share is ultimately returned, by channel?",
        "--sql", sql,
        "--param", "start_week=2026-04-06",
    ])
    assert out["accuracy_tier"] == "T3"
    assert out["fixture_backed"] is True
    assert any(w.startswith("EXPLORATORY (T3)") for w in out["warnings"]), (
        "the T3 warning must be unconditional"
    )
    assert out["manifest"]["register_sha"] and out["manifest"]["query_sha"]
    artifact = state["outputs"] / f"{AS_OF}-explore-confirmed-to-returned.md"
    compose_explore_artifact(out, artifact)
    state["explore_artifact"] = artifact

    # The T3 chart lane: the same spec/render pipeline as a governed metric,
    # with the EXPLORATORY stamp carried into the spec and the rendered HTML.
    charts = state["outputs"] / "charts"
    spec_path = charts / f"{AS_OF}-explore-confirmed-to-returned.spec.json"
    spec = r.json([
        helper("visualize-metric", "chart_spec.py"),
        "--artifact", artifact, "--out", spec_path,
    ])
    assert spec["exploratory"] is True, "the exploratory stamp must be in the spec"
    assert spec["accuracy_tier"] == "T3"
    assert spec["mock"] is True
    html = r.json([
        helper("render-chart", "render_html.py"),
        "--spec", spec_path,
        "--out", charts / f"{AS_OF}-explore-confirmed-to-returned.html",
    ])
    assert "EXPLORATORY (T3)" in Path(html["html"]).read_text(), (
        "the exploratory banner must reach the rendered chart"
    )
    return (
        f"{out['row_count']} rows · {out['source_mode']} · T3-stamped · "
        f"surfaces {','.join(out['surfaces'])} · charted as {spec['chart_type']} "
        f"with the EXPLORATORY banner",
        {"artifact": artifact, "chart": Path(html["html"])},
    )


def step_promote(r: Runner, state: dict):
    """Exploration -> reviewed draft: tamper-checked scaffold, inert until a
    human edits the tier (the R4 gate is the review gate)."""
    drafts = state["outputs"] / "drafts"
    out = r.json([
        helper("promote", "promote_metric.py"),
        "--artifact", state["explore_artifact"],
        "--name", "confirmed_to_returned_rate",
        "--out-dir", drafts,
        # every other step overwrites its own prior output on a re-run of the
        # gate; the draft is no different
        "--force",
    ])
    assert out["prefilled"] is True, "a SUM/SUM single-surface shape must prefill"
    draft = Path(out["draft_path"])
    text = draft.read_text()
    assert "accuracy_tier: T3" in text and "status: draft" in text, (
        "the draft must be inert until human review"
    )
    # The review gate, demonstrated: query-metric refuses the T3 draft.
    gate = r.run(
        [
            helper("query-metric", "kattfot_query.py"),
            "--metric", "confirmed_to_returned_rate",
        ],
        allow_exit=(1,),
        extra_env={"KATTFOT_METRICS_DIR": str(drafts)},
    )
    assert "ERROR (R4)" in gate.stderr, "the T3 draft must be refused until reviewed"
    return (
        f"draft at {draft.name} · prefilled · R4-refused until reviewed "
        f"(needs: {', '.join(out['needs_decision'])})",
        {"draft": draft},
    )


STEPS = {
    "setup": step_setup,
    "catalog": step_catalog,
    "coverage": step_coverage,
    "load-context": step_load_context,
    "query-metric": step_query_metric,
    "visualize-metric": step_visualize_metric,
    "render-chart": step_render_chart,
    "render-dashboard": step_render_dashboard,
    "export-report": step_export_report,
    "verify": step_verify,
    "reconcile": step_reconcile,
    "funnel-dropoff": step_funnel_dropoff,
    "stage-timing": step_stage_timing,
    "segment-compare": step_segment_compare,
    "funnel-pulse": step_funnel_pulse,
    "funnel-nowcast": step_funnel_nowcast,
    "explore": step_explore,
    "promote": step_promote,
}


def missing_optional(skill: str) -> str | None:
    """The requirements file to install, or None if the dependency is present.

    Probed with `find_spec` rather than an import: importing matplotlib here would
    execute it in the *parent* process, where `MPLCONFIGDIR` has not been scrubbed,
    and it prints a cache-directory warning that lands in the middle of the gate's
    own output. A presence check has no business running the module it checks for.
    """
    dep = OPTIONAL_DEPS.get(skill)
    if not dep:
        return None
    module, req = dep
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    return None if found else req


def outputs_dir() -> Path:
    return Path(os.environ.get("KATTFOT_OUTPUTS_DIR") or REPO_ROOT / "outputs" / "kattfot")


def rel(path) -> str:
    try:
        return str(Path(path).relative_to(REPO_ROOT))
    except (ValueError, TypeError):
        return str(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default=None,
                    help="comma-separated skill names to run (default: all sixteen)")
    ap.add_argument("--allow-missing-optional", action="store_true",
                    help="report a missing matplotlib/fpdf2 as a gap instead of failing")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args()

    selected = SKILL_ORDER
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [s for s in wanted if s not in STEPS]
        if unknown:
            raise SystemExit(f"ERROR: unknown skill(s) {unknown}. Known: {list(SKILL_ORDER)}")
        selected = tuple(s for s in SKILL_ORDER if s in wanted)

    outputs = outputs_dir()
    outputs.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="kattfot-offline-home-"))
    runner = Runner(outputs, home)
    state: dict = {"outputs": outputs}

    results = []
    for skill in selected:
        gap = missing_optional(skill)
        if gap and args.allow_missing_optional:
            results.append({"skill": skill, "status": "deps-missing",
                            "detail": f"install {gap}", "outputs": {}})
            continue
        if gap:
            results.append({
                "skill": skill, "status": "FAIL",
                "detail": (f"missing an offline dependency. Install it with:\n"
                           f"      .venv/bin/pip install -r {gap}\n"
                           f"      (or re-run with --allow-missing-optional)"),
                "outputs": {},
            })
            continue
        try:
            detail, produced = STEPS[skill](runner, state)
            results.append({"skill": skill, "status": "ok", "detail": detail,
                            "outputs": {k: rel(v) for k, v in produced.items()}})
        except Exception as exc:  # noqa: BLE001
            # Broad on purpose: one skill failing must not stop the summary, because
            # "which of the sixteen are broken" is the whole output of this script.
            results.append({"skill": skill, "status": "FAIL",
                            "detail": f"{type(exc).__name__}: {exc}", "outputs": {}})

    ran = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] == "FAIL"]
    gaps = [r for r in results if r["status"] == "deps-missing"]

    summary = {
        "selected": list(selected),
        "ran": len(ran),
        "failed": len(failed),
        "deps_missing": len(gaps),
        "engine": "sqlite",
        "credentials": "none — HOME scrubbed, no ADC, no project id",
        "outputs_dir": rel(outputs),
        "results": results,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("kattfot — all skills, offline (engine sqlite, no credentials, no network)\n")
        for r in results:
            mark = {"ok": "  ok  ", "FAIL": " FAIL ", "deps-missing": " deps "}[r["status"]]
            print(f"[{mark}] {r['skill']:<18} {r['detail']}")
            for kind, path in r["outputs"].items():
                print(f"{'':<28} {kind}: {path}")
        print(
            f"\n{len(ran)}/{len(selected)} skills ran offline"
            + (f", {len(gaps)} skipped for a missing offline dependency" if gaps else "")
            + (f", {len(failed)} FAILED" if failed else "")
            + f". Outputs in {rel(outputs)}/."
        )
        if not failed and not gaps and len(ran) == len(SKILL_ORDER):
            print(
                "Every functional skill in this template ran with no network, no cloud "
                "account, and no credentials. All numbers are fixture-backed and "
                "watermarked."
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
