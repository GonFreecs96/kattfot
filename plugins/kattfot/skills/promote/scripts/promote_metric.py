#!/usr/bin/env python3
"""promote_metric.py — scaffold a governed metric draft from an explore artifact.

The closing of the exploratory loop. The tier doctrine has always said "a
metric that matters enough to be asked twice should be promoted to T2 with a
reviewed definition"; this helper does the mechanical half of that promotion:
it takes an exploratory (T3) artifact, checks the artifact has not been
tampered with, and emits a **draft** metric definition into the consumer
metrics directory — never the bundled catalog — pre-filled with everything
derivable from the artifact and `NEEDS-DECISION` everywhere judgment is
required.

The draft is deliberately inert: it ships with `accuracy_tier: T3` and
`status: draft`, so `kattfot:query-metric` refuses it (R4) until a human
reviews the definition, fills the owner, and edits the tier to T2. **The
existing R4 gate IS the review gate** — promotion adds no bypass, it adds a
paper trail: the draft's `promoted_from` block pins the question, the exact
query fingerprint, and the register version that authorized the original
read.

What is derivable and what is not:
  * a single-surface `SAFE_DIVIDE(SUM(x), SUM(y))` shape pre-fills the view,
    numerator/denominator, time column, and allow-lists (from GROUP BY /
    filter parameters) — the shape the governed runtime serves.
  * anything else (percentiles, windows, joins, row-level output) is
    scaffolded with placeholders and an honest note: the metric runtime
    serves SUM/SUM ratios; a different shape is a spine skill or a new
    curated view, not a metric definition.

Exit codes: 0 success; 5 governance refusal (not an explore artifact, or the
tamper check failed); 1 bad invocation / unparseable artifact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _shared import exploration as exploration_lib  # noqa: E402
from _shared import manifest as manifest_lib  # noqa: E402
from _shared import sqlguard  # noqa: E402
from _shared.pii import is_pii_column  # noqa: E402

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "query-metric" / "scripts")
)
import kattfot_query as aq  # noqa: E402

try:
    import yaml
except ImportError:
    sys.stderr.write("Missing dependency pyyaml. Install with:\n  pip install pyyaml\n")
    sys.exit(3)

REFUSAL_EXIT = 5

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)
SQL_FENCE_RE = re.compile(r"```sql\s*\n(.*?)```", re.DOTALL)
JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

#: The SUM/SUM ratio shape the governed metric runtime serves. Lexical, like
#: everything in the exploratory lane: it recognizes the canonical form the
#: explore SKILL.md teaches, not every algebraic equivalent.
RATIO_RE = re.compile(
    r"SAFE_DIVIDE\s*\(\s*SUM\s*\(\s*(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\s*\)\s*,"
    r"\s*SUM\s*\(\s*(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\s*\)\s*\)",
    re.IGNORECASE,
)
GROUP_BY_RE = re.compile(
    r"\bGROUP\s+BY\s+(.+?)(?:\bORDER\b|\bHAVING\b|\bLIMIT\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)
#: Columns compared against a bound parameter — the filter columns the draft
#: should allow-list.
PARAM_FILTER_RE = re.compile(r"([A-Za-z_]\w*)\s*(?:=|!=|<>|>=|<=|>|<|\bIN\b)\s*@\w+", re.IGNORECASE)

#: Group columns that read as a time axis rather than a dimension.
TIME_COL_RE = re.compile(r"(week|date|month|day)", re.IGNORECASE)

NEEDS = "NEEDS-DECISION"


def refuse(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(REFUSAL_EXIT)


def parse_artifact(path: Path) -> tuple[dict, str, dict]:
    """Frontmatter, SQL, bound params — from exactly the fences the explore
    SKILL.md's Output format specifies."""
    if not path.exists():
        raise SystemExit(f"ERROR: artifact not found at {path}.")
    text = path.read_text()
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit(f"ERROR: {path} has no YAML frontmatter — not an artifact.")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"ERROR: unparseable frontmatter in {path}: {e}")
    body = m.group(2)
    sql_m = SQL_FENCE_RE.search(body)
    if not sql_m:
        raise SystemExit(
            f"ERROR: no ```sql fence found in {path} — the Provenance section "
            f"is required for promotion (it is the tamper-check input)."
        )
    params: dict = {}
    json_m = JSON_FENCE_RE.search(body, sql_m.end())
    if json_m:
        try:
            params = json.loads(json_m.group(1)) or {}
        except json.JSONDecodeError as e:
            raise SystemExit(f"ERROR: unparseable bound-parameters JSON in {path}: {e}")
    return fm, sql_m.group(1).strip(), params


def verify_provenance(fm: dict, sql: str, params: dict, artifact: Path) -> list:
    """The tamper check, plus register drift as a warning.

    Refuses when the artifact's SQL+params no longer hash to its manifest's
    `query_sha` — a promoted draft must descend from the query that actually
    ran, not from an edited transcript. Register drift (the allow-list changed
    since the exploration) is a warning: the draft is still reviewable, the
    reviewer just needs to know the authorizing register moved.
    """
    if fm.get("skill") != "explore" or fm.get("accuracy_tier") != "T3":
        refuse(
            f"ERROR (PROMOTE-INPUT): {artifact} is not an exploratory (T3) "
            f"artifact (skill={fm.get('skill')!r}, "
            f"tier={fm.get('accuracy_tier')!r}). Promotion starts from "
            f"kattfot:explore output; a governed artifact is already governed."
        )
    manifest = fm.get("manifest") or {}
    expected = manifest.get("query_sha")
    actual = manifest_lib.query_sha(sql, params)
    if not expected or actual != expected:
        refuse(
            f"ERROR (PROMOTE-TAMPER): the artifact's SQL and parameters hash "
            f"to query_sha {actual}, but its manifest says "
            f"{expected or '(absent)'}. The Provenance section no longer "
            f"matches the query that ran — refusing to scaffold a governed "
            f"draft from an edited transcript. Re-run kattfot:explore instead."
        )
    warnings = []
    recorded = manifest.get("register_sha")
    try:
        current = exploration_lib.register_sha()
    except Exception:
        current = None
    if recorded and current and recorded != current:
        warnings.append(
            f"REGISTER DRIFT: the exploration register has changed since this "
            f"artifact was produced (register_sha {recorded} -> {current}). "
            f"The draft is still reviewable; confirm its surface is still "
            f"registered."
        )
    return warnings


def analyze_shape(sql: str, surfaces: list) -> dict:
    """Everything derivable from the SQL, or placeholders where it is not.

    Returns the frontmatter-shaped fields plus `prefilled` (bool) and
    `needs_decision` (the fields a human must settle).
    """
    out = {
        "source_view": surfaces[0] if len(surfaces) == 1 else NEEDS,
        "numerator_column": NEEDS,
        "denominator_column": NEEDS,
        "time_column": NEEDS,
        "allowed_dimensions": [],
        "allowed_filters": [],
        "prefilled": False,
        "shape_note": None,
    }
    ratio = RATIO_RE.search(sql)
    group_cols: list = []
    gm = GROUP_BY_RE.search(sqlguard.strip_noise(sql))
    if gm:
        group_cols = [
            c for c in re.findall(r"[A-Za-z_]\w*", gm.group(1)) if not is_pii_column(c)
        ]
    filter_cols = [c for c in PARAM_FILTER_RE.findall(sql) if not is_pii_column(c)]

    if ratio and len(surfaces) == 1:
        out["numerator_column"] = ratio.group(1)
        out["denominator_column"] = ratio.group(2)
        time_cols = [c for c in group_cols if TIME_COL_RE.search(c)]
        if time_cols:
            out["time_column"] = time_cols[0]
        dims = [c for c in group_cols if c != out["time_column"]]
        out["allowed_dimensions"] = dims
        out["allowed_filters"] = sorted(
            set(([out["time_column"]] if out["time_column"] != NEEDS else []) + dims + filter_cols)
        )
        out["prefilled"] = True
    else:
        if not ratio:
            out["shape_note"] = (
                "The exploratory SQL is not a SAFE_DIVIDE(SUM(x), SUM(y)) "
                "ratio — the one shape the governed metric runtime serves. "
                "If the question is a percentile, a terminal-branch share, or "
                "a join, the promotion target is a spine skill or a new "
                "curated view, not a metric definition."
            )
        elif len(surfaces) != 1:
            out["shape_note"] = (
                f"The exploratory SQL reads {len(surfaces)} surfaces; a metric "
                f"definition names exactly one source_view. Materialize the "
                f"join as a curated view first, then promote against it."
            )
        out["allowed_dimensions"] = [c for c in group_cols if not TIME_COL_RE.search(c)]
        out["allowed_filters"] = sorted(set(group_cols + filter_cols))
    needs = [
        k
        for k in ("source_view", "numerator_column", "denominator_column", "time_column")
        if out[k] == NEEDS
    ]
    out["needs_decision"] = ["owner", "entity", "maturity_stage", "accuracy_tier"] + needs
    return out


def compose_draft(
    name: str, fm: dict, sql: str, shape: dict, artifact: Path, warnings: list
) -> str:
    question = fm.get("question") or "(question not recorded)"
    manifest = fm.get("manifest") or {}
    display = name.replace("_", " ").title()
    module = None
    try:
        register = exploration_lib.load_register()
        entry = exploration_lib.surface(register, shape["source_view"]) or {}
        module = entry.get("module")
    except SystemExit:
        pass  # register missing: the draft still scaffolds, module stays open

    front = {
        "name": name,
        "display_name": f"{display} (Draft)",
        "owner": "NEEDS-OWNER",
        "module": module or NEEDS,
        "entity": NEEDS,
        # T3 + draft on purpose: query-metric refuses this (R4) until a human
        # review edits the tier to T2. Promotion scaffolds; it never serves.
        "accuracy_tier": "T3",
        "source_view": shape["source_view"],
        "source_project": "${KATTFOT_BIGQUERY_PROJECT}",
        "time_grain": "week" if shape["time_column"] != NEEDS else NEEDS,
        "time_column": shape["time_column"],
        "time_column_type": "DATE",
        "numerator_column": shape["numerator_column"],
        "denominator_column": shape["denominator_column"],
        "value_format": "percent" if shape["prefilled"] else NEEDS,
        "allowed_dimensions": shape["allowed_dimensions"],
        "allowed_filters": shape["allowed_filters"],
        "default_lookback_weeks": 8,
        "status": "draft",
        "promoted_from": {
            "question": question,
            "artifact": str(artifact),
            "query_sha": manifest.get("query_sha"),
            "register_sha": manifest.get("register_sha"),
            "promoted_at": dt.date.today().isoformat(),
        },
    }
    lines = ["---", yaml.safe_dump(front, sort_keys=False).rstrip(), "---", ""]
    lines += [
        f"# {display} (Draft)",
        "",
        "> **DRAFT — NOT SERVED.** Scaffolded by `kattfot:promote` from an",
        "> exploratory artifact. `accuracy_tier: T3` keeps the governed runtime",
        "> refusing this definition (R4) until a human review completes the",
        "> checklist below and edits the tier to T2.",
        "",
        "## Definition",
        "",
        f"{question}",
        "",
        "_(Rewrite as a definition, not a question: what the number claims,",
        "at what grain, over what population. See AUTHORING.md.)_",
        "",
    ]
    if shape["shape_note"]:
        lines += ["## Shape note", "", shape["shape_note"], ""]
    lines += [
        "## Numerator",
        "",
        f"`{shape['numerator_column']}` — describe what qualifies a row, and at what grain.",
        "",
        "## Denominator",
        "",
        f"`{shape['denominator_column']}` — describe the population, and why it is the honest base.",
        "",
        "## Caveats",
        "",
        "- **Draft from exploration.** The definition below descends from an",
        "  ad-hoc T3 query; nothing about it has been reviewed. The caveats a",
        "  reviewer adds here are reproduced verbatim in every artifact —",
        "  including the unflattering ones.",
        "",
        "## Promotion checklist",
        "",
        "- [ ] `owner:` — a named owner, not a team alias that resolves to nobody",
        "- [ ] `entity:` — the registered entity this measures (see `load-context entities`)",
        "- [ ] `maturity_stage:` — which stage settles this number; without it the",
        "      metric has NO maturity gate and reports immature cohorts as final",
        "- [ ] allow-lists reviewed: every dimension and filter is deliberate",
        "- [ ] caveats written, including the unflattering ones",
        "- [ ] definition prose rewritten (Definition / Numerator / Denominator)",
        "- [ ] `accuracy_tier: T2` and `status: live` — the LAST edit, after all",
        "      of the above; this is the review gate",
        "",
        "## Provenance",
        "",
        "The exploratory query this draft descends from:",
        "",
        "```sql",
        sql,
        "```",
        "",
    ]
    if warnings:
        lines += ["## Promotion warnings", ""] + [f"- {w}" for w in warnings] + [""]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--artifact", required=True, help="path to a kattfot:explore artifact (.md)")
    p.add_argument(
        "--name",
        required=True,
        help="metric name for the draft (snake_case, becomes the YAML `name:`)",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="where the draft lands (default: the consumer metrics dir, "
        "KATTFOT_METRICS_DIR — never the bundled catalog)",
    )
    p.add_argument("--force", action="store_true", help="overwrite an existing draft")
    args = p.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.name):
        raise SystemExit(
            f"ERROR: --name {args.name!r} must be snake_case ([a-z][a-z0-9_]*)."
        )

    artifact = Path(args.artifact)
    fm, sql, params = parse_artifact(artifact)
    warnings = verify_provenance(fm, sql, params, artifact)
    surfaces = list(fm.get("surfaces") or [])
    shape = analyze_shape(sql, surfaces)

    out_dir = Path(args.out_dir) if args.out_dir else aq.metrics_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.name}.md"
    if out_path.exists() and not args.force:
        raise SystemExit(
            f"ERROR: {out_path} already exists. Pass --force to overwrite, or "
            f"pick another --name."
        )
    out_path.write_text(compose_draft(args.name, fm, sql, shape, artifact, warnings))

    print(
        json.dumps(
            {
                "skill": "promote",
                "draft_path": str(out_path),
                "metric_name": args.name,
                "accuracy_tier": "T3",
                "status": "draft",
                "prefilled": shape["prefilled"],
                "needs_decision": shape["needs_decision"],
                "shape_note": shape["shape_note"],
                "warnings": warnings,
                "next": (
                    "Complete the Promotion checklist in the draft, then edit "
                    "accuracy_tier to T2 and status to live. Until then, "
                    "query-metric refuses it (R4) — that refusal is the "
                    "review gate working."
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
