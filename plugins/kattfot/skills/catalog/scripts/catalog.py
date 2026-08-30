#!/usr/bin/env python3
"""catalog.py — enumerate what can actually be asked.

The front door: every registered metric, so a user knows which governed questions
exist before asking one. Two classes, because the runtime serves two shapes:

  * **ratio metrics** — `SUM(numerator) / SUM(denominator)` at a governed grain,
    served by `kattfot:query-metric`. One markdown file each, under
    `skills/query-metric/metrics/`.
  * **latency KPIs** — percentiles, served by `kattfot:stage-timing`. These live in
    a funnel spec's `kpis:` block rather than in the metrics directory, because a
    percentile is structurally not a ratio of two sums and cannot be expressed in
    a metric definition at all.

Both are **read off disk**, never from a list inside this file. A hardcoded
catalog is wrong the first time somebody adds a metric, and it is wrong silently —
the metric exists, works, and is invisible. The one thing a catalog must not do is
disagree with the runtime, so this walks exactly the directories the resolver
walks (consumer dir first, then the bundled one, consumer winning on a `name:`
collision) and reads exactly the spec files the module registry points at.

Honesty about what is queryable
-------------------------------
A KPI on a spine the registry marks `declared` — contract authored, no view
provisioned — is listed with `queryable: false` and its reason. Listing it as
available would be the catalog's version of the false promise the module registry
exists to prevent; hiding it would lose the fact that the contract is fixed.

Never queries anything. Pure file reads, so it works with no credentials and no
network.

Usage:
    python3 catalog.py                    # JSON
    python3 catalog.py --format markdown  # the tables the skill presents
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "query-metric" / "scripts"))
from _shared import spine  # noqa: E402

try:
    import yaml
except ImportError:
    sys.stderr.write("Missing dependency pyyaml. Install with:\n  pip install pyyaml\n")
    sys.exit(3)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[2].parent


def metric_roots() -> list:
    """Consumer dir first, then the bundled catalog — the resolver's own order."""
    import kattfot_query as aq

    return [aq.metrics_dir(), aq.bundled_metrics_dir()]


def first_definition_line(body: str) -> str:
    """The first sentence of `## Definition`, as the one-liner.

    Read from the file rather than duplicated in frontmatter so the catalog cannot
    describe a metric differently from the metric's own definition section.
    """
    lines = body.splitlines()
    inside = False
    for line in lines:
        if line.startswith("## "):
            inside = line.strip().lower() == "## definition"
            continue
        if inside and line.strip():
            return line.strip()
    return ""


def scan_metrics() -> list:
    """Every registered ratio metric, consumer overriding bundled on `name:`.

    Files with no `name:` (a `LINEAGE.md`) are documentation and are skipped —
    exactly what the resolver does, so the linter and the catalog cannot hold a
    file to a contract the runtime never applies to it.
    """
    seen: dict = {}
    for root in metric_roots():
        if not root or not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            m = FRONTMATTER_RE.match(path.read_text())
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            name = fm.get("name")
            if not name or name in seen:
                continue  # first root wins: the consumer override
            seen[name] = {
                "name": name,
                "display_name": fm.get("display_name", name),
                "definition": first_definition_line(m.group(2)),
                "accuracy_tier": fm.get("accuracy_tier"),
                "module": fm.get("module"),
                "entity": fm.get("entity"),
                "status": fm.get("status"),
                "value_format": fm.get("value_format"),
                "maturity_stage": fm.get("maturity_stage"),
                "owner": fm.get("owner"),
                "served_by": "kattfot:query-metric",
                "path": str(path.relative_to(_plugin_root().parent.parent))
                if str(path).startswith(str(_plugin_root().parent.parent)) else str(path),
            }
    return [seen[k] for k in sorted(seen)]


def scan_kpis(registry: dict) -> list:
    """Named latency KPIs from every registered spine's `kpis:` block."""
    out = []
    for mod_name, mod in (registry.get("modules") or {}).items():
        for spine_name, cfg in (mod.get("spines") or {}).items():
            spec_path = _plugin_root() / "config" / cfg["spec"]
            if not spec_path.exists():
                continue
            spec = yaml.safe_load(spec_path.read_text()) or {}
            transitions = {t["key"]: t for t in spec.get("transitions") or []}
            queryable = cfg.get("status") == "live"
            for kpi in spec.get("kpis") or []:
                t = transitions.get(kpi.get("transition")) or {}
                out.append({
                    "key": kpi.get("key"),
                    "display_name": kpi.get("display_name"),
                    "definition": (
                        f"percentiles of {t.get('label', kpi.get('transition'))} "
                        f"in {kpi.get('report_unit', t.get('unit'))}s, over the rows "
                        f"that reached both endpoints"
                    ),
                    "transition": kpi.get("transition"),
                    "percentiles": kpi.get("percentiles"),
                    "accuracy_tier": kpi.get("accuracy_tier"),
                    "module": mod_name,
                    "spine": spine_name,
                    "maturity_stage": kpi.get("maturity_stage"),
                    "owner": kpi.get("owner"),
                    "leak_guarded": kpi.get("leak_guarded"),
                    "served_by": "kattfot:stage-timing",
                    "queryable": queryable,
                    "not_queryable_because": None if queryable else (
                        f"the {mod_name}/{spine_name} spine is registered as "
                        f"'{cfg.get('status')}': its contract is authored and linted "
                        f"but no view is provisioned behind it"
                    ),
                })
    return out


def scan_spines(registry: dict) -> list:
    """Per-spine summary: which spine-backed skills are available where."""
    out = []
    for mod_name, mod in (registry.get("modules") or {}).items():
        for spine_name, cfg in (mod.get("spines") or {}).items():
            out.append({
                "module": mod_name,
                "module_label": mod.get("label"),
                "spine": spine_name,
                "primary_entity": cfg.get("primary_entity"),
                "status": cfg.get("status"),
                "default": bool(cfg.get("default")),
                "skills": list(cfg.get("skills") or []),
                "excluded": sorted((cfg.get("excluded") or {}).keys()),
                "handoff": cfg.get("handoff"),
            })
    return out


def build(registry: dict | None = None) -> dict:
    registry = registry if registry is not None else spine.load_registry()
    metrics = scan_metrics()
    kpis = scan_kpis(registry)
    by_module: dict = {}
    for m in metrics:
        by_module.setdefault(m.get("module") or "(unassigned)", []).append(m["name"])
    return {
        "skill": "catalog",
        "metric_count": len(metrics),
        "kpi_count": len(kpis),
        "metrics_by_module": by_module,
        "metrics": metrics,
        "latency_kpis": kpis,
        "spines": scan_spines(registry),
        "notes": [
            "Ratio metrics are served by kattfot:query-metric; latency KPIs by "
            "kattfot:stage-timing. The split is structural, not organisational — a "
            "percentile cannot be written as SUM/SUM.",
            "Read from disk on every invocation, so this cannot drift from what the "
            "runtime will actually resolve.",
            "Listing a metric is not a claim about the number: the tier, the "
            "caveats, and the maturity cutoff travel with the result, not with the "
            "catalog entry.",
        ],
    }


def render_markdown(cat: dict) -> str:
    lines = [
        "# kattfot catalog",
        "",
        f"{cat['metric_count']} governed ratio metrics · {cat['kpi_count']} latency KPIs",
        "",
        "## Ratio metrics (`kattfot:query-metric`)",
        "",
    ]
    for module in sorted(cat["metrics_by_module"]):
        lines += [f"### {module}", "", "| Metric | What it measures | Tier | Status |",
                  "|---|---|---|---|"]
        for m in cat["metrics"]:
            if (m.get("module") or "(unassigned)") != module:
                continue
            lines.append(
                f"| {m['display_name']} (`{m['name']}`) | {m['definition']} | "
                f"{m['accuracy_tier']} | {m['status']} |"
            )
        lines.append("")
    if cat["latency_kpis"]:
        lines += [
            "## Latency KPIs (`kattfot:stage-timing`)",
            "",
            "| KPI | What it measures | Percentiles | Tier | Queryable |",
            "|---|---|---|---|---|",
        ]
        for k in cat["latency_kpis"]:
            pcts = "/".join(f"p{p}" for p in (k.get("percentiles") or []))
            q = "yes" if k["queryable"] else f"no — {k['not_queryable_because']}"
            lines.append(
                f"| {k['display_name']} (`{k['key']}`) | {k['definition']} | {pcts} | "
                f"{k['accuracy_tier']} | {q} |"
            )
        lines.append("")
    lines += ["## Spines", "", "| Module / spine | Grain | Status | Spine-backed skills |",
              "|---|---|---|---|"]
    for s in cat["spines"]:
        skills = ", ".join(s["skills"]) or "_(none — see the registry's `excluded:` reasons)_"
        lines.append(
            f"| `{s['module']}/{s['spine']}` | {s['primary_entity']} | {s['status']} | {skills} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--format", default="json", choices=["json", "markdown"])
    args = ap.parse_args()
    cat = build()
    if args.format == "markdown":
        sys.stdout.write(render_markdown(cat))
    else:
        print(json.dumps(cat, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
