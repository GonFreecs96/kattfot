"""Module registry + funnel-spec contract linter.

Turns `config/modules.yaml`'s own stated rules into checked invariants, in the same
hand-rolled style as the rest of the suite: plain asserts over `yaml.safe_load`'d
config, no schema library.

Deliberately does NOT validate the internal shape of `kpis:` / `pulse:` /
`nowcast:` / `scorecard:` beyond presence-when-a-skill-needs-it. A skill's own
config sub-shape is that skill's test's job; a registry-level linter that reached
into all of them would need updating every time a skill gained a parameter.

Pure and offline — never touches a warehouse.
"""

from pathlib import Path

import pytest
import yaml

from _shared import spine

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "plugins" / "assay" / "config"
REGISTRY_PATH = CONFIG_DIR / "modules.yaml"

# The full universe of spine-backed skills a registry entry can name. The
# metric-layer skills (query-metric, visualize-metric) are absent on purpose: they
# resolve a metric's own `source_view`, not a spine, so listing them here would
# imply a relationship that does not exist.
ALL_SKILLS = {
    "funnel-dropoff",
    "stage-timing",
    "segment-compare",
    "funnel-pulse",
    "funnel-nowcast",
    "release-scorecard",
    "applicant-journey",
}

# The plumbing keys every funnel spec must carry.
#
# `legacy_exclude_column` / `legacy_exclude_value` are NOT in this list, and that is
# a considered choice rather than an oversight: a spine with no retired cohort
# legitimately has neither, and the both-or-neither rule that governs them is
# behavioural rather than structural. It is enforced in
# tests/test_legacy_exclude.py, against the builder, where the failure it prevents
# actually lives.
CORE_REQUIRED_SPEC_FIELDS = [
    "source_view",
    "source_project",
    "accuracy_tier",
    "primary_entity",
    "entities",
    "relationships",
    "cohort_column",
    "maturity_flag",
    "defaults",
    "allowed_dimensions",
    "allowed_filters",
    "string_cast",
]

# Per-skill spec keys that must be present for a skill to be listed as INCLUDED.
# Only genuinely hard-required keys — the ones whose absence is a crash, not a
# fallback. Soft, `.get()`-with-a-real-default keys are deliberately absent, or this
# check would block inclusions that would in fact work.
SKILL_REQUIRED_SPEC_FIELDS = {
    "funnel-dropoff": ["stages"],
    "stage-timing": ["transitions"],
    "segment-compare": ["stages", "transitions"],
    "funnel-pulse": ["stages"],
    "funnel-nowcast": ["nowcast"],
    "release-scorecard": ["stages"],
    "applicant-journey": ["rate_flags", "journey_flags"],
}


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _spines(registry: dict):
    for mod_name, mod in (registry.get("modules") or {}).items():
        for spine_name, cfg in (mod.get("spines") or {}).items():
            yield mod_name, spine_name, cfg


_REGISTRY = _load_yaml(REGISTRY_PATH)
_MODULES = list(_REGISTRY.get("modules", {}).items())
_MODULE_IDS = [m for m, _ in _MODULES]
_SPINES = list(_spines(_REGISTRY))
_SPINE_IDS = [f"{m}/{s}" for m, s, _ in _SPINES]


def _included_skill_cases():
    out = []
    for mod_name, spine_name, cfg in _SPINES:
        for skill in cfg.get("skills") or []:
            out.append((mod_name, spine_name, skill, cfg))
    return out


_INCLUDED = _included_skill_cases()


# --- registry structure ------------------------------------------------------


def test_registry_parses():
    assert _REGISTRY.get("modules"), "modules.yaml should load with a non-empty `modules:` key"


def test_registry_has_the_expected_modules():
    """`checkout` is the worked example; `browse` is registered with `spines: {}` to
    keep the metric-layer-only shape covered and to give a future spine (plus its
    handoff edge to checkout/session) somewhere to land."""
    assert set(_REGISTRY["modules"]) >= {"checkout", "browse"}


@pytest.mark.parametrize("mod_name,mod", _MODULES, ids=_MODULE_IDS)
def test_exactly_one_default_spine_when_nonempty(mod_name, mod):
    spines = mod.get("spines") or {}
    if not spines:
        return  # `spines: {}` is a valid, deliberate shape for a metric-only module
    defaults = [name for name, cfg in spines.items() if cfg.get("default")]
    assert len(defaults) == 1, (
        f"{mod_name}: expected exactly one default spine, got {defaults}"
    )


@pytest.mark.parametrize("mod_name,mod", _MODULES, ids=_MODULE_IDS)
def test_module_has_a_label(mod_name, mod):
    assert isinstance(mod.get("label"), str) and mod["label"].strip(), (
        f"{mod_name}: needs a human-readable `label`"
    )


# --- per-spine spec integrity ------------------------------------------------


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_spec_file_exists_and_parses(mod_name, spine_name, cfg):
    p = CONFIG_DIR / cfg["spec"]
    assert p.exists(), f"{mod_name}/{spine_name}: spec file {p} does not exist"
    spec = _load_yaml(p)
    assert isinstance(spec, dict) and spec, (
        f"{mod_name}/{spine_name}: {p} did not parse to a non-empty mapping"
    )


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_core_required_fields_present(mod_name, spine_name, cfg):
    spec = _load_yaml(CONFIG_DIR / cfg["spec"])
    missing = [f for f in CORE_REQUIRED_SPEC_FIELDS if f not in spec]
    assert not missing, (
        f"{mod_name}/{spine_name} ({cfg['spec']}) missing required fields: {missing}"
    )


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_stage_bearing_specs_declare_transitions_and_terminal_branches(mod_name, spine_name, cfg):
    """A spec with a stage ladder but no timing transitions or terminal branches is a
    half-authored funnel contract: it would let a drop-off skill in and leave a
    timing skill with nothing to measure."""
    spec = _load_yaml(CONFIG_DIR / cfg["spec"])
    if "stages" not in spec:
        return
    assert "transitions" in spec, (
        f"{mod_name}/{spine_name}: declares `stages` but no `transitions`"
    )
    assert "terminal_branches" in spec, (
        f"{mod_name}/{spine_name}: declares `stages` but no `terminal_branches`"
    )


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_stage_ladder_is_ordered_without_gaps(mod_name, spine_name, cfg):
    spec = _load_yaml(CONFIG_DIR / cfg["spec"])
    stages = spec.get("stages") or []
    if not stages:
        return
    ordinals = [s.get("ordinal") for s in stages]
    assert ordinals == list(range(1, len(stages) + 1)), (
        f"{mod_name}/{spine_name}: stage ordinals are {ordinals}, expected "
        f"1..{len(stages)} in order — a drop-off curve reads them positionally"
    )
    keys = [s.get("key") for s in stages]
    assert len(set(keys)) == len(keys), f"{mod_name}/{spine_name}: duplicate stage keys"


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_primary_entity_matches_the_registry(mod_name, spine_name, cfg):
    spec = _load_yaml(CONFIG_DIR / cfg["spec"])
    assert spec.get("primary_entity") == cfg.get("primary_entity"), (
        f"{mod_name}/{spine_name}: registry says primary_entity "
        f"{cfg.get('primary_entity')!r} but the spec says {spec.get('primary_entity')!r}"
    )


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_allowed_dimensions_and_filters_are_free_of_pii(mod_name, spine_name, cfg):
    from _shared import pii

    spec = _load_yaml(CONFIG_DIR / cfg["spec"])
    cols = list(spec.get("allowed_dimensions") or []) + list(spec.get("allowed_filters") or [])
    offenders = [(c, pii.pii_reason(c)) for c in cols if pii.is_pii_column(c)]
    assert not offenders, (
        f"{mod_name}/{spine_name}: spec allow-lists PII columns: {offenders}"
    )


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_cohort_column_is_not_an_equality_filter(mod_name, spine_name, cfg):
    """The cohort column is the WINDOW control (`BETWEEN @start AND @end`). Listing it
    in `allowed_dimensions` would invite grouping a weekly view by its own week, and
    reads as though a caller could pin a single cohort by equality."""
    spec = _load_yaml(CONFIG_DIR / cfg["spec"])
    cohort = spec.get("cohort_column")
    assert cohort not in (spec.get("allowed_dimensions") or []), (
        f"{mod_name}/{spine_name}: cohort column {cohort!r} is in allowed_dimensions"
    )


# --- skills / excluded: mandatory, not optional ------------------------------


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_every_skill_is_included_xor_excluded(mod_name, spine_name, cfg):
    included = set(cfg.get("skills") or [])
    excluded = set((cfg.get("excluded") or {}).keys())
    overlap = included & excluded
    missing = ALL_SKILLS - included - excluded
    unknown = (included | excluded) - ALL_SKILLS
    assert not overlap, f"{mod_name}/{spine_name}: in both skills and excluded: {sorted(overlap)}"
    assert not missing, (
        f"{mod_name}/{spine_name}: missing from skills+excluded: {sorted(missing)}"
    )
    assert not unknown, f"{mod_name}/{spine_name}: unknown skill name(s): {sorted(unknown)}"


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_excluded_entries_carry_a_substantive_reason(mod_name, spine_name, cfg):
    """"TODO" is not a reason. An exclusion has to say enough that a reader can tell
    whether it is a grain impossibility or an unbuilt skill — those need completely
    different responses."""
    for skill, reason in (cfg.get("excluded") or {}).items():
        assert isinstance(reason, str) and len(reason.strip()) >= 40, (
            f"{mod_name}/{spine_name}: excluded[{skill!r}] needs a substantive "
            f"reason, got {reason!r}"
        )
        assert "todo" not in reason.lower(), (
            f"{mod_name}/{spine_name}: excluded[{skill!r}] says TODO rather than why"
        )


def test_included_skills_are_not_a_false_promise():
    """A skill listed as included must have what it reads, or the registry is
    advertising a capability that crashes.

    Written as a loop over possibly-zero cases rather than a parametrize, because an
    empty parametrize produces a SKIPPED test — and a governance check that reports
    itself as skipped is indistinguishable from one that was never written. No
    spine-backed skill is included yet, so this currently passes vacuously and starts
    enforcing the moment one is flipped on.
    """
    problems = []
    for mod_name, spine_name, skill, cfg in _INCLUDED:
        spec = _load_yaml(CONFIG_DIR / cfg["spec"])
        missing = [f for f in SKILL_REQUIRED_SPEC_FIELDS.get(skill, []) if f not in spec]
        if missing:
            problems.append(f"{mod_name}/{spine_name}: {skill!r} included but spec lacks {missing}")
    assert not problems, problems


def test_included_skills_stages_are_maturity_covered():
    """Mirrors `spine.resolve_stage_cutoffs()`'s hard-refusal condition — every
    stage/transition's `maturity_stage` needs a lag in `stage_maturity.lag_days`
    whenever `defaults.mature_only` is on — checked at lint time instead of only as a
    runtime refusal. Same zero-case reasoning as above."""
    problems = []
    for mod_name, spine_name, skill, cfg in _INCLUDED:
        spec = _load_yaml(CONFIG_DIR / cfg["spec"])
        if spec.get("defaults", {}).get("mature_only", True) is False:
            continue
        stage_defs = (spec.get("stages") or []) + (spec.get("transitions") or [])
        for sd in stage_defs:
            key = sd.get("key")
            mstage = sd.get("maturity_stage", key)
            if spine.stage_lag_days(spec, mstage) is None:
                problems.append(
                    f"{mod_name}/{spine_name}: {skill!r} included but stage {key} "
                    f"(maturity_stage={mstage!r}) has no lag"
                )
    assert not problems, problems


def test_every_spec_stage_and_transition_has_a_lag():
    """The same coverage check, but over EVERY registered spine rather than only the
    spines with an included skill — so the spec stays runnable before any skill is
    wired to it. This is what would catch a stage added without a lag during Phase 3
    development, rather than at the moment somebody first runs the skill."""
    problems = []
    for mod_name, spine_name, cfg in _SPINES:
        spec = _load_yaml(CONFIG_DIR / cfg["spec"])
        if spec.get("defaults", {}).get("mature_only", True) is False:
            continue
        for sd in (spec.get("stages") or []) + (spec.get("transitions") or []):
            key = sd.get("key")
            mstage = sd.get("maturity_stage", key)
            if spine.stage_lag_days(spec, mstage) is None:
                problems.append(f"{mod_name}/{spine_name}: {key} -> {mstage!r} has no lag")
    assert not problems, problems


# --- handoff (declarative cross-spine composition) ---------------------------


def test_handoff_when_present_is_well_formed():
    """No spine declares a handoff yet — `browse` is registered with `spines: {}`
    precisely so a future browse-session spine has somewhere to declare one. Written
    as a loop so this reports as a pass rather than a skip."""
    problems = []
    for mod_name, spine_name, cfg in _SPINES:
        handoff = cfg.get("handoff")
        if handoff is None:
            continue
        if not handoff.get("to") or "/" not in handoff["to"]:
            problems.append(f"{mod_name}/{spine_name}: handoff.to should be 'module/spine'")
            continue
        if not handoff.get("on"):
            problems.append(f"{mod_name}/{spine_name}: handoff missing `on`")
        target_mod, target_spine = handoff["to"].split("/", 1)
        if target_mod not in _REGISTRY["modules"]:
            problems.append(f"{mod_name}/{spine_name}: handoff names unknown module {target_mod!r}")
        elif target_spine not in (_REGISTRY["modules"][target_mod].get("spines") or {}):
            problems.append(f"{mod_name}/{spine_name}: handoff names unknown spine {handoff['to']!r}")
    assert not problems, problems


# --- the runtime resolver reads the same file this linter validates ----------


@pytest.mark.parametrize("mod_name,spine_name,cfg", _SPINES, ids=_SPINE_IDS)
def test_resolve_spine_matches_registry_entry(mod_name, spine_name, cfg):
    assert spine.resolve_spine(mod_name, spine_name, registry=_REGISTRY) == cfg


def test_resolve_spine_refuses_unknown_module():
    with pytest.raises(SystemExit):
        spine.resolve_spine("no_such_module", None, registry=_REGISTRY)


def test_resolve_spine_refuses_unknown_spine():
    with pytest.raises(SystemExit):
        spine.resolve_spine("checkout", "no_such_spine", registry=_REGISTRY)


def test_spine_for_entity_maps_back():
    assert spine.spine_for_entity("checkout", "checkout_session", registry=_REGISTRY) == "session"
    assert spine.spine_for_entity("checkout", "not_an_entity", registry=_REGISTRY) is None
    assert spine.spine_for_entity("checkout", None, registry=_REGISTRY) is None


def test_default_spec_path_resolves_to_the_checkout_funnel_spec():
    """The backward-compatibility guarantee every zero-argument caller depends on: a
    bare `spec_path()` must resolve to the default module's default spine."""
    import os

    assert "ASSAY_FUNNEL_SPEC" not in os.environ, (
        "ASSAY_FUNNEL_SPEC is set in this environment; this test cannot validate the "
        "true default and would give a false result either way"
    )
    assert spine.spec_path().name == "checkout-funnel-spec.yaml"
    assert spine.spec_path() == spine.spec_path(module="checkout", spine=None)
    assert spine.DEFAULT_MODULE == "checkout"


def test_funnel_spec_env_override_wins():
    """`ASSAY_FUNNEL_SPEC` beats the registry outright — the seam the maturity tests
    use to run against a synthetic spec instead of the live one."""
    import os

    original = os.environ.get("ASSAY_FUNNEL_SPEC")
    try:
        os.environ["ASSAY_FUNNEL_SPEC"] = "/tmp/override-spec.yaml"
        assert spine.spec_path() == Path("/tmp/override-spec.yaml")
    finally:
        if original is None:
            os.environ.pop("ASSAY_FUNNEL_SPEC", None)
        else:
            os.environ["ASSAY_FUNNEL_SPEC"] = original
