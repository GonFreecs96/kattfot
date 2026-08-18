"""The scrub gate: does it actually catch a leak, and does it stay quiet when clean?

`scripts/scrub_check.py` is what keeps this public template free of the private
original's names. A blocklist that silently stopped matching would be worse than
no blocklist at all — it would read as a passing gate — so the gate itself is
tested here, both directions.

**Note the string arithmetic.** Every deny token is assembled at runtime
(`"be" + "stow"`) instead of written literally — in the fixtures *and* in the
expected values. `scrub_check.py` exempts exactly one path from its own scan —
itself — so a test that spelled a token out would be found by the very gate it is
testing, and `pytest` and the scrub would disagree about whether the repo is
clean. Assembling the strings keeps this file honestly clean while still
exercising real patterns. Please keep it that way.
"""

from pathlib import Path

import pytest

import scrub_check

REPO_ROOT = Path(__file__).resolve().parents[1]

# Assembled, never written literally — see the module docstring. Each of these is
# both a string that must be caught and (for the first two) the exact regex the
# deny list stores, so they double as the expected `Hit.token`.
COMPANY_TOKEN = "be" + "stow"
WAREHOUSE_TOKEN = "cr" + "s_id"
CONTACT_FIXTURE = "mark" + "." + "reid" + "@" + "example.com"
REPO_CODENAME = r"\b" + "BE" + "AR" + r"\b"
TOOLING_TOKEN = "atlas" + "sian"


# --- scan_text: the matching primitive --------------------------------------

def test_clean_text_has_no_hits():
    text = "channel,payment_method,market\norganic_search,card,us\n"
    assert scrub_check.scan_text(text) == []


def test_planted_token_is_caught():
    hits = scrub_check.scan_text(f"a clean line\nthis mentions {COMPANY_TOKEN} here\n")
    assert len(hits) == 1
    assert hits[0].line == 2, "must report the line the token is on"
    assert hits[0].token == COMPANY_TOKEN


def test_matching_is_case_insensitive():
    for variant in (COMPANY_TOKEN.upper(), COMPANY_TOKEN.capitalize()):
        assert scrub_check.scan_text(variant), f"{variant!r} should be caught"


def test_hit_str_is_path_line_token():
    hits = scrub_check.scan_text(f"{COMPANY_TOKEN}\n", path="some/file.py")
    assert str(hits[0]) == f"some/file.py:1:{COMPANY_TOKEN}"


def test_multiple_tokens_on_one_line_all_report():
    hits = scrub_check.scan_text(f"{COMPANY_TOKEN} and {WAREHOUSE_TOKEN}\n")
    assert {h.token for h in hits} == {COMPANY_TOKEN, WAREHOUSE_TOKEN}


def test_word_boundary_tokens_do_not_overmatch():
    """Tokens anchored with \\b must not fire on innocent substrings — otherwise
    the gate cries wolf and gets switched off."""
    assert scrub_check.scan_text("the bearing surface was measured\n") == []
    assert scrub_check.scan_text("he grew a beard over the winter\n") == []


# --- scan_repo / main: the gate ---------------------------------------------

def _tree(root: Path, files: dict) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_scan_repo_catches_planted_file(tmp_path):
    _tree(tmp_path, {
        "clean.py": "x = 1\n",
        "docs/leak.md": f"# notes\n\nthe {COMPANY_TOKEN} warehouse\n",
    })
    hits = scrub_check.scan_repo(tmp_path)
    assert [str(h) for h in hits] == [f"docs/leak.md:3:{COMPANY_TOKEN}"]


def test_scan_repo_clean_tree_has_no_hits(tmp_path):
    _tree(tmp_path, {
        "a.py": "channel = 'organic_search'\n",
        "b/c.md": "# Checkout funnel\n\nCart Created -> Order Confirmed.\n",
    })
    assert scrub_check.scan_repo(tmp_path) == []


def test_main_exits_nonzero_on_hit(tmp_path, capsys):
    _tree(tmp_path, {"leak.txt": f"{CONTACT_FIXTURE}\n"})
    code = scrub_check.main(["--root", str(tmp_path)])
    assert code == 1
    out = capsys.readouterr()
    assert "leak.txt:1:" in out.out
    assert "FAIL" in out.err


def test_main_exits_zero_on_clean_tree(tmp_path, capsys):
    _tree(tmp_path, {"ok.txt": "nothing to see\n"})
    code = scrub_check.main(["--root", str(tmp_path)])
    assert code == 0
    assert "CLEAN" in capsys.readouterr().out


def test_quiet_mode_is_exit_code_only(tmp_path, capsys):
    _tree(tmp_path, {"leak.txt": f"{COMPANY_TOKEN}\n"})
    assert scrub_check.main(["--root", str(tmp_path), "--quiet"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_binary_file_is_skipped_not_fatal(tmp_path):
    """A non-UTF-8 file must not crash the walk (vendored assets get scanned too)."""
    (tmp_path / "blob.bin").write_bytes(b"\x00\xff\xfe not text")
    assert scrub_check.scan_repo(tmp_path) == []


# --- the gate applied to this repo -----------------------------------------

def test_scrub_check_exempts_only_itself():
    assert scrub_check.SELF_EXEMPT == {"scripts/scrub_check.py"}


def test_this_repo_is_clean():
    """The gate, as a test. `pytest` green and the scrub clean become the same
    statement, so a leak cannot be introduced without a red test."""
    hits = scrub_check.scan_repo(REPO_ROOT)
    assert not hits, "deny tokens found:\n" + "\n".join(str(h) for h in hits)


@pytest.mark.parametrize(
    "token", [COMPANY_TOKEN, WAREHOUSE_TOKEN, REPO_CODENAME, TOOLING_TOKEN]
)
def test_deny_list_still_contains_key_tokens(token):
    """Guard against a well-meaning edit quietly shrinking the list."""
    assert token in scrub_check.DENY_TOKENS
