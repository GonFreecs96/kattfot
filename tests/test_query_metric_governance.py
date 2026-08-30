"""Governance: the query helper's refusal and SQL-safety contract.

These cover the rules the *helper code* enforces, behind the agent layer:

  R1  unregistered metric            -> find_metric_file raises SystemExit
  R2  dimension not in allow-list    -> validate_request raises SystemExit
  R3  filter not in allow-list       -> validate_request raises SystemExit
  R4  tier not served (T1 / T3)      -> validate_tier raises SystemExit
  R6  PII dimension or filter        -> validate_request raises, no override

plus the SQL-safety, source-mode, project-resolution, and time-range invariants the
helper owns.

Why they belong in code and not only in the prompt: a helper invoked directly — by a
script, a scheduled job, another plugin — never passes through the agent layer at
all. A rule that lives only in a prompt is advisory the moment anything other than
the agent calls the helper. R5 (no individual-level output) is the one rule that
genuinely cannot be enforced here: it is a property of the question, not of the
arguments, so it stays an agent-layer rule and is documented as such.
"""

import datetime as dt

import pytest

import kattfot_query as aq


# --- R2 / R3: allow-list validation -----------------------------------------


def test_rejects_unlisted_dimension():
    metric = {"name": "m", "allowed_dimensions": ["market"], "allowed_filters": []}
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, ["unauthorized_dim"], {})
    assert "R2" in str(exc.value)


def test_rejects_unlisted_filter():
    metric = {"name": "m", "allowed_dimensions": [], "allowed_filters": ["market"]}
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, [], {"unauthorized_col": "x"})
    assert "R3" in str(exc.value)


def test_refusal_lists_what_is_allowed():
    """A refusal that does not show the allow-list makes the user guess. Every R2/R3
    message names the alternatives."""
    metric = {"name": "m", "allowed_dimensions": ["market", "channel"], "allowed_filters": []}
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, ["nope"], {})
    msg = str(exc.value)
    assert "market" in msg and "channel" in msg


def test_allows_listed_dimension_and_filter():
    metric = {"name": "m", "allowed_dimensions": ["market"], "allowed_filters": ["market"]}
    aq.validate_request(metric, ["market"], {"market": "us"})  # must not raise


# --- R6: PII outranks the allow-list ----------------------------------------


def test_pii_dimension_refused_even_when_allow_listed():
    metric = {"name": "m", "allowed_dimensions": ["email"], "allowed_filters": []}
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, ["email"], {})
    assert "R6" in str(exc.value)
    assert "No override" in str(exc.value)


def test_pii_filter_refused_even_when_allow_listed():
    metric = {"name": "m", "allowed_dimensions": [], "allowed_filters": ["customer_phone"]}
    with pytest.raises(SystemExit):
        aq.validate_request(metric, [], {"customer_phone": "x"})


def test_pii_refusal_names_the_category():
    metric = {"name": "m", "allowed_dimensions": [], "allowed_filters": []}
    with pytest.raises(SystemExit) as exc:
        aq.validate_request(metric, ["date_of_birth"], {})
    assert "date_of_birth" in str(exc.value)


# --- R4: the tier gate ------------------------------------------------------


def test_t2_is_served():
    aq.validate_tier({"name": "m", "accuracy_tier": "T2"})  # must not raise


def test_t1_is_refused():
    """T1 must come from the system of record. A governed convenience layer cannot
    inherit a control it does not implement."""
    with pytest.raises(SystemExit) as exc:
        aq.validate_tier({"name": "m", "accuracy_tier": "T1"})
    assert "R4" in str(exc.value)


def test_t3_is_refused():
    with pytest.raises(SystemExit) as exc:
        aq.validate_tier({"name": "m", "accuracy_tier": "T3"})
    assert "R4" in str(exc.value)


def test_missing_or_bogus_tier_is_refused():
    """An absent tier is not a default to T2. A number whose review status is
    unstated is exactly the thing this repo refuses to produce."""
    for tier in (None, "", "T4", "operational"):
        with pytest.raises(SystemExit):
            aq.validate_tier({"name": "m", "accuracy_tier": tier})


# --- R1: metric resolution --------------------------------------------------


def test_unregistered_metric_refused():
    with pytest.raises(SystemExit) as exc:
        aq.find_metric_file("definitely_not_a_real_metric_zzz")
    assert "R1" in str(exc.value)


def test_bundled_metric_loads():
    m = aq.load_metric("cart_to_checkout_start_conversion")
    assert m["accuracy_tier"] == "T2"
    assert m["source_view"]
    assert isinstance(m["allowed_dimensions"], list)
    assert m["_def_sha"]  # the definition fingerprint the manifest pins


# --- R1-AMBIGUOUS: a miss with several readings is refused, never ranked -----
# The text-to-SQL ambiguity literature (AmbiQT, EMNLP 2023) measures the
# failure this guards: models silently commit to ONE reading of an ambiguous
# ask. Here the ambiguous miss must name every reading and refuse to choose —
# because 'payment' answered as the approval rate is a different question than
# 'payment' answered as the decline rate, with a different denominator.


def test_generic_term_matching_many_metrics_is_ambiguous_not_ranked():
    with pytest.raises(SystemExit) as exc:
        aq.find_metric_file("payment")
    msg = str(exc.value)
    assert "R1-AMBIGUOUS" in msg
    # Every substring reading is named, not just a winner.
    assert "payment_approval_rate" in msg
    assert "payment_decline_rate" in msg
    assert "payment_submit_to_decision_rate" in msg
    assert "Refusing to guess" in msg


def test_unique_typo_still_gets_the_plain_did_you_mean_refusal():
    """One plausible reading is a typo, not an ambiguity — the helpful R1 with
    a suggestion stays, and must NOT escalate to the ambiguous form."""
    with pytest.raises(SystemExit) as exc:
        aq.find_metric_file("cart_to_stale_rte")
    msg = str(exc.value)
    assert "R1-AMBIGUOUS" not in msg
    assert "R1" in msg
    assert "cart_to_stale_rate" in msg  # the did-you-mean suggestion


def test_near_tie_fuzzy_matches_are_ambiguous():
    """Two candidates inside the score margin: picking the top one would
    answer a different question about half the time, so both are surfaced."""
    available = ["gross_revenue_rate", "gross_revenue_rank"]
    got = aq.ambiguous_candidates("gross_revenue_rat", available)
    assert set(got) == set(available)


def test_a_clear_winner_is_not_ambiguous():
    available = ["payment_approval_rate", "cart_to_checkout_start_conversion"]
    assert aq.ambiguous_candidates("payment_approval_rte", available) == []


def test_exact_names_never_trip_the_ambiguity_guard():
    """Every registered name must keep resolving exactly — the guard fires
    only on the refusal path, so governance gained a check without the happy
    path losing one."""
    m = aq.load_metric("payment_approval_rate")
    assert m["name"] == "payment_approval_rate"


# --- time-range parsing -----------------------------------------------------


def test_last_completed_monday_from_a_monday():
    # 2026-06-08 is a Monday; the most recent COMPLETED week starts 2026-06-01.
    assert aq.last_completed_monday(dt.date(2026, 6, 8)) == dt.date(2026, 6, 1)


def test_last_completed_monday_never_returns_a_partial_week():
    for offset in range(14):
        today = dt.date(2026, 6, 8) + dt.timedelta(days=offset)
        monday = aq.last_completed_monday(today)
        assert monday.weekday() == 0
        # The whole week (monday..monday+6) is in the past.
        assert monday + dt.timedelta(days=6) < today


def test_parse_time_range_last_n_weeks():
    today = dt.date(2026, 6, 8)
    start, end = aq.parse_time_range("last_12_weeks", 8, today)
    assert end == aq.last_completed_monday(today)
    assert (end - start).days == 11 * 7  # N weeks inclusive => (N-1)*7 days span


def test_parse_time_range_explicit_dates():
    start, end = aq.parse_time_range("2026-01-05:2026-03-30", 8, dt.date(2026, 6, 8))
    assert start == dt.date(2026, 1, 5)
    assert end == dt.date(2026, 3, 30)


def test_parse_time_range_default_uses_metric_lookback():
    today = dt.date(2026, 6, 8)
    start, end = aq.parse_time_range("", 26, today)
    assert (end - start).days == 25 * 7


def test_parse_time_range_bad_spec_refused():
    for bad in ("last_year", "2026-01-05", "since March", "last_-3_weeks"):
        with pytest.raises(SystemExit):
            aq.parse_time_range(bad, 8, dt.date(2026, 6, 8))


# --- filter parsing ---------------------------------------------------------


def test_parse_filters_ok_and_trims():
    assert aq.parse_filters(["market=us", " channel = web "]) == {
        "market": "us",
        "channel": "web",
    }


def test_parse_filters_keeps_equals_in_the_value():
    """Split on the FIRST `=` only, so a value containing one survives."""
    assert aq.parse_filters(["promo=a=b"]) == {"promo": "a=b"}


def test_parse_filters_rejects_missing_equals():
    with pytest.raises(SystemExit):
        aq.parse_filters(["noequalshere"])


# --- source mode ------------------------------------------------------------


def test_dummy_status_routes_to_mock_csv_on_any_engine():
    """`status: dummy` is a property of the METRIC — its view is not provisioned
    anywhere — so it holds regardless of engine."""
    assert aq.resolve_source_mode({"status": "dummy"}, "sqlite")[0] == "mock_csv"
    assert aq.resolve_source_mode({"status": "dummy"}, "bigquery")[0] == "mock_csv"


def test_live_status_routes_to_the_resolved_engine():
    assert aq.resolve_source_mode({"status": "live"}, "sqlite")[0] == "fixture_sql"
    assert aq.resolve_source_mode({"status": "live"}, "bigquery")[0] == "bigquery"


def test_missing_status_routes_to_the_engine_not_to_mock(monkeypatch):
    """No silent mock fallback: anything not declared `dummy` runs its real SQL."""
    monkeypatch.delenv("KATTFOT_ENGINE", raising=False)
    assert aq.resolve_source_mode({})[0] == "fixture_sql"


def test_source_mode_reason_is_always_populated():
    """The artifact prints the reason next to the mode. An empty one turns 'why is
    this fixture-backed?' into a code-reading exercise."""
    for metric, engine in (({"status": "dummy"}, "sqlite"), ({}, "sqlite"), ({}, "bigquery")):
        mode, reason = aq.resolve_source_mode(metric, engine)
        assert reason and len(reason) > 20, f"{mode}: reason {reason!r} is not explanatory"


def test_fixture_modes_are_the_watermarked_ones():
    assert aq.FIXTURE_SOURCE_MODES == {"fixture_sql", "mock_csv"}
    assert "bigquery" not in aq.FIXTURE_SOURCE_MODES


# --- project resolution -----------------------------------------------------


def test_resolve_project_prefers_a_concrete_metric_field():
    assert aq.resolve_project({"source_project": "proj-a"}) == "proj-a"


def test_resolve_project_expands_an_env_placeholder(monkeypatch):
    """The bundled catalog ships `${KATTFOT_BIGQUERY_PROJECT}` rather than a real
    project id — a template that baked one in would ship somebody else's billing
    account."""
    monkeypatch.setenv("KATTFOT_BIGQUERY_PROJECT", "my-real-project")
    assert aq.resolve_project(
        {"source_project": "${KATTFOT_BIGQUERY_PROJECT}"}, "bigquery"
    ) == "my-real-project"


def test_unresolved_placeholder_is_a_hard_error_on_bigquery(monkeypatch):
    monkeypatch.delenv("KATTFOT_BIGQUERY_PROJECT", raising=False)
    with pytest.raises(SystemExit) as exc:
        aq.resolve_project({"name": "m", "source_project": "${KATTFOT_BIGQUERY_PROJECT}"}, "bigquery")
    assert "KATTFOT_BIGQUERY_PROJECT" in str(exc.value)


def test_unresolved_placeholder_is_kept_verbatim_on_a_fixture_path(monkeypatch):
    """On a fixture read the project does not affect the result, and keeping the
    placeholder verbatim is both self-documenting and STABLE — which is what keeps the
    artifact's `query_sha` reproducible across runs."""
    monkeypatch.delenv("KATTFOT_BIGQUERY_PROJECT", raising=False)
    got = aq.resolve_project({"source_project": "${KATTFOT_BIGQUERY_PROJECT}"}, "fixture_sql")
    assert got == "${KATTFOT_BIGQUERY_PROJECT}"


def test_resolve_project_missing_entirely_is_refused_on_bigquery(monkeypatch):
    monkeypatch.delenv("KATTFOT_BIGQUERY_PROJECT", raising=False)
    with pytest.raises(SystemExit):
        aq.resolve_project({}, "bigquery")


def test_no_real_project_id_is_baked_into_the_catalog():
    """Every bundled metric must use the placeholder. A concrete project id in a
    public template is somebody's billing account and quite possibly a private
    identifier."""
    offenders = []
    for path in sorted((aq.bundled_metrics_dir()).rglob("*.md")):
        fm = aq.FRONTMATTER_RE.match(path.read_text())
        if not fm:
            continue
        import yaml

        data = yaml.safe_load(fm.group(1)) or {}
        proj = str(data.get("source_project", ""))
        if data.get("name") and not proj.startswith("${"):
            offenders.append(f"{path.name}: {proj}")
    assert not offenders, f"metrics carry a concrete source_project: {offenders}"


# --- SQL safety: values are bound, never inlined ----------------------------


def test_build_sql_parameterizes_filter_values():
    metric = {
        "name": "m",
        "source_view": "ds.v",
        "time_column": "cohort_week",
        "numerator_column": "num",
        "denominator_column": "den",
    }
    injection = "us'; DROP TABLE secrets;--"
    sql, params = aq.build_sql(metric, "proj", ["market"], {"market": injection})

    assert "@start_date" in sql and "@end_date" in sql
    assert "@filter_0" in sql
    # The attacker-controlled value must never reach the SQL text.
    assert injection not in sql
    assert "DROP TABLE" not in sql
    assert params["filter_0"] == injection


def test_build_sql_binds_every_filter_separately():
    metric = {
        "name": "m", "source_view": "ds.v", "time_column": "cohort_week",
        "numerator_column": "num", "denominator_column": "den",
    }
    sql, params = aq.build_sql(metric, "proj", [], {"market": "us", "channel": "web"})
    assert params == {"filter_0": "us", "filter_1": "web"}
    assert "@filter_0" in sql and "@filter_1" in sql


def test_build_sql_is_canonical_bigquery_on_every_engine(monkeypatch):
    """The SQL text does not depend on the engine. That is what makes an artifact's
    `query_sha` receipt comparable between a fixture read and a warehouse read, so
    `kattfot:verify` works across both."""
    metric = {
        "name": "m", "source_view": "ds.v", "time_column": "cohort_week",
        "numerator_column": "num", "denominator_column": "den",
    }
    monkeypatch.setenv("KATTFOT_ENGINE", "sqlite")
    sqlite_sql, _ = aq.build_sql(metric, "proj", [], {})
    monkeypatch.setenv("KATTFOT_ENGINE", "bigquery")
    bq_sql, _ = aq.build_sql(metric, "proj", [], {})
    assert sqlite_sql == bq_sql
    assert "SAFE_DIVIDE" in sqlite_sql  # canonical BigQuery, not sqlite
    assert "`proj.ds.v`" in sqlite_sql
