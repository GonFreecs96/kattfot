"""Shared test setup.

Puts the plugin's skill packages on `sys.path` so tests import the governance code
exactly as the skill runtime does:

  - `_shared.identity`   (per-user identity / service-account refusal)
  - `_shared.pii`        (R6 PII denylist)
  - `_shared.manifest`   (content-addressed provenance receipts)
  - `_shared.reconcile`  (difference attribution)
  - `_shared.verify`     (offline definition/query drift)
  - `_shared.spine`      (module registry, maturity, WHERE builder)
  - `_shared.dialect`    (the BigQuery -> sqlite translator)
  - `_shared.adapters`   (the warehouse adapter seam)
  - `assay_query`        (the governed query helper)
  - `chart_spec`         (visualize-metric's spec builder)
  - `doctor`             (the setup skill's environment doctor)
  - `scrub_check`        (the repo's deny-token gate)

`render.py` is deliberately absent from this list: it needs matplotlib, and the
suite is kept installable in seconds with no compiled dependencies. Its behavior is
covered where it matters — through `chart_spec`'s output contract, which is what
constrains what any renderer can draw.

Note the adapter import style. `_shared.adapters` keeps a process-level cache of
adapter instances, so it must be a single module object; the package uses relative
imports internally and everything here reaches it through the `_shared` package
name for the same reason. The stateless siblings (`identity`, `pii`) are reachable
either way, which is why some existing skill scripts import them bare.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "assay" / "skills"
SETUP_SCRIPTS = SKILLS / "setup" / "scripts"
QUERY_SCRIPTS = SKILLS / "query-metric" / "scripts"
VISUALIZE_SCRIPTS = SKILLS / "visualize-metric" / "scripts"
REPO_SCRIPTS = ROOT / "scripts"

for _p in (
    str(SKILLS),
    str(SETUP_SCRIPTS),
    str(QUERY_SCRIPTS),
    str(VISUALIZE_SCRIPTS),
    str(REPO_SCRIPTS),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)
