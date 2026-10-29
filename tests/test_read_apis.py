"""The three read APIs: `catalog`, `load-context`, and `coverage`.

None of these touches a warehouse, which is exactly why they need testing: a skill
that only reads files looks impossible to get wrong, and has two failure modes that
matter.

**Drift.** A catalog that enumerates from memory, or from a list in its own source,
is wrong the first time somebody adds a metric — and wrong *silently*, because the
metric exists, works, and is invisible. So the tests here add a metric on disk and
assert it appears.

**Leaking a guarantee at the boundary.** The accuracy-tier gate is worth nothing if
it holds inside this repo and evaporates when another tool reads a definition out.
A consumer handed a T1 or T3 body with no marking will present it exactly like a
governed T2 number. `TIER_REFUSED` is therefore tested as a refusal with a
governance exit code, not as a formatting detail.

Offline and in-process throughout.
"""

import json
import sys
from pathlib import Path

import pytest

import catalog as catalog_lib
import coverage_lookup
import load_context
from _shared import coverage as cov

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "kattfot"


def run(module, argv, capsys) -> str:
    """Call a helper's `main()` and return stdout, asserting a clean exit."""
    old = sys.argv
    sys.argv = [module.__name__] + argv
    try:
        code = module.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code in (0, None), f"unexpected exit {code}: {out}"
    return out


def run_expecting(module, argv, capsys, code) -> str:
    old = sys.argv
    sys.argv = [module.__name__] + argv
    try:
        actual = module.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert actual == code, f"expected exit {code}, got {actual}: {out}"
    return out


# ===========================================================================
# catalog
# ===========================================================================


def test_catalog_enumerates_every_bundled_metric():
    """Counted from the files on disk, so this cannot pass against a stale list."""
    on_disk = {
        p.stem
        for p in (PLUGIN / "skills" / "query-metric" / "metrics").rglob("*.md")
        if p.name != "LINEAGE.md"
    }
    cat = catalog_lib.build()
    assert cat["metric_count"] == len(on_disk)
    assert cat["metric_count"] == 15
    for m in cat["metrics"]:
        assert m["name"] and m["display_name"] and m["accuracy_tier"] and m["module"]
        assert m["definition"], f"{m['name']} has no one-liner from its ## Definition"


def test_catalog_skips_documentation_files():
    """`LINEAGE.md` has no `name:` and is documentation. The catalog must skip exactly
    what the resolver skips, or the linter and the catalog hold a file to a contract
    the runtime never applies to it."""
    names = {m["name"] for m in catalog_lib.build()["metrics"]}
    assert not any("lineage" in n.lower() for n in names)


def test_catalog_picks_up_a_consumer_metric(tmp_path, monkeypatch):
    """The drift test. A metric added to the consumer directory has to appear without
    anybody editing the catalog skill."""
    (tmp_path / "extra.md").write_text(
        "---\nname: my_local_rate\ndisplay_name: My Local Rate\n"
        "accuracy_tier: T2\nmodule: checkout\nstatus: live\n---\n\n"
        "## Definition\nA locally authored rate, for the drift test.\n"
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(tmp_path))
    cat = catalog_lib.build()
    hit = next((m for m in cat["metrics"] if m["name"] == "my_local_rate"), None)
    assert hit is not None, "a consumer-authored metric did not reach the catalog"
    assert hit["definition"].startswith("A locally authored rate")
    assert cat["metric_count"] == 16


def test_a_consumer_metric_overrides_a_bundled_one_of_the_same_name(tmp_path, monkeypatch):
    """Consumer-wins on a `name:` collision — the resolver's rule, so a team can
    override one metric without forking the catalog. If the catalog listed both, it
    would show a definition the runtime will never use."""
    (tmp_path / "override.md").write_text(
        "---\nname: payment_approval_rate\ndisplay_name: OVERRIDDEN\n"
        "accuracy_tier: T2\nmodule: checkout\nstatus: live\n---\n\n"
        "## Definition\nThe local override.\n"
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(tmp_path))
    cat = catalog_lib.build()
    hits = [m for m in cat["metrics"] if m["name"] == "payment_approval_rate"]
    assert len(hits) == 1
    assert hits[0]["display_name"] == "OVERRIDDEN"


def test_catalog_lists_latency_kpis_from_every_registered_spec():
    """A percentile KPI cannot be expressed as a metric definition, so it lives in a
    funnel spec. If the catalog only walked `metrics/`, `time_to_ship_days` would be
    undiscoverable."""
    kpis = {k["key"] for k in catalog_lib.build()["latency_kpis"]}
    assert {"time_to_ship_days", "time_to_decision_minutes"} <= kpis
    assert "time_to_cart_minutes" in kpis, "the browse spec's KPI is missing"


def test_a_kpi_on_a_declared_spine_is_marked_not_queryable():
    """The honesty requirement. The browse spine's contract is authored and linted but
    no view is provisioned, so its KPI must be listed AND flagged. Listing it as
    available is the catalog's version of the registry's false promise; hiding it
    loses the fact that the contract is fixed."""
    kpis = {k["key"]: k for k in catalog_lib.build()["latency_kpis"]}
    browse = kpis["time_to_cart_minutes"]
    assert browse["queryable"] is False
    assert "declared" in browse["not_queryable_because"]
    checkout = kpis["time_to_ship_days"]
    assert checkout["queryable"] is True
    assert checkout["not_queryable_because"] is None


def test_catalog_reports_spine_status_and_the_handoff_edge():
    spines = {(s["module"], s["spine"]): s for s in catalog_lib.build()["spines"]}
    checkout = spines[("checkout", "session")]
    assert checkout["status"] == "live"
    assert set(checkout["skills"]) == {
        "funnel-dropoff", "stage-timing", "segment-compare", "funnel-pulse",
        "funnel-nowcast",
    }
    browse = spines[("browse", "session")]
    assert browse["status"] == "declared"
    assert browse["skills"] == []
    assert browse["handoff"]["to"] == "checkout/session"


def test_catalog_markdown_renders_both_tables(capsys):
    out = run(catalog_lib, ["--format", "markdown"], capsys)
    assert "## Ratio metrics" in out
    assert "## Latency KPIs" in out
    assert "## Spines" in out
    assert "payment_approval_rate" in out
    assert "time_to_ship_days" in out


def test_catalog_json_is_parseable(capsys):
    cat = json.loads(run(catalog_lib, [], capsys))
    assert cat["skill"] == "catalog"
    assert cat["metrics"] and cat["latency_kpis"] and cat["spines"]


def test_catalog_never_runs_a_query(monkeypatch):
    """The boundary. A catalog that reached a warehouse to enrich a listing would make
    "what can I ask" a billable operation, and would need credentials to answer."""
    from _shared import adapters

    def boom(*a, **k):
        raise AssertionError("catalog must not execute a query")

    monkeypatch.setattr(adapters, "get_adapter", boom)
    assert catalog_lib.build()["metric_count"] == 15


# --- definition freshness (doctrine/ownership-governance.md) -----------------

import datetime as dt  # noqa: E402  (kept beside the tests that pin the clock)

REVIEWED = dt.date(2026, 10, 28)  # every bundled definition's last_reviewed


def test_freshness_is_computed_against_a_pinned_clock():
    """Inside the window is fresh, one day past it is stale, and the boundary day
    itself is still fresh. Pinned so the suite does not flip on a calendar date."""
    f = catalog_lib.freshness
    assert f("2026-10-28", today=REVIEWED, window_days=180) == "fresh"
    assert f(REVIEWED, today=REVIEWED + dt.timedelta(days=180), window_days=180) == "fresh"
    assert f(REVIEWED, today=REVIEWED + dt.timedelta(days=181), window_days=180) == "stale"
    assert f(dt.datetime(2026, 10, 28, 9, 0), today=REVIEWED, window_days=180) == "fresh"


def test_a_missing_or_unparseable_review_date_is_stale_not_fresh():
    """The doctrine's one warning: a freshness signal that silently fails to fire
    reads as fresh. So absence and garbage both fail closed."""
    f = catalog_lib.freshness
    assert f(None, today=REVIEWED, window_days=180) == "stale"
    assert f("", today=REVIEWED, window_days=180) == "stale"
    assert f("last week", today=REVIEWED, window_days=180) == "stale"
    assert f("2026-13-45", today=REVIEWED, window_days=180) == "stale"


def test_the_review_window_defaults_to_180_days_and_reads_the_env(monkeypatch):
    monkeypatch.delenv("KATTFOT_REVIEW_WINDOW_DAYS", raising=False)
    assert catalog_lib.review_window_days() == 180
    assert catalog_lib.freshness(REVIEWED, today=REVIEWED + dt.timedelta(days=30)) == "fresh"
    monkeypatch.setenv("KATTFOT_REVIEW_WINDOW_DAYS", "7")
    assert catalog_lib.review_window_days() == 7
    assert catalog_lib.freshness(REVIEWED, today=REVIEWED + dt.timedelta(days=30)) == "stale"
    monkeypatch.setenv("KATTFOT_REVIEW_WINDOW_DAYS", "not a number")
    assert catalog_lib.review_window_days() == 180


def test_every_bundled_definition_lists_its_version_and_freshness():
    """The listing carries the keys the linter requires, and the signal flips for
    the whole catalog when the clock moves past the window."""
    fresh = catalog_lib.build(today=REVIEWED)
    assert fresh["review_window_days"] == 180
    assert fresh["stale_count"] == 0
    for m in fresh["metrics"]:
        assert isinstance(m["version"], int) and m["version"] >= 1
        assert m["last_reviewed"] == REVIEWED.isoformat()
        assert m["freshness"] == "fresh"
    later = catalog_lib.build(today=REVIEWED + dt.timedelta(days=400))
    assert later["stale_count"] == later["metric_count"] == 15
    assert {m["freshness"] for m in later["metrics"]} == {"stale"}


def test_markdown_surfaces_the_stale_marker_with_date_and_owner():
    """The doctrine asks for a `STALE:` marker next to the metric wherever it is
    listed, naming the owner and the date it was last reviewed."""
    fresh = catalog_lib.render_markdown(catalog_lib.build(today=REVIEWED))
    assert "STALE:" not in fresh
    assert "fresh (2026-10-28)" in fresh
    stale = catalog_lib.render_markdown(
        catalog_lib.build(today=REVIEWED + dt.timedelta(days=400))
    )
    assert "15 stale" in stale
    assert "STALE: last reviewed 2026-10-28, owner: payments-pm" in stale


def test_a_consumer_metric_without_a_review_date_lists_as_stale(tmp_path, monkeypatch):
    """A consumer override that never set `last_reviewed` is listed — the catalog is
    not the linter — but it is listed as stale, never as fresh by default."""
    (tmp_path / "extra.md").write_text(
        "---\nname: my_local_rate\ndisplay_name: My Local Rate\nowner: me\n"
        "accuracy_tier: T2\nmodule: checkout\nstatus: live\n---\n\n"
        "## Definition\nA locally authored rate.\n"
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(tmp_path))
    cat = catalog_lib.build(today=REVIEWED)
    hit = next(m for m in cat["metrics"] if m["name"] == "my_local_rate")
    assert hit["freshness"] == "stale" and hit["last_reviewed"] is None
    assert cat["stale_count"] == 1
    assert "STALE: never reviewed, owner: me" in catalog_lib.render_markdown(cat)


def test_catalog_cli_pins_the_clock_with_today(capsys):
    out = json.loads(run(catalog_lib, ["--today", "2030-01-01"], capsys))
    assert out["stale_count"] == 15
    out = json.loads(run(catalog_lib, ["--today", REVIEWED.isoformat()], capsys))
    assert out["stale_count"] == 0


def test_load_context_catalog_carries_freshness(capsys):
    out = run(load_context, ["catalog"], capsys)
    assert "| version | freshness |" in out
    assert "| 1 | fresh |" in out or "| 1 | stale |" in out


# ===========================================================================
# coverage
# ===========================================================================


def test_coverage_lists_the_whole_register(capsys):
    payload = json.loads(run(coverage_lookup, [], capsys))
    assert payload["mode"] == "list"
    assert payload["register_size"] >= 6
    assert set(payload["by_status"]) <= cov.STATUSES


def test_coverage_matches_a_stakeholders_phrasing(capsys):
    payload = json.loads(run(coverage_lookup, ["--term", "why was the cart abandoned"], capsys))
    assert [m["id"] for m in payload["matches"]] == ["cart_abandon_reason"]
    assert payload["matches"][0]["queryable"] is False
    assert payload["matches"][0]["blocker"]


def test_coverage_resolves_an_id(capsys):
    payload = json.loads(run(coverage_lookup, ["--id", "stage_timing"], capsys))
    assert payload["matches"][0]["status"] == "live"
    assert payload["matches"][0]["queryable"] is True


def test_a_no_match_exits_nonzero_and_routes_rather_than_guessing(capsys):
    """"Not in the register" and "not measurable" are different statements, and
    conflating them turns a gap in the register into a false fact about the data."""
    out = run_expecting(coverage_lookup, ["--term", "quantum widget throughput"], capsys, 1)
    payload = json.loads(out)
    assert payload["matches"] == []
    md = coverage_lookup.render(payload)
    assert "NO_COVERAGE_ENTRY" in md
    assert "Do not read this as 'not measurable'" in md


def test_partial_entries_render_their_blocker_verbatim(capsys):
    """A summarised blocker is how a hard limit becomes a soft caveat. The whole
    string has to survive to the output."""
    payload = json.loads(run(coverage_lookup, ["--id", "return_reason_codes"], capsys))
    entry = payload["matches"][0]
    assert entry["status"] == "partial"
    assert entry["queryable"] is True
    md = coverage_lookup.render(payload)
    assert entry["blocker"] in md
    assert "31%" in md


def test_coverage_markdown_states_a_verdict_per_status(capsys):
    for entry_id, verdict in (
        ("checkout_funnel_conversion", "Yes —"),
        ("return_reason_codes", "Yes, but"),
        ("payment_retry_attempts", "Not yet"),
        ("cart_abandon_reason", "No —"),
        ("on_site_search_terms", "not coming"),
        ("wallet_provider_breakdown", "Not yet"),
    ):
        payload = json.loads(run(coverage_lookup, ["--id", entry_id], capsys))
        assert verdict in coverage_lookup.render(payload), f"{entry_id} has no verdict line"


def test_coverage_honours_the_env_override(tmp_path, monkeypatch, capsys):
    p = tmp_path / "mine.yaml"
    p.write_text("coverage:\n  - {id: local_thing, asks: [local], status: live, serve: here}\n")
    monkeypatch.setenv("KATTFOT_COVERAGE_FILE", str(p))
    payload = json.loads(run(coverage_lookup, [], capsys))
    assert payload["register_size"] == 1


def test_an_empty_register_exits_nonzero_rather_than_reporting_full_coverage(
    tmp_path, monkeypatch, capsys
):
    """The degradation that matters. A missing register loads as `{}`, and an empty
    listing must not read as "nothing is blocked"."""
    monkeypatch.setenv("KATTFOT_COVERAGE_FILE", str(tmp_path / "absent.yaml"))
    payload = json.loads(run_expecting(coverage_lookup, [], capsys, 1))
    assert payload["register_size"] == 0


# ===========================================================================
# load-context
# ===========================================================================


def test_no_scope_lists_the_scopes(capsys):
    out = run(load_context, [], capsys)
    for scope in ("catalog", "metric/{slug}", "entities", "coverage", "spines"):
        assert scope in out


def test_metric_scope_returns_the_file_verbatim(capsys):
    """The file, not a paraphrase. A summarised definition is a second, unversioned
    definition, and the point of a governed context layer is that there is one."""
    out = run(load_context, ["metric/payment_approval_rate"], capsys)
    source = (
        PLUGIN / "skills" / "query-metric" / "metrics" / "checkout" / "payment-approval-rate.md"
    ).read_text()
    assert out.startswith(source.rstrip()[:200])
    assert "name: payment_approval_rate" in out


def test_compact_returns_frontmatter_only(capsys):
    out = run(load_context, ["metric/payment_approval_rate", "--compact"], capsys)
    assert "name: payment_approval_rate" in out
    assert "## Definition" not in out


def test_module_scope_uses_the_documented_separator(capsys):
    out = run(load_context, ["module/checkout", "--compact", "--limit", "3"], capsys)
    assert out.count("--- ") >= 3
    assert "Showing items 1-3 of 15" in out
    assert "--offset 3" in out


def test_paging_reaches_the_end_without_a_next_pointer(capsys):
    out = run(load_context, ["module/checkout", "--compact", "--limit", "10", "--offset", "10"], capsys)
    assert "Showing items 11-15 of 15" in out
    assert "--offset" not in out.split("Showing")[-1]


@pytest.mark.parametrize("scope", ["catalog", "entities", "spines", "coverage", "tiers", "governance"])
def test_the_documented_scopes_all_resolve(scope, capsys):
    assert run(load_context, [scope], capsys).strip()


def test_entity_scope_returns_the_declared_browse_grain(capsys):
    out = run(load_context, ["entity/browse_session"], capsys)
    assert "status: declared" in out
    assert "metric_backed: false" in out
    assert "denominator" in out


def test_spines_scope_carries_the_handoff_and_its_caveat(capsys):
    """The handoff edge's coverage caveat is governed context. Without this scope the
    only route to it is reading the YAML directly, which is the path-coupling the
    scope vocabulary exists to avoid."""
    payload = json.loads(run(load_context, ["spines"], capsys))
    browse = next(s for s in payload["spines"] if s["module"] == "browse")
    handoff = browse["handoff"]
    assert handoff["to"] == "checkout/session"
    assert handoff["on"] == "checkout_session_id"
    assert handoff["coverage_basis"] == "declared_estimate"
    assert handoff["coverage_measured_at"] is None
    assert "conversion rate" in handoff["coverage_note"].lower()
    assert "browse sessions" in handoff["coverage_denominator"]


def test_lineage_scope_returns_the_module_note(capsys):
    out = run(load_context, ["lineage/checkout"], capsys)
    assert "cohort_vs_activity_time" in out
    assert "per_stage_maturity" in out


def test_coverage_entry_scope_returns_one_entry(capsys):
    out = run(load_context, ["coverage/cart_abandon_reason"], capsys)
    assert "id: cart_abandon_reason" in out
    assert "status: blocked" in out


# --- the sentinels ---------------------------------------------------------


def test_unknown_scope_is_scope_not_found_with_a_suggestion(capsys):
    out = run_expecting(load_context, ["governence"], capsys, 1)
    assert out.startswith("SCOPE_NOT_FOUND -- ")
    assert "governance" in out


def test_unknown_metric_is_scope_not_found_and_lists_what_exists(capsys):
    out = run_expecting(load_context, ["metric/payment_aproval_rate"], capsys, 1)
    assert out.startswith("SCOPE_NOT_FOUND -- ")
    assert "Did you mean: payment_approval_rate" in out


def test_unknown_entity_is_scope_not_found(capsys):
    out = run_expecting(load_context, ["entity/nope"], capsys, 1)
    assert out.startswith("SCOPE_NOT_FOUND -- ")


def test_absent_glossary_is_empty_result_not_an_error(capsys):
    """A promised-but-unpopulated scope is a documented state. `EMPTY_RESULT` says
    "the scope exists and holds nothing", which is different from "no such scope"."""
    out = run_expecting(load_context, ["glossary"], capsys, 1)
    assert out.startswith("EMPTY_RESULT -- ")


def test_a_path_escaping_the_read_roots_is_refused(capsys):
    """Checked on the RESOLVED path. A textual check on the slug would have to
    anticipate every encoding of "parent directory"; resolving first and comparing
    after is the only ordering that is actually safe."""
    out = run_expecting(load_context, ["lineage/../../../../etc"], capsys, 5)
    assert out.startswith("PATH_BOUNDARY -- ")


def test_path_boundary_uses_the_governance_exit_code(capsys):
    """5 is the refusal code the spine skills use. A boundary escape is a governance
    event, not a lookup miss, and the exit code has to say which."""
    run_expecting(load_context, ["lineage/../../../../etc"], capsys, 5)


def test_a_non_t2_metric_is_tier_refused(tmp_path, monkeypatch, capsys):
    """The load-bearing sentinel. The tier guarantee has to survive the interop
    boundary: a consumer handed a T3 body with no marking will present it exactly like
    a governed T2 number, in a briefing, to someone who acts on it. So the body is
    never returned."""
    (tmp_path / "explore.md").write_text(
        "---\nname: exploratory_thing\ndisplay_name: Exploratory Thing\n"
        "accuracy_tier: T3\nmodule: checkout\nstatus: live\n---\n\n"
        "## Definition\nAn unreviewed exploratory number.\n"
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(tmp_path))
    out = run_expecting(load_context, ["metric/exploratory_thing"], capsys, 5)
    assert out.startswith("TIER_REFUSED -- ")
    assert "T3" in out
    assert "An unreviewed exploratory number" not in out, "the body leaked despite the refusal"


@pytest.mark.parametrize("tier", ["T1", "T3"])
def test_both_ungoverned_tiers_are_refused(tier, tmp_path, monkeypatch, capsys):
    (tmp_path / "m.md").write_text(
        f"---\nname: ungoverned\ndisplay_name: U\naccuracy_tier: {tier}\n"
        f"module: checkout\nstatus: live\n---\n\n## Definition\nBody text.\n"
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(tmp_path))
    out = run_expecting(load_context, ["metric/ungoverned"], capsys, 5)
    assert out.startswith("TIER_REFUSED -- ")
    assert "Body text" not in out


@pytest.mark.parametrize("tier, referred", [("T3", True), ("T1", False)])
def test_the_explorable_referral_follows_t3_refusals_only(
    tier, referred, tmp_path, monkeypatch, capsys
):
    """The optional `EXPLORABLE -- ` second line from the consumer contract:
    a T3 definition has a lane to be referred to; T1 belongs to the system of
    record and gets no referral."""
    (tmp_path / "m.md").write_text(
        f"---\nname: ungoverned\ndisplay_name: U\naccuracy_tier: {tier}\n"
        f"module: checkout\nstatus: live\n---\n\n## Definition\nBody text.\n"
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(tmp_path))
    out = run_expecting(load_context, ["metric/ungoverned"], capsys, 5)
    assert ("\nEXPLORABLE -- " in out) is referred
    if referred:
        assert "kattfot:explore" in out


def test_a_module_read_withholds_a_non_t2_body_without_refusing_the_set(
    tmp_path, monkeypatch, capsys
):
    """A caller who asked for a set gets the set. Refusing it wholesale over one
    member would make the scope unusable; returning that member's body would break the
    guarantee. So it is named with its tier and withheld."""
    (tmp_path / "m.md").write_text(
        "---\nname: ungoverned\ndisplay_name: U\naccuracy_tier: T3\n"
        "module: checkout\nstatus: live\n---\n\n## Definition\nSecret body.\n"
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(tmp_path))
    out = run(load_context, ["module/checkout", "--limit", "40"], capsys)
    assert "TIER_REFUSED -- ungoverned is tier T3" in out
    assert "Secret body" not in out
    assert "name: payment_approval_rate" in out, "the T2 members must still come back"


def test_the_catalog_scope_names_a_non_t2_metric_without_serving_it(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "m.md").write_text(
        "---\nname: ungoverned\ndisplay_name: U\naccuracy_tier: T3\n"
        "module: checkout\nstatus: live\n---\n\n## Definition\nSecret body.\n"
    )
    monkeypatch.setenv("KATTFOT_METRICS_DIR", str(tmp_path))
    out = run(load_context, ["catalog"], capsys)
    assert "ungoverned" in out
    assert "T3" in out
    # The one-liner is the first line of the metric's own `## Definition`, so it is
    # body content and must not travel through the governed API either.
    assert "Secret body" not in out
    assert "TIER_REFUSED — tier T3, body withheld" in out
    assert "NOT" in out and "served" in out
    # A T2 metric's one-liner is still served, or the scope would be useless.
    assert "Of the payments that received a decision" in out


def test_load_context_and_catalog_describe_the_same_catalog(capsys):
    """The one failure a consumer API of this kind is most likely to have: the machine
    front door and the human one disagreeing about what is registered."""
    from_api = run(load_context, ["catalog"], capsys)
    for m in catalog_lib.build()["metrics"]:
        assert m["name"] in from_api


def test_load_context_never_runs_a_query(monkeypatch, capsys):
    from _shared import adapters

    def boom(*a, **k):
        raise AssertionError("load-context must not execute a query")

    monkeypatch.setattr(adapters, "get_adapter", boom)
    for scope in ("catalog", "entities", "coverage", "spines", "metric/payment_approval_rate"):
        assert run(load_context, [scope], capsys).strip()


def test_every_read_root_is_inside_the_plugin():
    """The allow-list has to be an allow-list. A root pointing outside the plugin — a
    home directory, `/tmp` — would make the boundary check decorative."""
    for name, root in load_context.read_roots().items():
        if name == "consumer_metrics":
            continue  # legitimately consumer-supplied, and cwd-relative by default
        assert PLUGIN.resolve() in root.resolve().parents or root.resolve() == PLUGIN.resolve(), (
            f"read root {name} ({root}) is outside the plugin"
        )
