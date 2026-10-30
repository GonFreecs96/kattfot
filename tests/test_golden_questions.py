"""Golden questions: the per-case regression gate for query-metric's resolution path.

`tests/golden/questions.yaml` holds the cases — one question each, in two to four
phrasings, with ONE pinned expectation: the governed request it must become or the
refusal rule that must fire. Every (case, phrasing) pair below is its own test, so a
single regression fails by name. There is no aggregate pass rate, on purpose: a
suite that passes at 97% has hidden which three percent now answer the wrong
question.

Hold-out: `resolve_question.resolve` takes the phrasing and the catalog, nothing
else. The expectation is read here and compared against what comes back; it never
reaches the resolver. `test_resolver_signature_admits_no_expectation` pins that.

What is under test is the real path: the resolver's alias/keyword layer decides
what to hand the helper, and `kattfot_query.load_metric`, `validate_tier` and
`validate_request` — the same functions `kattfot_query.main` calls — do every
refusal. The two definitions under `tests/golden/metrics/` (a T1 and a T3) are
mounted as the consumer metrics dir so R4 fires on real definitions rather than on
a stub dict; the bundled catalog is T2-only by lint.

The resolver is deterministic matching, not an LLM. That is the honest scope: a
phrasing outside its vocabulary fails here by name, which is the signal to add an
alias or a vocabulary entry — never to loosen the expectation.
"""

import inspect
from pathlib import Path

import pytest
import yaml

import kattfot_query as aq
import resolve_question as rq

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"
CASES = yaml.safe_load((GOLDEN / "questions.yaml").read_text())["cases"]
REFUSAL_RULES = ("R1", "R1-AMBIGUOUS", "R2", "R3", "R4", "R5", "R6")
KINDS = ("capability", "regression")


def _params():
    for case in CASES:
        for i, phrasing in enumerate(case["phrasings"]):
            yield pytest.param(
                case,
                phrasing,
                id=f"{case['id']}[{i}]",
                marks=getattr(pytest.mark, case["kind"]),
            )


@pytest.fixture(scope="module")
def golden_env():
    """Mount the T1/T3 fixtures as the consumer metrics dir — the same override a
    deploying team uses — for the whole module, then undo it."""
    mp = pytest.MonkeyPatch()
    mp.setenv("KATTFOT_METRICS_DIR", str(GOLDEN / "metrics"))
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def catalog(golden_env):
    return rq.load_catalog()


# --- the gate: one test per (case, phrasing) ---------------------------------


@pytest.mark.parametrize("case,phrasing", list(_params()))
def test_golden_question(case, phrasing, catalog, golden_env):
    expect = case["expect"]
    out = rq.resolve(phrasing, catalog)  # the phrasing, and nothing else

    if "refusal" in expect:
        assert isinstance(out, rq.Refusal), (
            f"{case['id']}: expected refusal {expect['refusal']}, got a resolution "
            f"to {out.metric!r} with dims={out.dimensions} filters={out.filters}"
        )
        assert out.rule == expect["refusal"], (
            f"{case['id']}: expected {expect['refusal']}, got {out.rule}: {out.message}"
        )
        # R1-AMBIGUOUS must name every pinned reading; R1 must suggest the pinned
        # metric. Both are the "useful refusal" half of the contract.
        for key in ("candidates", "suggests"):
            for name in expect.get(key) or []:
                assert name in out.candidates, (
                    f"{case['id']}: refusal did not name {name!r}; it named {out.candidates}"
                )
        return

    assert isinstance(out, rq.Resolution), (
        f"{case['id']}: expected {expect['metric']}, got refusal {out.rule}: {out.message}"
    )
    assert out.metric == expect["metric"]
    assert sorted(out.dimensions) == sorted(expect.get("dimensions") or [])
    assert out.filters == (expect.get("filters") or {})
    if expect.get("time_range"):
        assert out.time_range == expect["time_range"]


# --- structural promises the case file makes about itself ----------------------


def test_case_file_shape():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in CASES:
        assert c["kind"] in KINDS, f"{c['id']}: kind must be one of {KINDS}"
        assert c.get("rationale", "").strip(), f"{c['id']}: every case carries a rationale"
        assert 2 <= len(c["phrasings"]) <= 4, f"{c['id']}: two to four phrasings"
        exp = c["expect"]
        if "refusal" in exp:
            assert exp["refusal"] in REFUSAL_RULES, f"{c['id']}: unknown rule {exp['refusal']}"
            assert "metric" not in exp, f"{c['id']}: a refusal case pins no metric"
        else:
            assert exp.get("metric"), f"{c['id']}: a resolution case pins a metric"
            assert "dimensions" in exp and "filters" in exp, (
                f"{c['id']}: pin dimensions and filters explicitly, even when empty"
            )


def test_every_bundled_metric_has_a_capability_case():
    """Adding a metric without a golden question fails here, by metric name."""
    bundled = set(aq.available_metric_names(aq.bundled_metrics_dir()))
    covered = {c["expect"]["metric"] for c in CASES if "metric" in c["expect"]}
    assert bundled <= covered, f"bundled metrics with no golden case: {sorted(bundled - covered)}"


def test_every_refusal_rule_has_at_least_two_cases():
    counts = {rule: 0 for rule in REFUSAL_RULES}
    for c in CASES:
        rule = c["expect"].get("refusal")
        if rule:
            counts[rule] += 1
    thin = {r: n for r, n in counts.items() if n < 2}
    assert not thin, f"refusal rules with fewer than two golden cases: {thin}"


def test_should_not_answer_cases_cover_tier_domain_and_individual():
    """The explicit should-not-answer set: a T1 ask, a T3 ask, out-of-domain, and
    individual-level — at least four cases between them, not one catch-all."""
    rules = [c["expect"].get("refusal") for c in CASES]
    assert rules.count("R4") >= 2 and rules.count("R5") >= 2 and rules.count("R1") >= 2


# --- hold-out and path pins -----------------------------------------------------


def test_resolver_signature_admits_no_expectation():
    """The expected answer cannot be passed in: the resolver takes a question and
    a catalog. A later 'hint' parameter would turn the gate into a tautology."""
    assert list(inspect.signature(rq.resolve).parameters) == ["question", "catalog"]


def test_bare_registered_name_resolves_through_the_helper(golden_env, catalog):
    """A caller who already knows the name gets the helper's own exact-name path,
    which is also how a consumer override wins on a collision."""
    out = rq.resolve("payment_approval_rate", catalog)
    assert isinstance(out, rq.Resolution)
    assert out.metric == "payment_approval_rate" and out.matched_on == "payment_approval_rate"


def test_golden_fixtures_are_not_served_tiers(golden_env):
    """The two fixture definitions exist to make R4 fire on a real definition; if
    one quietly became T2 the R4 cases would be testing nothing."""
    tiers = {}
    for p in (GOLDEN / "metrics").glob("*.md"):
        fm = yaml.safe_load(aq.FRONTMATTER_RE.match(p.read_text()).group(1))
        tiers[fm["name"]] = fm["accuracy_tier"]
    assert set(tiers.values()) == {"T1", "T3"}, tiers


def test_resolver_is_not_an_llm():
    """Documented scope, pinned: the module imports nothing that could call a
    model. The matching is a vocabulary, and the vocabulary is the contract."""
    src = Path(rq.__file__).read_text()
    for forbidden in ("anthropic", "openai", "requests", "httpx", "urllib"):
        assert f"import {forbidden}" not in src and f"from {forbidden}" not in src
