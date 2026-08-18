"""Shared test setup.

Puts the plugin's skill packages on sys.path so tests can import the governance
code exactly as the skill runtime does:

  - `_shared.identity`       (per-user-identity / service-account refusal)
  - `_shared.pii`            (R6 PII denylist)
  - `_shared.manifest`       (content-addressed provenance receipts)
  - `_shared.reconcile`      (difference attribution)
  - `_shared.verify`         (offline definition/query drift)
  - `doctor`                 (setup skill's environment doctor)
  - `scrub_check`            (the repo's deny-token gate)

These are the modules that enforce governance in code. The agent layer enforces
the tier gate (R4) and the individual-level rules (R5); those are validated by
the catalog linter + agent prompts, not here.

Note what is deliberately absent: the query helper's `query-metric/scripts` dir,
which would put `assay_query` on the path. That skill is not in this template
yet, so anything needing it is either injected as a stub (see `test_verify.py`)
or deferred — no test here imports a module that does not exist.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "assay" / "skills"
SETUP_SCRIPTS = SKILLS / "setup" / "scripts"
REPO_SCRIPTS = ROOT / "scripts"

for _p in (str(SKILLS), str(SETUP_SCRIPTS), str(REPO_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
