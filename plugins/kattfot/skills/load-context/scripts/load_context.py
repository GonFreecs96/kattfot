#!/usr/bin/env python3
"""load_context.py — the governed definitions read API, for other tools and plugins.

Returns metric definitions, the entity registry, tier and governance doctrine,
lineage notes, and the coverage register, addressed by **scope** rather than by
path. Pure file reads: this never touches a warehouse, and for a live number the
caller wants `kattfot:query-metric` instead.

The contract is `contracts/consumer-api.md`, and it is a contract rather than a
convention because of one requirement: **a consumer must be able to tell a governed
answer from a refusal without parsing prose.** Every failure mode is a single line
with a stable prefix:

    SCOPE_NOT_FOUND -- {detail}     nothing matched, including after a fuzzy attempt
    EMPTY_RESULT -- {detail}        the scope resolved but holds nothing
    PATH_BOUNDARY -- {detail}       the resolved path escaped the allowed read roots
    TIER_REFUSED -- {detail}        the metric is T1 or T3, not served by this API

`TIER_REFUSED` is the load-bearing one. The tier guarantee is worth nothing if it
survives inside this repo and evaporates at the interop boundary: a consumer that
receives a T1 (regulator-grade) or T3 (exploratory) body with no marking will
present it exactly like a governed T2 number. So the refusal happens here, in code,
in the same place the content would otherwise be returned — a prompt-level rule
would be bypassed by the first caller who invokes this script directly.

Exit codes, so a script can branch without parsing:
    0  content returned
    1  SCOPE_NOT_FOUND / EMPTY_RESULT — nothing to return, nothing wrong
    5  TIER_REFUSED / PATH_BOUNDARY — a governance refusal (matching the spine
       skills' refusal code)
    3  a missing dependency

Usage:
    python3 load_context.py                       # list the scopes
    python3 load_context.py catalog
    python3 load_context.py metric/payment_approval_rate
    python3 load_context.py module/checkout --compact --limit 5
    python3 load_context.py entity/checkout_session
    python3 load_context.py coverage/cart_abandon_reason
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS))
sys.path.insert(0, str(SKILLS / "query-metric" / "scripts"))
sys.path.insert(0, str(SKILLS / "catalog" / "scripts"))
from _shared import coverage as cov  # noqa: E402
from _shared import spine  # noqa: E402
from _shared.exploration import EXPLORABLE_HINT  # noqa: E402

try:
    import yaml
except ImportError:
    sys.stderr.write("Missing dependency pyyaml. Install with:\n  pip install pyyaml\n")
    sys.exit(3)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)

SERVED_TIER = "T2"
DEFAULT_FRESHNESS_DAYS = 90
DEFAULT_LIMIT = 20

REFUSAL_EXIT = 5
NOT_FOUND_EXIT = 1

SCOPES = {
    "catalog": "every registered metric, plus latency KPIs and the entity registry",
    "metric/{slug}": "one metric's full definition file",
    "module/{name}": "every metric definition under that module",
    "entities": "the entity registry: slug, grain, module, status",
    "entity/{name}": "one entity's full definition: grain, keys, dimensions, relationships, rules",
    "lineage/{module}": "the module's LINEAGE.md — shared business rules and view lineage",
    "tiers": "the accuracy-tier doctrine and the refusal rules",
    "governance": "the ownership and governance doctrine: owners, cadence, freshness SLA",
    "spines": "the module registry as data: spines, their status, and any handoff edge",
    "coverage": "the instrumentation coverage register, grouped by status",
    "coverage/{id}": "one coverage entry",
    "glossary": "reserved-term definitions (may be empty in this template)",
    "glossary/{term}": "one glossary term",
}


class Sentinel(Exception):
    """A contract sentinel. Carries the line to print and the exit code."""

    def __init__(self, name: str, detail: str, code: int):
        super().__init__(f"{name} -- {detail}")
        self.line = f"{name} -- {detail}"
        self.code = code


def scope_not_found(detail: str) -> Sentinel:
    return Sentinel("SCOPE_NOT_FOUND", detail, NOT_FOUND_EXIT)


def empty_result(detail: str) -> Sentinel:
    return Sentinel("EMPTY_RESULT", detail, NOT_FOUND_EXIT)


def path_boundary(detail: str) -> Sentinel:
    return Sentinel("PATH_BOUNDARY", detail, REFUSAL_EXIT)


def tier_refused(detail: str) -> Sentinel:
    return Sentinel("TIER_REFUSED", detail, REFUSAL_EXIT)


# ---------------------------------------------------------------------------
# Read roots + the boundary check
# ---------------------------------------------------------------------------
# Everything this API can read, enumerated. The check is on the RESOLVED path, so
# a `..` inside a slug cannot walk out of the tree: resolving first and comparing
# after is the only ordering that is safe, because a textual check on the slug has
# to anticipate every encoding of "parent directory".


def _plugin_root() -> Path:
    return SKILLS.parent


def read_roots() -> dict:
    import kattfot_query as aq

    qm = SKILLS / "query-metric"
    return {
        "consumer_metrics": aq.metrics_dir(),
        "bundled_metrics": qm / "metrics",
        "entities": qm / "entities",
        "doctrine": qm / "doctrine",
        "config": _plugin_root() / "config",
        "glossary": _plugin_root() / "glossary",
    }


def check_boundary(path: Path) -> Path:
    resolved = path.resolve()
    for root in read_roots().values():
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except (ValueError, OSError):
            continue
    raise path_boundary(
        f"{path} resolves outside every allowed read root "
        f"({', '.join(sorted(read_roots()))})"
    )


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        return (yaml.safe_load(m.group(1)) or {}), m.group(2)
    except yaml.YAMLError:
        return {}, m.group(2)


def metric_files() -> list:
    """(path, frontmatter) for every registered metric, consumer dir winning."""
    import kattfot_query as aq

    seen: dict = {}
    for root in (aq.metrics_dir(), aq.bundled_metrics_dir()):
        if not root or not root.exists():
            continue
        for p in sorted(root.rglob("*.md")):
            fm, _ = split_frontmatter(p.read_text())
            name = fm.get("name")
            if name and name not in seen:
                seen[name] = (p, fm)
    return [seen[k] for k in sorted(seen)]


def entity_files() -> list:
    root = read_roots()["entities"]
    out = []
    if root.exists():
        for p in sorted(root.rglob("*.md")):
            fm, body = split_frontmatter(p.read_text())
            if fm.get("entity"):
                out.append((p, fm, body))
    return out


def first_body_line(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def stale_line(path: Path, fm: dict, freshness_days: int) -> str | None:
    """`STALE:` when a returned file has not been touched inside the SLA.

    Read from the filesystem mtime rather than a hand-maintained `updated:` field,
    because the field would be the thing that goes stale.
    """
    try:
        mtime = dt.date.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None
    age = (dt.date.today() - mtime).days
    if age <= freshness_days:
        return None
    owner = fm.get("owner")
    suffix = f", owner: {owner}" if owner else ""
    return f"STALE: {path.name} last updated {mtime.isoformat()}{suffix}"


def paginate(items: list, limit: int, offset: int) -> tuple:
    total = len(items)
    window = items[offset: offset + limit]
    footer = None
    if total > limit:
        start = offset + 1 if window else 0
        end = offset + len(window)
        nxt = offset + limit
        footer = f"_Showing items {start}-{end} of {total}."
        footer += f" Use --offset {nxt} for next page._" if nxt < total else "_"
    return window, footer


# ---------------------------------------------------------------------------
# Scope handlers
# ---------------------------------------------------------------------------


def render_catalog(args) -> str:
    """The compact table: metrics, then latency KPIs, then entities.

    Delegates the metric and KPI enumeration to the `catalog` skill's builder so
    the machine-readable and human-facing front doors cannot describe different
    catalogs — the one failure a consumer API of this kind is most likely to have.
    """
    import catalog as catalog_lib

    cat = catalog_lib.build()
    lines = [
        "# kattfot catalog (consumer API)",
        "",
        f"{cat['metric_count']} ratio metrics · {cat['kpi_count']} latency KPIs "
        f"· {len(cat['spines'])} spines",
        "",
        "| name | display_name | accuracy_tier | module | version | freshness | definition |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in cat["metrics"]:
        # A non-T2 metric is NAMED so a consumer can see it exists, and its
        # one-liner is withheld — because that one-liner is the first line of the
        # metric's own `## Definition`, which is body content. Handing it over
        # would be a small, quiet version of exactly what TIER_REFUSED prevents on
        # a direct request: a description of an ungoverned number, arriving through
        # the governed API, with nothing marking it as different.
        definition = (
            m["definition"] if m["accuracy_tier"] == SERVED_TIER
            else f"TIER_REFUSED — tier {m['accuracy_tier']}, body withheld"
        )
        lines.append(
            f"| {m['name']} | {m['display_name']} | {m['accuracy_tier']} | "
            f"{m['module']} | {m.get('version') or '—'} | {m.get('freshness')} | {definition} |"
        )
    non_t2 = [m for m in cat["metrics"] if m["accuracy_tier"] != SERVED_TIER]
    if non_t2:
        lines += ["", "Non-T2 metrics are listed above with their tier and are NOT "
                      "served as bodies by this API (TIER_REFUSED on a direct request):"]
        lines += [f"- {m['name']} ({m['accuracy_tier']})" for m in non_t2]
    if cat["latency_kpis"]:
        lines += [
            "",
            "| kpi | display_name | accuracy_tier | percentiles | engine | queryable |",
            "|---|---|---|---|---|---|",
        ]
        for k in cat["latency_kpis"]:
            lines.append(
                f"| {k['key']} | {k['display_name']} | {k['accuracy_tier']} | "
                f"{','.join(str(p) for p in k.get('percentiles') or [])} | "
                f"stage-timing | {str(k['queryable']).lower()} |"
            )
    ents = entity_files()
    if ents:
        lines += [
            "",
            "| entity | display_name | module | status | metric_backed | grain |",
            "|---|---|---|---|---|---|",
        ]
        for _p, fm, _b in ents:
            lines.append(
                f"| {fm['entity']} | {fm.get('display_name')} | {fm.get('module')} | "
                f"{fm.get('status', 'live')} | {str(fm.get('metric_backed', True)).lower()} | "
                f"{fm.get('grain')} |"
            )
    return "\n".join(lines) + "\n"


def render_metric(slug: str, args) -> str:
    files = metric_files()
    match = next((t for t in files if t[1].get("name") == slug), None)
    if match is None:
        match = next((t for t in files if t[0].stem == slug), None)
    if match is None:
        names = [fm.get("name") for _p, fm in files]
        close = difflib.get_close_matches(slug, [n for n in names if n], n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise scope_not_found(
            f"{slug} is not a registered metric.{hint} Registered: {', '.join(sorted(n for n in names if n))}"
        )
    path, fm = match
    check_boundary(path)
    tier = fm.get("accuracy_tier")
    if tier != SERVED_TIER:
        # T3 gets the machine-readable referral (there IS a lane for it);
        # T1 does not — the system of record is not an exploration target.
        hint = EXPLORABLE_HINT if tier == "T3" else ""
        raise tier_refused(
            f"{slug} is tier {tier}; the governed context API serves {SERVED_TIER} "
            f"only. T1 comes from the system of record, T3 belongs in the "
            f"exploratory lane (kattfot:explore) — handing either back here would "
            f"let a consumer present it as decision-grade.{hint}"
        )
    text = path.read_text()
    if args.compact:
        fmt, _body = split_frontmatter(text)
        text = yaml.safe_dump(fmt, sort_keys=False)
    stale = stale_line(path, fm, args.freshness_days)
    return text + (f"\n{stale}\n" if stale else "")


def render_module(name: str, args) -> str:
    files = [(p, fm) for p, fm in metric_files() if fm.get("module") == name]
    if not files:
        modules = sorted({fm.get("module") for _p, fm in metric_files() if fm.get("module")})
        raise scope_not_found(
            f"{name} has no registered metrics. Modules with metrics: {', '.join(modules)}"
        )
    window, footer = paginate(files, args.limit, args.offset)
    if not window:
        raise empty_result(f"module/{name} has {len(files)} metrics but offset {args.offset} is past the end")
    parts = []
    stale = []
    for path, fm in window:
        check_boundary(path)
        text = path.read_text()
        if fm.get("accuracy_tier") != SERVED_TIER:
            # A module read is not refused wholesale — a consumer asked for the
            # set — but a non-T2 member is named with its tier instead of handed
            # over, so the tier guarantee holds per item.
            parts.append(
                f"--- {path.name} ---\n"
                f"TIER_REFUSED -- {fm.get('name')} is tier {fm.get('accuracy_tier')}; "
                f"body withheld.\n"
            )
            continue
        if args.compact:
            f2, _b = split_frontmatter(text)
            text = yaml.safe_dump(f2, sort_keys=False)
        parts.append(f"--- {path.name} ---\n{text}")
        s = stale_line(path, fm, args.freshness_days)
        if s:
            stale.append(s)
    out = "\n".join(parts)
    if footer:
        out += "\n" + footer + "\n"
    if stale:
        out += "\n" + "\n".join(stale) + "\n"
    return out


def render_entities(args) -> str:
    ents = entity_files()
    if not ents:
        raise empty_result("the entity registry is empty")
    window, footer = paginate(ents, args.limit, args.offset)
    lines = [
        "| entity | display_name | module | status | metric_backed | grain | summary |",
        "|---|---|---|---|---|---|---|",
    ]
    for _p, fm, body in window:
        lines.append(
            f"| {fm['entity']} | {fm.get('display_name')} | {fm.get('module')} | "
            f"{fm.get('status', 'live')} | {str(fm.get('metric_backed', True)).lower()} | "
            f"{fm.get('grain')} | {first_body_line(body)} |"
        )
    out = "\n".join(lines) + "\n"
    if footer:
        out += footer + "\n"
    return out


def render_entity(name: str, args) -> str:
    ents = entity_files()
    match = next((t for t in ents if t[1].get("entity") == name), None)
    if match is None:
        match = next((t for t in ents if t[0].stem == name.replace("_", "-")), None)
    if match is None:
        slugs = sorted(fm["entity"] for _p, fm, _b in ents)
        close = difflib.get_close_matches(name, slugs, n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise scope_not_found(
            f"{name} is not a registered entity.{hint} Registered: {', '.join(slugs)}"
        )
    path, fm, _body = match
    check_boundary(path)
    text = path.read_text()
    if args.compact:
        text = yaml.safe_dump(fm, sort_keys=False)
    stale = stale_line(path, fm, args.freshness_days)
    return text + (f"\n{stale}\n" if stale else "")


def render_lineage(module: str, args) -> str:
    path = read_roots()["bundled_metrics"] / module / "LINEAGE.md"
    check_boundary(path)
    if not path.exists():
        available = sorted(
            p.parent.name
            for p in read_roots()["bundled_metrics"].rglob("LINEAGE.md")
        )
        raise scope_not_found(
            f"no LINEAGE.md for module {module}. Available: {', '.join(available)}"
        )
    return path.read_text()


def render_doctrine(filename: str, label: str, args) -> str:
    path = read_roots()["doctrine"] / filename
    check_boundary(path)
    if not path.exists():
        raise scope_not_found(f"the {label} doctrine file is missing at {path}")
    fm, _body = split_frontmatter(path.read_text())
    text = path.read_text()
    stale = stale_line(path, fm, args.freshness_days)
    return text + (f"\n{stale}\n" if stale else "")


def render_spines(args) -> str:
    """The module registry as data, including any handoff edge.

    Added beyond the contract's original scope table (a new scope is
    non-breaking): the handoff edge and its coverage caveat are governed context a
    consumer genuinely needs, and without this scope the only way to reach them is
    to read the YAML directly — which is exactly the path-coupling the scope
    vocabulary exists to avoid.
    """
    registry = spine.load_registry()
    rows = []
    for mod, mod_cfg in (registry.get("modules") or {}).items():
        for name, cfg in (mod_cfg.get("spines") or {}).items():
            rows.append({
                "module": mod,
                "module_label": mod_cfg.get("label"),
                "spine": name,
                "primary_entity": cfg.get("primary_entity"),
                "status": cfg.get("status"),
                "default": bool(cfg.get("default")),
                "spec": cfg.get("spec"),
                "skills": list(cfg.get("skills") or []),
                "excluded": sorted((cfg.get("excluded") or {}).keys()),
                "handoff": cfg.get("handoff"),
            })
    if not rows:
        raise empty_result("no spine is registered")
    return json.dumps({"spines": rows}, indent=2, default=str) + "\n"


def render_coverage(args) -> str:
    register = cov.load_coverage()
    grouped = cov.summary(register)
    if not grouped:
        raise empty_result(
            "the coverage register is absent or empty — nothing is registered as "
            "measurable or blocked"
        )
    lines = ["| id | status | queryable | detail | ref | owner |", "|---|---|---|---|---|---|"]
    for status, entries in grouped.items():
        for e in entries:
            detail = (e.get("serve") or e.get("blocker") or "").strip().split(".")[0]
            lines.append(
                f"| {e.get('id')} | {status} | "
                f"{str(status in cov.QUERYABLE).lower()} | {detail} | "
                f"{e.get('blocker_ref') or e.get('jira') or '—'} | {e.get('owner') or '—'} |"
            )
    return "\n".join(lines) + "\n"


def render_coverage_entry(entry_id: str, args) -> str:
    register = cov.load_coverage()
    hit = cov.by_id(register, entry_id)
    if hit is None:
        ids = [e.get("id") for e in cov.entries(register)]
        close = difflib.get_close_matches(entry_id, [i for i in ids if i], n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise scope_not_found(
            f"{entry_id} is not a coverage entry.{hint} Registered: {', '.join(sorted(i for i in ids if i))}"
        )
    return yaml.safe_dump(hit, sort_keys=False, width=88)


def render_glossary(term: str | None, args) -> str:
    root = read_roots()["glossary"]
    if not root.exists() or not any(root.glob("*.md")):
        raise empty_result(
            "no glossary is authored in this template. The scope exists because the "
            "contract promises it; an empty scope is a documented state, not an error."
        )
    if term:
        path = root / f"{term}.md"
        check_boundary(path)
        if not path.exists():
            raise scope_not_found(f"{term} is not a glossary term")
        return path.read_text()
    parts = []
    for p in sorted(root.glob("*.md")):
        check_boundary(p)
        parts.append(f"--- {p.name} ---\n{p.read_text()}")
    return "\n".join(parts)


def list_scopes() -> str:
    lines = [
        "# kattfot:load-context — available scopes",
        "",
        "Pass one as the single positional argument. Definitions only — never a "
        "live number; for that, call kattfot:query-metric.",
        "",
        "| scope | returns |",
        "|---|---|",
    ]
    lines += [f"| `{k}` | {v} |" for k, v in SCOPES.items()]
    lines += [
        "",
        "Failure modes are single lines with stable prefixes, per "
        "contracts/consumer-api.md: SCOPE_NOT_FOUND, EMPTY_RESULT, PATH_BOUNDARY, "
        "TIER_REFUSED.",
    ]
    return "\n".join(lines) + "\n"


def dispatch(scope: str, args) -> str:
    head, _, tail = scope.partition("/")
    tail = tail.strip("/")
    if head == "catalog" and not tail:
        return render_catalog(args)
    if head == "metric":
        if not tail:
            raise scope_not_found("metric/{slug} needs a metric name")
        return render_metric(tail, args)
    if head == "module":
        if not tail:
            raise scope_not_found("module/{name} needs a module name")
        return render_module(tail, args)
    if head == "entities" and not tail:
        return render_entities(args)
    if head == "entity":
        if not tail:
            raise scope_not_found("entity/{name} needs an entity slug")
        return render_entity(tail, args)
    if head == "lineage":
        if not tail:
            raise scope_not_found("lineage/{module} needs a module name")
        return render_lineage(tail, args)
    if head == "tiers" and not tail:
        return render_doctrine("accuracy-tiers.md", "accuracy-tier", args)
    if head == "governance" and not tail:
        return render_doctrine("ownership-governance.md", "ownership & governance", args)
    if head == "spines" and not tail:
        return render_spines(args)
    if head == "coverage":
        return render_coverage_entry(tail, args) if tail else render_coverage(args)
    if head == "glossary":
        return render_glossary(tail or None, args)
    close = difflib.get_close_matches(head, [s.split("/")[0] for s in SCOPES], n=3, cutoff=0.4)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise scope_not_found(
        f"{scope!r} is not a known scope.{hint} Available: {', '.join(SCOPES)}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scope", nargs="?", default=None, help="the scope to load")
    ap.add_argument("--compact", action="store_true",
                    help="frontmatter only, for multi-file and definition scopes")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--freshness-days", type=int, default=DEFAULT_FRESHNESS_DAYS,
                    help=f"STALE threshold in days (default {DEFAULT_FRESHNESS_DAYS})")
    args = ap.parse_args()

    if not args.scope:
        sys.stdout.write(list_scopes())
        return 0
    try:
        sys.stdout.write(dispatch(args.scope, args))
    except Sentinel as s:
        # The sentinel goes to STDOUT because it is the response, not a log line:
        # a consumer prefix-matches it the same way it would parse content. The
        # exit code is the machine-readable duplicate for a shell caller.
        print(s.line)
        return s.code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
