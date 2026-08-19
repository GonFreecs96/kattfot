"""PII linter on the curated-view DDL.

Defense in depth at the data layer. Every guard above this — the metric allow-lists,
the spine's dimension validation, the R6 denylist in the helper — is moot if the
curated view a governed query reads exposes a direct-PII column in the first place:
someone will eventually reach it another way.

Scans every DDL file the plugin ships and fails pytest on any identifier matching the
R6 denylist (this repo has no CI; a human must run pytest for this to catch anything).
Comments are stripped first so prose discussing PII does not trip it, which
matters because the DDL's comments are where the reasoning lives.
"""

import re
from pathlib import Path

import pytest

from _shared import pii

ROOT = Path(__file__).resolve().parents[1]
VIEWS_DIR = ROOT / "plugins" / "assay" / "skills" / "query-metric" / "views"

DDL_FILES = sorted(VIEWS_DIR.glob("*.sql"))
DDL_IDS = [p.name for p in DDL_FILES]


def _strip_comments(sql: str) -> str:
    """Remove `--` line comments and `/* */` blocks.

    Applied before every scan below. The DDL's comments are where the reasoning lives,
    and they name columns freely — including PII column names while explaining why they
    are absent, and backticked identifiers while explaining a cast. Scanning the raw
    text would make honest documentation trip the linter.
    """
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)


def _identifiers(sql: str) -> set:
    """Identifier-like tokens in the SQL proper."""
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _strip_comments(sql)))


def test_views_present():
    assert DDL_FILES, f"no view DDL found under {VIEWS_DIR}"


@pytest.mark.parametrize("ddl", DDL_FILES, ids=DDL_IDS)
def test_no_pii_columns_in_view_ddl(ddl):
    offenders = sorted(
        (tok, pii.pii_reason(tok))
        for tok in _identifiers(ddl.read_text())
        if pii.is_pii_column(tok)
    )
    assert not offenders, f"{ddl.name} references PII identifiers: {offenders}"


@pytest.mark.parametrize("ddl", DDL_FILES, ids=DDL_IDS)
def test_the_linter_is_actually_looking_at_something(ddl):
    """Guard against a vacuous pass. If the comment stripper ever swallowed the whole
    file — an unterminated block comment would do it — the PII scan above would pass on
    an empty token set and report a clean view that was never read."""
    tokens = _identifiers(ddl.read_text())
    assert len(tokens) > 25, (
        f"{ddl.name}: only {len(tokens)} identifiers survived comment stripping; the "
        f"PII scan may be passing vacuously"
    )
    assert "SELECT" in tokens


@pytest.mark.parametrize("ddl", DDL_FILES, ids=DDL_IDS)
def test_identity_is_carried_only_as_opaque_keys(ddl):
    """The positive form of the same rule: person linkage exists in these views, and it
    has to be tokenized. `customer_token` is explicitly allow-listed as non-PII in
    `_shared/pii.py`; a raw personal identifier in its place would pass a naive
    "no PII columns" scan only until someone named it honestly."""
    tokens = _identifiers(ddl.read_text())
    for suspicious in ("customer_id", "user_id", "person_id", "shopper_id"):
        assert suspicious not in tokens, (
            f"{ddl.name} carries {suspicious!r}. Use an opaque token "
            f"(`customer_token`) so linkage works without a re-identifiable key."
        )


def test_no_real_project_or_dataset_is_baked_into_the_ddl():
    """A public template must not ship a concrete warehouse identifier — it would be
    somebody's billing account, and quite possibly a private name. Both DDL files use
    `${WAREHOUSE_PROJECT}` / `${SOURCE_DATASET}` placeholders."""
    for ddl in DDL_FILES:
        refs = re.findall(r"`([^`]+)`", _strip_comments(ddl.read_text()))
        assert refs, f"{ddl.name}: no backticked table references found at all"
        for ref in refs:
            assert ref.startswith("${"), (
                f"{ddl.name}: backticked reference {ref!r} does not start with a "
                f"${{PLACEHOLDER}} — a concrete project or dataset id must not ship"
            )
