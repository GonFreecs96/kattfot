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
  - `kattfot_query`        (the governed query helper)
  - `chart_spec`         (visualize-metric's spec builder)
  - `doctor`             (the setup skill's environment doctor)
  - `scrub_check`        (the repo's deny-token gate)
  - the nine skill helpers whose `main()` is the governance entry point:
    `funnel_dropoff`, `stage_timing`, `segment_compare`, `funnel_pulse`,
    `funnel_nowcast`, `catalog`, `load_context`, `coverage_lookup`, `reconcile`

Skill helpers are imported and their `main()` called with a patched `sys.argv`,
rather than shelled out to. Same entry point, same exit codes, and the sqlite
adapter's in-memory fixture database is built once for the whole session instead of
once per subprocess — which is the difference between a fast suite and a slow one
now that a 22k-row spine is loaded.

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
SKILLS = ROOT / "plugins" / "kattfot" / "skills"
SETUP_SCRIPTS = SKILLS / "setup" / "scripts"
QUERY_SCRIPTS = SKILLS / "query-metric" / "scripts"
VISUALIZE_SCRIPTS = SKILLS / "visualize-metric" / "scripts"
REPO_SCRIPTS = ROOT / "scripts"

#: Skills whose helper is a governance entry point the suite drives directly.
HELPER_SKILLS = (
    "funnel-dropoff",
    "stage-timing",
    "segment-compare",
    "funnel-pulse",
    "funnel-nowcast",
    "catalog",
    "load-context",
    "coverage",
    "reconcile",
    "explore",
    "promote",
)

for _p in (
    str(SKILLS),
    str(SETUP_SCRIPTS),
    str(QUERY_SCRIPTS),
    str(VISUALIZE_SCRIPTS),
    str(REPO_SCRIPTS),
    *(str(SKILLS / _s / "scripts") for _s in HELPER_SKILLS),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)
