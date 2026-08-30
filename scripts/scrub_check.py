#!/usr/bin/env python3
"""scrub_check.py — the deny-token gate for this public template.

`kattfot` is the sanitized, public-ready form of a private analytics repo. The
governance engine, hook matchers, and skill prose were ported across; the
warehouse identifiers, carrier names, internal project keys, and contact
addresses were not. This script is the mechanical check that they stay out:
it walks every tracked file, matches a deny list case-insensitively, prints
each hit as `path:line:token`, and exits non-zero if there is even one.

Run it before every commit, and wire it into CI once there is a workflow:

    python3 scripts/scrub_check.py          # report hits, exit 1 if any
    python3 scripts/scrub_check.py --quiet  # exit code only

Standard library only, so it runs anywhere Python 3.9+ does with no install.

Two deliberate limitations, both documented rather than papered over:

  - **This file is exempt from its own scan.** It necessarily spells every
    token out. Nothing else is exempt — not even the vendored assets — so a
    leak anywhere in the tree is a failure.
  - **Matching is line-by-line.** A token split across a line break is not
    caught. Tokens are short and prose wraps between words, so this is a
    reasonable trade for output that can name a line number.

The tokens are a blocklist, not a sandbox: it catches the known-bad names, not
every conceivable way to describe a private system. Human review of new prose
is still the durable control.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# This file is the one place the tokens are written out in full, so it cannot
# scan itself. Paths are repo-relative, POSIX-style.
SELF_EXEMPT = {"scripts/scrub_check.py"}

# Directories never worth walking in the filesystem-fallback mode (the
# git-tracked path already excludes them via .gitignore).
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".pytest_cache", "node_modules", "outputs"}

# Every pattern is matched case-insensitively against each line. Grouped by
# what it protects against, so a reviewer adding a token knows where it goes.
DENY_TOKENS: tuple[str, ...] = (
    # Company, repo, and org names.
    r"bestow",
    r"bstw",
    r"\bBEAR\b",
    r"bear_[a-z]",
    r"bestowinc",
    # Carrier / partner names.
    r"tlic",
    r"transamerica",
    r"\busaa\b",
    r"\bnwla\b",
    r"nationwide",
    r"nwlepro",
    r"\blepro\b",
    r"sammons",
    r"equitable",
    r"\beflic\b",
    # Warehouse datasets and identifiers.
    r"mds_reporting",
    r"mds_staging",
    r"\bmds\.",
    r"analytics_staging",
    r"crs_id",
    r"ssn_token",
    # Internal vendors / systems / project codenames.
    r"pin2sign",
    r"socure",
    r"milliman",
    r"ryerson",
    r"protoss",
    r"six\s*eyes",
    # Domain vocabulary specific to the private original.
    r"freelook",
    # Internal issue-tracker project keys.
    r"MODAPPCHK",
    r"MODPRESALE",
    r"DPS-\d",
    r"BPS-\d",
    r"ACO-\d",
    r"APC-\d",
    # Internal tooling / sibling plugins / contacts.
    r"atlassian",
    r"\bBOSS\b",
    r"intelligence\s+hub",
    r"mark\.reid@",
    r"engineering@bestow",
    r"28f5a9ff-e1ce-47a9-b683-f6a6e9212ae9",
)

_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (token, re.compile(token, re.IGNORECASE)) for token in DENY_TOKENS
)


class Hit:
    """One deny-token match: where it is, and which token fired."""

    __slots__ = ("path", "line", "token", "text")

    def __init__(self, path: str, line: int, token: str, text: str):
        self.path, self.line, self.token, self.text = path, line, token, text

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.token}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Hit({self!s})"


def scan_text(text: str, path: str = "<text>") -> list[Hit]:
    """Every deny-token hit in `text`, in file order then deny-list order."""
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for token, rx in _COMPILED:
            if rx.search(line):
                hits.append(Hit(path, lineno, token, line.strip()))
    return hits


def scan_file(path: Path, *, rel: str | None = None) -> list[Hit]:
    """Scan one file. Unreadable or non-UTF-8 (binary) files yield no hits."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return scan_text(text, rel or path.name)


def tracked_files(root: Path) -> list[Path]:
    """Git-tracked files, or a filesystem walk when git can't answer.

    Pre-commit (a fresh `git init` with nothing added yet) `git ls-files` is
    empty, which would make the gate pass vacuously. Falling back to a walk is
    what makes this usable as the very first check on a new repo.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode == 0:
            names = [n for n in out.stdout.split("\0") if n]
            if names:
                return [root / n for n in names]
    except (OSError, subprocess.SubprocessError):
        pass

    found = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        found.append(p)
    return found


def scan_repo(root: Path | None = None) -> list[Hit]:
    """Scan every tracked file under `root`, skipping this script itself."""
    root = root or REPO_ROOT
    hits = []
    for path in tracked_files(root):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = str(path)
        if rel in SELF_EXEMPT or not path.is_file():
            continue
        hits.extend(scan_file(path, rel=rel))
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=None, help="repo root to scan (default: this repo)")
    ap.add_argument("--quiet", action="store_true", help="suppress output; exit code only")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else REPO_ROOT
    hits = scan_repo(root)

    if not args.quiet:
        for hit in hits:
            print(hit)
        sys.stdout.flush()  # keep the hit list above the summary when stderr is merged
        n_files = len({h.path for h in hits})
        if hits:
            print(
                f"\nFAIL: {len(hits)} deny-token hit(s) in {n_files} file(s). "
                f"Scrub them before committing.",
                file=sys.stderr,
            )
        else:
            print(f"CLEAN: no deny tokens found ({len(DENY_TOKENS)} tokens checked).")

    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
