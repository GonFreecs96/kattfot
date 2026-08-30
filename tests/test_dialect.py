"""The dialect translator's golden test: BigQuery SQL in, sqlite SQL out.

Every skill in this plugin authors SQL in one canonical dialect (BigQuery) and the
sqlite adapter translates it once. Because everything funnels through one function,
the translation set can be **enumerated** rather than sampled — and that is what
makes a golden test here meaningful instead of decorative.

The golden cases live inline, as (input, expected) pairs, rather than in a separate
fixture file. That is deliberate: the pairing *is* the documentation of the
contract, and a reviewer reading a diff should see the before and the after side by
side without opening a second file.

Three layers, each catching a different kind of regression:

  1. **Per-rule goldens** — one case per entry in `dialect.TRANSLATIONS`, with a
     test asserting the two lists have not drifted apart. A new rule with no golden
     case fails.
  2. **Composite goldens** — the real SQL the repo's own builders emit, translated
     whole, because rules interact (a rewrite injects text that a later rule must
     also see).
  3. **The totality guard** — `assert_translated` must reject anything the table
     does not cover, so an unlisted construct fails loudly at translation instead
     of being silently mis-parsed by sqlite.
"""

import datetime as dt

import pytest

from _shared import dialect


# ---------------------------------------------------------------------------
# 1. Per-rule goldens — one per entry in dialect.TRANSLATIONS
# ---------------------------------------------------------------------------
# Keyed by translation id. `None` marks a rule that is not a SQL-text rewrite
# (parameter binding) or that is deliberately refused rather than translated;
# each has its own dedicated test below.

GOLDEN: dict[str, tuple[str, str]] = {
    "named_params": (
        "SELECT x FROM t WHERE d BETWEEN @start_date AND @end_date AND c = @filter_0",
        'SELECT x FROM t WHERE d BETWEEN :start_date AND :end_date AND c = :filter_0',
    ),
    "qualified_identifier": (
        "SELECT 1 FROM `my-project.analytics.cart_checkout_funnel_weekly`",
        'SELECT 1 FROM "cart_checkout_funnel_weekly"',
    ),
    "safe_divide": (
        "SELECT SAFE_DIVIDE(SUM(num), SUM(den)) AS r FROM t",
        "SELECT (CAST(SUM(num) AS REAL) / NULLIF(SUM(den), 0)) AS r FROM t",
    ),
    "countif": (
        "SELECT COUNTIF(flag) AS n FROM t",
        "SELECT SUM(CASE WHEN (flag) THEN 1 ELSE 0 END) AS n FROM t",
    ),
    "cast_string": (
        "SELECT CAST(is_first_order AS STRING) AS c FROM t",
        "SELECT CAST(is_first_order AS TEXT) AS c FROM t",
    ),
    "safe_cast": (
        "SELECT SAFE_CAST(is_first_order AS STRING) AS c FROM t",
        "SELECT CAST(is_first_order AS TEXT) AS c FROM t",
    ),
    "if_function": (
        "SELECT IF(cohort_week <= @cut_bound, mins, NULL) AS d FROM t",
        "SELECT CASE WHEN (cohort_week <= :cut_bound) THEN mins ELSE NULL END AS d FROM t",
    ),
    "date_diff": (
        "SELECT DATE_DIFF(delivered_date, created_date, DAY) AS d FROM t",
        "SELECT CAST(julianday(delivered_date) - julianday(created_date) AS INTEGER) AS d FROM t",
    ),
    "timestamp_diff": (
        "SELECT TIMESTAMP_DIFF(shipped_at, order_confirmed_at, MINUTE) AS m FROM t",
        "SELECT CAST((julianday(shipped_at) - julianday(order_confirmed_at)) * 1440 AS INTEGER) AS m FROM t",
    ),
    "current_date": (
        "SELECT 1 FROM t WHERE cohort_week < CURRENT_DATE()",
        "SELECT 1 FROM t WHERE cohort_week < DATE('now')",
    ),
    # Not text rewrites — covered by dedicated tests below.
    "approx_quantiles": None,
    "date_params": None,
}

_TEXT_CASES = [(k, *v) for k, v in GOLDEN.items() if v is not None]


def test_every_translation_rule_has_a_golden_case():
    """The claim this suite makes is that the translation set is TOTAL. That claim
    is only checkable if the golden table and the rule table cannot drift apart, so
    assert the ids match exactly in both directions."""
    documented = set(dialect.TRANSLATION_IDS)
    covered = set(GOLDEN)
    assert covered == documented, (
        f"golden cases and dialect.TRANSLATIONS disagree — "
        f"missing goldens: {sorted(documented - covered)}, "
        f"stale goldens: {sorted(covered - documented)}"
    )


@pytest.mark.parametrize(
    "rule_id,bigquery,expected", _TEXT_CASES, ids=[c[0] for c in _TEXT_CASES]
)
def test_rule_golden(rule_id, bigquery, expected):
    assert dialect.translate_sql(bigquery) == expected


# ---------------------------------------------------------------------------
# 2. Composite goldens — the SQL the repo's own builders actually emit
# ---------------------------------------------------------------------------
# Rules interact: SAFE_DIVIDE injects a CAST that the AS-STRING rule must not
# mangle, and every rewrite injects text the parameter rule has to see. A per-rule
# suite cannot catch an ordering bug; these can.

QUERY_METRIC_SQL = """SELECT
  cohort_week, channel,
  SAFE_DIVIDE(SUM(checkouts_started), SUM(carts_created)) AS cart_to_checkout_start_conversion,
  SUM(checkouts_started) AS numerator,
  SUM(carts_created) AS denominator
FROM `${KATTFOT_BIGQUERY_PROJECT}.analytics.cart_checkout_funnel_weekly`
WHERE cohort_week BETWEEN @start_date AND @end_date AND cohort_week <= @maturity_cutoff
GROUP BY cohort_week, channel
ORDER BY cohort_week, channel"""

QUERY_METRIC_SQLITE = """SELECT
  cohort_week, channel,
  (CAST(SUM(checkouts_started) AS REAL) / NULLIF(SUM(carts_created), 0)) AS cart_to_checkout_start_conversion,
  SUM(checkouts_started) AS numerator,
  SUM(carts_created) AS denominator
FROM "cart_checkout_funnel_weekly"
WHERE cohort_week BETWEEN :start_date AND :end_date AND cohort_week <= :maturity_cutoff
GROUP BY cohort_week, channel
ORDER BY cohort_week, channel"""


def test_query_metric_sql_golden():
    """The exact shape `kattfot_query.build_sql` produces. If this drifts, every
    fixture-backed number in the repo drifted with it."""
    assert dialect.translate_sql(QUERY_METRIC_SQL) == QUERY_METRIC_SQLITE


# The shape a spine-backed drop-off query takes: per-stage gated COUNTIFs over a
# cast dimension. Not built by any shipped skill yet (Phase 3), which is exactly why
# it is pinned now — the translator has to already handle what those builders emit.
SPINE_STAGE_SQL = """SELECT
  CAST(is_first_order AS STRING) AS grp,
  COUNTIF(cart_created_flag) AS n_created,
  COUNTIF(payment_decisioned_flag AND cohort_week <= @cut_payment_decisioned) AS n_decisioned,
  COUNTIF(return_window_complete_flag AND cohort_week <= @cut_deep) AS deep_complete,
  SAFE_DIVIDE(COUNTIF(shipped_flag), COUNTIF(order_confirmed_flag)) AS ship_rate
FROM `proj.analytics.cart_checkout_session_spine`
WHERE cohort_week BETWEEN @start_date AND @end_date
  AND CAST(is_first_order AS STRING) != @legacy_excluded
GROUP BY grp"""

SPINE_STAGE_SQLITE = """SELECT
  CAST(is_first_order AS TEXT) AS grp,
  SUM(CASE WHEN (cart_created_flag) THEN 1 ELSE 0 END) AS n_created,
  SUM(CASE WHEN (payment_decisioned_flag AND cohort_week <= :cut_payment_decisioned) THEN 1 ELSE 0 END) AS n_decisioned,
  SUM(CASE WHEN (return_window_complete_flag AND cohort_week <= :cut_deep) THEN 1 ELSE 0 END) AS deep_complete,
  (CAST(SUM(CASE WHEN (shipped_flag) THEN 1 ELSE 0 END) AS REAL) / NULLIF(SUM(CASE WHEN (order_confirmed_flag) THEN 1 ELSE 0 END), 0)) AS ship_rate
FROM "cart_checkout_session_spine"
WHERE cohort_week BETWEEN :start_date AND :end_date
  AND CAST(is_first_order AS TEXT) != :legacy_excluded
GROUP BY grp"""


def test_spine_stage_sql_golden():
    """SAFE_DIVIDE wrapping two COUNTIFs is the case a naive regex translator gets
    wrong: the arguments contain their own parens and commas."""
    assert dialect.translate_sql(SPINE_STAGE_SQL) == SPINE_STAGE_SQLITE


def test_nested_countif_inside_safe_divide_is_balanced():
    """Isolate the nesting property from the larger golden, so a failure says
    which of the two things broke."""
    out = dialect.translate_sql("SELECT SAFE_DIVIDE(COUNTIF(a AND b), COUNTIF(c)) AS r")
    assert out == (
        "SELECT (CAST(SUM(CASE WHEN (a AND b) THEN 1 ELSE 0 END) AS REAL) "
        "/ NULLIF(SUM(CASE WHEN (c) THEN 1 ELSE 0 END), 0)) AS r"
    )


# ---------------------------------------------------------------------------
# 3. The totality guard
# ---------------------------------------------------------------------------


def test_approx_quantiles_is_refused_not_guessed():
    """sqlite has no percentile function. Faking one in SQL would be slow and wrong
    on ties, so percentiles are an adapter capability instead — and this construct
    must never reach the translator."""
    with pytest.raises(dialect.DialectError) as exc:
        dialect.translate_sql(
            "SELECT APPROX_QUANTILES(mins_confirm_to_ship, 100)[OFFSET(90)] AS p90 FROM t"
        )
    assert "adapters" in str(exc.value)  # points at the supported route


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT DATE_TRUNC(d, WEEK(MONDAY)) FROM t",
        "SELECT x FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1",
        "SELECT SAFE.PARSE_DATE('%Y', y) FROM t",
        "SELECT * FROM UNNEST(arr)",
    ],
)
def test_untranslatable_constructs_raise(sql):
    """A construct outside the table fails loudly at translation. The alternative —
    passing it through — hands sqlite something it will either reject with an
    opaque parse error or, worse, parse into a different number."""
    with pytest.raises(dialect.DialectError):
        dialect.translate_sql(sql)


def test_unsupported_diff_unit_raises():
    with pytest.raises(dialect.DialectError):
        dialect.translate_sql("SELECT DATE_DIFF(a, b, QUARTER) FROM t")


def test_assert_translated_accepts_clean_sqlite():
    dialect.assert_translated('SELECT a FROM "t" WHERE d BETWEEN :s AND :e')


def test_if_residual_check_does_not_fire_on_column_names():
    """The residual scan looks for `IF(` as a CALL. A column named `is_first_order`
    or a bare `IF` inside prose must not trip it, or every real query would fail
    the guard."""
    dialect.assert_translated(
        'SELECT is_first_order, notify_flag, iff_col FROM "t" WHERE x = :v'
    )


# ---------------------------------------------------------------------------
# Parameter translation
# ---------------------------------------------------------------------------


def test_dates_bind_as_iso_text():
    """sqlite has no date type, so the bind value must be ISO-8601 text — the one
    representation whose lexicographic order matches chronological order."""
    params = dialect.translate_params(
        {"start_date": dt.date(2026, 3, 2), "end_date": dt.date(2026, 8, 10)}
    )
    assert params == {"start_date": "2026-03-02", "end_date": "2026-08-10"}
    assert all(isinstance(v, str) for v in params.values())


def test_datetimes_bind_as_iso_text():
    params = dialect.translate_params(
        {"t": dt.datetime(2026, 3, 2, 13, 45, 9)}
    )
    assert params["t"] == "2026-03-02 13:45:09"


def test_booleans_bind_as_ints():
    assert dialect.translate_params({"a": True, "b": False}) == {"a": 1, "b": 0}


def test_strings_and_numbers_pass_through():
    assert dialect.translate_params({"s": "web", "n": 7, "f": 1.5}) == {
        "s": "web", "n": 7, "f": 1.5,
    }


def test_table_name_flattens_any_depth():
    assert dialect.table_name("proj.analytics.v") == "v"
    assert dialect.table_name("analytics.v") == "v"
    assert dialect.table_name("v") == "v"
    assert dialect.table_name("`proj.analytics.v`") == "v"


def test_translate_returns_both_halves():
    sql, params = dialect.translate(
        "SELECT 1 FROM `p.d.v` WHERE d = @d", {"d": dt.date(2026, 1, 5)}
    )
    assert sql == 'SELECT 1 FROM "v" WHERE d = :d'
    assert params == {"d": "2026-01-05"}
