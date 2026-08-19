"""Catalog linter: every bundled metric and entity is a valid governed definition.

This is the automated form of the contract written down in
`query-metric/AUTHORING.md` and `entities/README.md`. It is what turns the catalog
from "trust the author" into a checked invariant, and it is the gate any future
metric PR has to pass.

Files under `metrics/` without a `name:` key (a `LINEAGE.md`) and under `entities/`
without an `entity:` key (a `README.md`) are documentation and are skipped —
mirroring exactly what the runtime resolver does, so the linter cannot hold a file
to a contract the resolver never applies to it.
"""

from pathlib import Path

import pytest
import yaml

import assay_query as aq

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "assay"
QUERY_METRIC_DIR = PLUGIN / "skills" / "query-metric"
METRICS_DIR = QUERY_METRIC_DIR / "metrics"
ENTITIES_DIR = QUERY_METRIC_DIR / "entities"
REGISTRY_PATH = PLUGIN / "config" / "modules.yaml"

REQUIRED_FIELDS = [
    "name",
    "display_name",
    "accuracy_tier",
    "source_view",
    "source_project",
    "time_column",
    "numerator_column",
    "denominator_column",
    "value_format",
    "allowed_dimensions",
    "allowed_filters",
    "default_lookback_weeks",
    "status",
    "entity",     # every metric names the entity its num/den measures
    "module",     # and the module it belongs to
]
VALID_TIERS = {"T1", "T2", "T3"}
SERVED_TIER = "T2"  # the only tier the runtime serves; T1/T3 refuse under R4
VALID_STATUS = {"dummy", "live", "deprecated"}
VALID_VALUE_FORMAT = {"percent", "count", "currency", "duration"}
VALID_TIME_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}

ENTITY_REQUIRED_FIELDS = ["entity", "display_name", "module", "grain"]
VALID_CARDINALITIES = {
    "one_to_one",
    "one_to_zero_or_one",
    "one_to_many",
    "many_to_one",
    "many_to_many",
}
VALID_ENTITY_STATUS = {"live", "declared", "deprecated"}

# The entities the checkout module must declare. Guards against silent loss — the
# selection rule is "backs a metric or anchors a governed dimension", and every one
# of these does.
EXPECTED_CORE_ENTITIES = {
    "checkout_session",
    "order",
    "payment",
    "shipment",
    "customer",
    "channel",
    "market",
}


def _load_frontmatter(path: Path):
    m = aq.FRONTMATTER_RE.match(path.read_text())
    return (yaml.safe_load(m.group(1)) or {}) if m else None


def _metric_files():
    out = []
    for p in sorted(METRICS_DIR.rglob("*.md")):
        fm = _load_frontmatter(p)
        if fm and "name" in fm:
            out.append((p, fm))
    return out


def _entity_files():
    out = []
    if ENTITIES_DIR.exists():
        for p in sorted(ENTITIES_DIR.rglob("*.md")):
            fm = _load_frontmatter(p)
            if fm and "entity" in fm:
                out.append((p, fm))
    return out


METRICS = _metric_files()
_IDS = [p.relative_to(METRICS_DIR).as_posix() for p, _ in METRICS]
METRIC_NAMES = {fm["name"] for _, fm in METRICS}

ENTITIES = _entity_files()
_ENTITY_IDS = [p.name for p, _ in ENTITIES]
REGISTERED_ENTITIES = {fm["entity"] for _, fm in ENTITIES}

REGISTERED_MODULES = set((yaml.safe_load(REGISTRY_PATH.read_text()) or {}).get("modules", {}))


# --- the metric catalog ------------------------------------------------------


def test_catalog_is_discoverable():
    """The checkout module ships 15 metrics. Guard against silent loss — a resolver
    that finds nothing refuses cleanly, which reads like a configuration problem
    rather than a missing catalog."""
    assert len(METRICS) >= 15, (
        f"only found {len(METRICS)} metric files under {METRICS_DIR}"
    )


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_required_fields_present(path, fm):
    missing = [f for f in REQUIRED_FIELDS if f not in fm]
    assert not missing, f"{path.name} is missing required fields: {missing}"


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_enums_and_types_valid(path, fm):
    assert fm["accuracy_tier"] in VALID_TIERS, f"{path.name}: bad tier {fm['accuracy_tier']!r}"
    assert fm["status"] in VALID_STATUS, f"{path.name}: bad status {fm['status']!r}"
    assert fm["value_format"] in VALID_VALUE_FORMAT, (
        f"{path.name}: bad value_format {fm['value_format']!r}"
    )
    if "time_column_type" in fm:
        assert str(fm["time_column_type"]).upper() in VALID_TIME_TYPES
    assert isinstance(fm["allowed_dimensions"], list), f"{path.name}: allowed_dimensions not a list"
    assert isinstance(fm["allowed_filters"], list), f"{path.name}: allowed_filters not a list"
    assert isinstance(fm["default_lookback_weeks"], int), (
        f"{path.name}: default_lookback_weeks not an int"
    )
    assert "." in fm["source_view"], f"{path.name}: source_view must be dataset.table"


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_served_metrics_are_t2(path, fm):
    """Governance invariant. A bundled T1 metric would be refused under R4 and a T3
    one has no governed path — either way it is unreachable, so shipping one is a
    footgun rather than a feature. Relaxing this is a deliberate, reviewed change;
    see doctrine/accuracy-tiers.md."""
    assert fm["accuracy_tier"] == SERVED_TIER, (
        f"{path.name} is {fm['accuracy_tier']}; only {SERVED_TIER} is served."
    )


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_module_matches_directory(path, fm):
    assert fm.get("module") == path.parent.name, (
        f"{path.name}: module {fm.get('module')!r} != directory {path.parent.name!r}"
    )


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_module_is_registered(path, fm):
    assert fm["module"] in REGISTERED_MODULES, (
        f"{path.name}: module {fm['module']!r} is not in config/modules.yaml "
        f"(registered: {sorted(REGISTERED_MODULES)})"
    )


def test_metric_names_are_unique():
    names = [fm["name"] for _, fm in METRICS]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate metric names (the helper resolves by name): {dupes}"


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_metric_declares_a_maturity_gate(path, fm):
    """A metric with no maturity gate reports a deep-funnel rate for last week's
    cohort as though the answer were in — and wrong in the flattering direction.
    Either `maturity_stage` (the normal case) or an explicit `maturity_lag_days`."""
    assert fm.get("maturity_stage") or fm.get("maturity_lag_days") is not None, (
        f"{path.name}: no maturity_stage or maturity_lag_days — the metric would "
        f"never withhold an unsettled cohort"
    )


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_metric_body_has_a_caveats_section(path, fm):
    """Caveats are reproduced verbatim into every artifact, chart, and PDF, so an
    empty section is not a stylistic gap — it is a governed number travelling with
    nothing attached that would change how a reader interprets it."""
    body = aq.FRONTMATTER_RE.match(path.read_text()).group(2)
    assert "## Caveats" in body, f"{path.name}: no '## Caveats' section"
    section = body.split("## Caveats", 1)[1].split("\n## ", 1)[0]
    bullets = [ln for ln in section.splitlines() if ln.strip().startswith("- ")]
    assert len(bullets) >= 2, (
        f"{path.name}: only {len(bullets)} caveat bullet(s). A governed metric with "
        f"nothing worth warning about is almost always an unexamined one."
    )


# --- the entity registry ----------------------------------------------------


def test_entity_registry_is_discoverable():
    assert len(ENTITIES) >= 7, (
        f"only found {len(ENTITIES)} entity files under {ENTITIES_DIR}"
    )


def test_registry_declares_the_expected_core_entities():
    missing = sorted(EXPECTED_CORE_ENTITIES - REGISTERED_ENTITIES)
    assert not missing, f"entity registry is missing expected entities: {missing}"


def test_entity_slugs_are_unique():
    slugs = [fm["entity"] for _, fm in ENTITIES]
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    assert not dupes, f"duplicate entity slugs: {dupes}"


@pytest.mark.parametrize("path,fm", ENTITIES, ids=_ENTITY_IDS)
def test_entity_required_fields_present(path, fm):
    missing = [f for f in ENTITY_REQUIRED_FIELDS if f not in fm]
    assert not missing, f"{path.name} is missing required entity fields: {missing}"


@pytest.mark.parametrize("path,fm", ENTITIES, ids=_ENTITY_IDS)
def test_entity_module_is_registered(path, fm):
    assert fm["module"] in REGISTERED_MODULES, (
        f"{path.name}: module {fm['module']!r} is not in config/modules.yaml"
    )


@pytest.mark.parametrize("path,fm", ENTITIES, ids=_ENTITY_IDS)
def test_entity_relationships_are_well_formed(path, fm):
    rels = fm.get("relationships") or []
    assert isinstance(rels, list), f"{path.name}: relationships must be a list"
    for r in rels:
        assert isinstance(r, dict), f"{path.name}: each relationship must be a mapping, got {r!r}"
        to = r.get("to")
        assert to in REGISTERED_ENTITIES, (
            f"{path.name}: relationship targets unregistered entity {to!r}"
        )
        assert r.get("kind") in VALID_CARDINALITIES, (
            f"{path.name}: relationship to {to!r} has bad kind {r.get('kind')!r} "
            f"(valid: {sorted(VALID_CARDINALITIES)})"
        )
        for m in r.get("backs") or []:
            assert m in METRIC_NAMES, (
                f"{path.name}: relationship to {to!r} claims to back unknown metric {m!r}"
            )


@pytest.mark.parametrize("path,fm", ENTITIES, ids=_ENTITY_IDS)
def test_entity_business_rules_are_well_formed(path, fm):
    rules = fm.get("business_rules") or []
    assert isinstance(rules, list), f"{path.name}: business_rules must be a list"
    for r in rules:
        assert isinstance(r, dict), f"{path.name}: each business_rule must be a mapping, got {r!r}"
        assert r.get("id"), f"{path.name}: a business_rule is missing its `id`"
        assert r.get("source"), f"{path.name}: business_rule {r.get('id')!r} is missing `source`"


@pytest.mark.parametrize("path,fm", ENTITIES, ids=_ENTITY_IDS)
def test_entity_status_flags_are_well_formed(path, fm):
    if "status" in fm:
        assert fm["status"] in VALID_ENTITY_STATUS, (
            f"{path.name}: bad status {fm['status']!r} (valid: {sorted(VALID_ENTITY_STATUS)})"
        )
    if "metric_backed" in fm:
        assert isinstance(fm["metric_backed"], bool), (
            f"{path.name}: metric_backed must be a bool, got {fm['metric_backed']!r}"
        )


@pytest.mark.parametrize("path,fm", ENTITIES, ids=_ENTITY_IDS)
def test_entity_governed_dimensions_are_well_formed(path, fm):
    """A `dimensions:` block is how a CATEGORICAL is modelled — anchored to its
    parent entity rather than given a file of its own. Each entry must enumerate its
    values, because an un-enumerated dimension is just a column name."""
    dims = fm.get("dimensions")
    if dims is None:
        return
    assert isinstance(dims, list), f"{path.name}: dimensions must be a list"
    for d in dims:
        assert isinstance(d, dict), f"{path.name}: each dimension must be a mapping, got {d!r}"
        assert d.get("name"), f"{path.name}: a governed dimension is missing its `name`"
        vals = d.get("values")
        assert isinstance(vals, list) and vals, (
            f"{path.name}: dimension {d.get('name')!r} must enumerate a non-empty "
            f"`values` list"
        )


@pytest.mark.parametrize("path,fm", ENTITIES, ids=_ENTITY_IDS)
def test_declared_entities_back_no_metrics(path, fm):
    """A forward-looking (`status: declared`) entity is CONTEXT, not a metric grain:
    it is not in a governed view, so it must set `metric_backed: false` and must not
    claim to carry a live metric."""
    if fm.get("status") != "declared":
        return
    assert fm.get("metric_backed") is False, (
        f"{path.name}: a declared entity must set `metric_backed: false`"
    )
    for r in fm.get("relationships") or []:
        assert not r.get("backs"), (
            f"{path.name}: a declared entity must not back a live metric ({r.get('backs')!r})"
        )


@pytest.mark.parametrize("path,fm", ENTITIES, ids=_ENTITY_IDS)
def test_non_backed_entities_claim_no_backs_edge(path, fm):
    """The same rule, keyed on the other signal: an entity that says it backs no
    metric must not then name one. `customer` in this catalog is exactly that case —
    joinable and registered as context, with no metric at person grain."""
    if fm.get("metric_backed") is not False:
        return
    for r in fm.get("relationships") or []:
        assert not r.get("backs"), (
            f"{path.name}: metric_backed is false but a relationship claims "
            f"`backs: {r.get('backs')!r}`"
        )


# --- metric <-> entity coherence --------------------------------------------


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_metric_entity_is_registered(path, fm):
    ent = fm.get("entity")
    assert ent in REGISTERED_ENTITIES, (
        f"{path.name}: entity {ent!r} is not registered (declare it under "
        f"query-metric/entities/). Registered: {sorted(REGISTERED_ENTITIES)}"
    )


@pytest.mark.parametrize("path,fm", METRICS, ids=_IDS)
def test_metric_optional_entity_refs_are_well_formed(path, fm):
    rels = fm.get("relationships")
    if rels is not None:
        assert isinstance(rels, list), f"{path.name}: relationships must be a list of slugs"
        for slug in rels:
            assert slug in REGISTERED_ENTITIES, (
                f"{path.name}: relationships names unregistered entity {slug!r}"
            )
    rules = fm.get("business_rules")
    if rules is not None:
        assert isinstance(rules, list), f"{path.name}: business_rules must be a list of ids"
        for rid in rules:
            assert isinstance(rid, str) and rid, f"{path.name}: bad business_rule id {rid!r}"


def test_metric_entity_is_metric_backed():
    """A metric's primary `entity:` must be one that actually backs metrics — never
    a `declared` entity, and never one flagged `metric_backed: false`. Tightened on
    EITHER signal so a future live-but-not-backed entity cannot slip through."""
    non_backed = {
        fm["entity"]
        for _, fm in ENTITIES
        if fm.get("status") == "declared" or fm.get("metric_backed") is False
    }
    offenders = sorted(
        {fm.get("entity") for _, fm in METRICS if fm.get("entity") in non_backed}
    )
    assert not offenders, (
        f"metrics name non-metric-backed entities as primary: {offenders}"
    )


def test_no_bundled_metric_allow_lists_a_pii_column():
    """R6 is checked at query time regardless, but an allow-list containing a PII
    column is a latent authoring error worth catching at lint time — the refusal
    would read as a puzzling runtime failure rather than a bad definition."""
    from _shared import pii

    offenders = []
    for path, fm in METRICS:
        for col in list(fm.get("allowed_dimensions") or []) + list(fm.get("allowed_filters") or []):
            if pii.is_pii_column(col):
                offenders.append(f"{path.name}: {col} ({pii.pii_reason(col)})")
    assert not offenders, f"metrics allow-list PII columns: {offenders}"
