#!/usr/bin/env python3
"""kattfot_explore.py — the vetted ad-hoc SQL helper behind `kattfot:explore`.

The exploratory lane: free-form SELECT SQL over the surfaces declared in
config/exploration.yaml, for the questions no governed metric can answer.
Every result is stamped **T3** — ad hoc, no reviewed definition, no owner,
not decision-grade — and carries the same provenance machinery as a governed
number: a manifest receipt, an audit-chain trace, and the fixture watermark
where applicable. Exploration relaxes the *allow-lists*, never the *account*.

Governance, in code rather than in a prompt:

  * exactly one SELECT statement — DML/DDL/scripting refused (`_shared/sqlguard`)
  * every referenced table must be registered in the exploration register,
    which FAILS CLOSED (`_shared/exploration`) — an unreadable register means
    no exploration, not open exploration
  * `SELECT *` only on surfaces whose register entry attests `select_star`
  * R6 PII scan over EVERY identifier in the statement — projection, WHERE,
    ON — hard refuse, no override, same rule as the governed lane
  * on BigQuery: the query runs as the individual human via ADC (service
    accounts refused at the adapter), and `defaults.max_bytes_billed` caps
    what one ad-hoc query may scan
  * rows are returned uncapped, by design: an analyst inspecting outliers
    needs the rows. What bounds row access is the register plus the caller's
    own warehouse identity — the query can read nothing the human cannot.

Deliberately NOT here: metric allow-lists (there is no metric), maturity
cutoffs (an ad-hoc window lives inside the SQL where this layer cannot see
it — the T3 stamp is the caveat), and any silent fallback between engines.

`--dry-run`: estimate what the query would cost instead of running it.
Governance precedes cost — every guard above runs first, unchanged, so a
query that would be refused is refused, not priced. Past the guards, the
estimate is capability-gated (`CAP_DRY_RUN`, `_shared/adapters/base.py`):
BigQuery reports real bytes-to-be-scanned via a dry-run job that never
executes and bills nothing; sqlite has no cost dimension to estimate, so it
reports `estimate_available: false` with an honest reason rather than a
faked number — the guards still ran, so this is a governance lint, not a
refusal. A dry run gets no manifest, no `result_sha`, and — unlike every
other exit-0 path here — no audit append: the manifest and the audit chain
both fingerprint a *read* (what ran, what came back), and a dry run reads
nothing. An auditor reconstructing "what did this person query" would want
dry-run attempts too, but the audit record schema is built entirely around
results, and this lane keeps one schema rather than growing a second for
attempts; the BigQuery dry-run call itself is still a real, identity-
asserted API call, so it is not unattributable, just not logged here.

Exit codes: 0 success (including a `--dry-run` cost preview, or its honest
"no estimate" reply on sqlite); 5 governance refusal (statement / surface /
star / PII / dialect); 2/3/4 via the adapter layer (ADC missing, deps
missing, service account refused); 1 bad invocation or missing register.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

# Make `_shared` importable when this script is invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _shared import adapters as adapters_lib  # noqa: E402
from _shared import exploration as exploration_lib  # noqa: E402
from _shared import guardrails as guardrails_lib  # noqa: E402
from _shared import manifest as manifest_lib  # noqa: E402
from _shared import sqlguard  # noqa: E402
from _shared.dialect import DialectError  # noqa: E402
from _shared.pii import is_pii_column, pii_reason  # noqa: E402

# Reuse the governed helper's project resolution, filter parsing, and audit
# plumbing rather than re-implementing them (same precedent as _shared/verify's
# loader): one audit log, one hash chain, one schema for the trace.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "query-metric" / "scripts")
)
import kattfot_query as aq  # noqa: E402

REFUSAL_EXIT = 5

#: The warning every exploratory result carries, unconditionally. This is the
#: lane's whole honesty story in one line — it must survive into the artifact,
#: the same way the FIXTURE warning does.
T3_WARNING = (
    "EXPLORATORY (T3): this is an ad-hoc result — no reviewed definition, no "
    "named owner, and no promise it means the same thing next week. Not "
    "decision-grade. If this question recurs, promote it to a governed T2 "
    "metric (kattfot:promote)."
)


def refuse(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(REFUSAL_EXIT)


def validate_surfaces(register: dict, refs: set) -> dict:
    """Every referenced table must be a `live` register entry. Returns
    {surface: entry} for the referenced ones."""
    known = exploration_lib.surfaces(register)
    out = {}
    for ref in sorted(refs):
        entry = known.get(ref)
        if not isinstance(entry, dict):
            registered = ", ".join(sorted(known)) or "(none)"
            hint = ""
            if "." in ref and ref.count(".") >= 2:
                hint = (
                    " Write surfaces as dataset.table — the project is "
                    "resolved by the runtime, not named in the SQL."
                )
            refuse(
                f"ERROR (EXPLORE-SURFACE): table '{ref}' is not in the "
                f"exploration register ({exploration_lib.register_path()}). "
                f"Registered surfaces: {registered}.{hint} Adding a surface "
                f"is a governance change, not a config tweak."
            )
        if entry.get("status") != "live":
            refuse(
                f"ERROR (EXPLORE-SURFACE): '{ref}' is registered but not live "
                f"(status: {entry.get('status')!r}). "
                f"{entry.get('notes') or 'Nothing is provisioned behind it.'}"
            )
        out[ref] = entry
    return out


def validate_pii(sql: str) -> None:
    """R6 over every identifier in the statement. Checked before anything is
    executed, and deliberately blunt: an alias named like PII refuses too."""
    for tok in sorted(sqlguard.identifiers(sql)):
        if is_pii_column(tok):
            refuse(
                f"ERROR (R6): identifier '{tok}' is personal PII "
                f"({pii_reason(tok)}) and is refused by design — in the "
                f"exploratory lane every identifier in the statement is "
                f"scanned, not just dimensions and filters. No override."
            )


def validate_select_star(sql: str, entries: dict) -> None:
    if not sqlguard.uses_select_star(sql):
        return
    unattested = sorted(s for s, e in entries.items() if not e.get("select_star"))
    if unattested:
        refuse(
            f"ERROR (EXPLORE-STAR): SELECT * is refused over "
            f"{', '.join(unattested)} — the register does not attest that "
            f"surface as PII-free (`select_star: true`). Name the columns "
            f"instead; each named identifier is PII-scanned."
        )


def qualify_sql(sql: str, refs: set, project: str) -> str:
    """Rewrite each registered `dataset.table` reference to the canonical
    backticked, project-qualified form. This is the one rewrite the lane
    performs, and it is what makes the same SQL run on both engines: the
    sqlite translator flattens `` `project.dataset.table` `` to the fixture
    table (the last dot-segment). Already-backticked references are handled
    too, so an analyst may write either form."""
    out = sql
    for ref in sorted(refs, key=len, reverse=True):
        pattern = re.compile(
            r"`" + re.escape(ref) + r"`|(?<![\w.`])" + re.escape(ref) + r"(?![\w.`])"
        )
        out = pattern.sub(f"`{project}.{ref}`", out)
    return out


def format_bytes(n: int) -> str:
    """Base-2 human-readable rendering of a byte count (`B`/`KiB`/.../`PiB`),
    for the `--dry-run` console output. The raw integer stays the
    authoritative field in the JSON; this is a convenience alongside it."""
    val = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if val < 1024.0 or unit == "PiB":
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.2f} {unit}"
        val /= 1024.0


def build_dry_run_result(
    *,
    adapter,
    engine: str,
    mode: str,
    reason: str,
    project: str,
    qualified_sql: str,
    params: dict,
    refs: set,
    question: str,
    max_bytes,
) -> dict:
    """The `--dry-run` result: the same provenance envelope a normal result
    carries (T3 stamp, question, surfaces, engine, qualified SQL) reporting a
    cost estimate instead of rows.

    Capability-gated, same as `percentiles()`: only an engine advertising
    `CAP_DRY_RUN` can price a query without running it (today, only BigQuery).
    On sqlite this still runs every governance guard above and reports
    `estimate_available: false` with an honest reason rather than faking a
    byte count — a governance lint that works fully offline, not a refusal.

    No `manifest` and no `result_sha` here, unlike a normal result: those
    fingerprint a READ (the query_sha of what ran, the result_sha of what came
    back), and a dry run reads nothing — there is no result to fingerprint.
    The caller likewise skips the audit append entirely; see the module
    docstring for why that is a deliberate omission, not an oversight.
    """
    result = {
        "skill": "explore",
        "dry_run": True,
        "accuracy_tier": "T3",
        "question": question,
        "surfaces": sorted(refs),
        "source_mode": mode,
        "source_mode_reason": reason,
        "source_project": project,
        "engine": engine,
        "engine_capabilities": sorted(adapter.capabilities()),
        "query": qualified_sql,
        "query_params": params,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "agent": {
            "skill": "explore",
            "plugin_version": manifest_lib.plugin_version(),
        },
        "warnings": [T3_WARNING],
    }
    if adapter.supports(adapters_lib.CAP_DRY_RUN):
        estimate = adapter.dry_run(project, qualified_sql, params)
        total_bytes = int(estimate["total_bytes_processed"])
        would_exceed = (total_bytes > max_bytes) if max_bytes is not None else None
        result["estimate_available"] = True
        result["estimate"] = {
            "total_bytes_processed": total_bytes,
            "total_bytes_processed_human": format_bytes(total_bytes),
            "max_bytes_billed": max_bytes,
            "max_bytes_billed_human": (
                format_bytes(max_bytes) if max_bytes is not None else None
            ),
            "would_exceed": would_exceed,
        }
    else:
        result["estimate_available"] = False
        result["estimate_unavailable_reason"] = (
            f"the {engine} engine does not advertise "
            f"{adapters_lib.CAP_DRY_RUN!r} — it has no cost dimension to "
            f"estimate (a committed fixture read has no bytes-scanned concept "
            f"the way a warehouse query does). Every governance guard above "
            f"still ran, so --dry-run still works offline as a governance "
            f"lint even with no estimate to report."
        )
    return result


def build_audit_record(result: dict, user_email: str | None) -> dict:
    """Same schema as the governed lane's trace (one audit log, one shape),
    with `metric: null` and the exploratory fields added: the question asked
    and the surfaces read. Row data is never logged."""
    record = aq.build_audit_record(result, user_email)
    record["metric"] = None
    record["question"] = result.get("question")
    record["surfaces"] = result.get("surfaces")
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sql", default=None, help="the SELECT statement to run")
    p.add_argument(
        "--sql-file", default=None, help="read the SELECT statement from a file"
    )
    p.add_argument(
        "--question",
        required=True,
        help="the analyst's question, in words — recorded in provenance and audit",
    )
    p.add_argument(
        "--param",
        action="append",
        default=[],
        help="name=value bound as a query parameter (repeatable; bound, never interpolated)",
    )
    p.add_argument(
        "--engine",
        default=None,
        help="override KATTFOT_ENGINE for this run (sqlite | bigquery)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "estimate what the query would cost instead of running it. Every "
            "governance guard still runs unchanged — a query that would be "
            "refused is refused, not estimated. No rows, no manifest, no "
            "audit append."
        ),
    )
    p.add_argument("--output-format", default="json", choices=["json"])
    args = p.parse_args()

    if (args.sql is None) == (args.sql_file is None):
        raise SystemExit("ERROR: provide exactly one of --sql or --sql-file.")
    sql = args.sql if args.sql is not None else Path(args.sql_file).read_text()
    if not args.question.strip():
        raise SystemExit("ERROR: --question must not be empty.")
    params = aq.parse_filters(args.param)

    register = exploration_lib.load_register()  # fail-closed

    try:
        sqlguard.assert_single_select(sql)
    except sqlguard.SqlGuardError as e:
        refuse(f"ERROR (EXPLORE-STATEMENT): {e}")
    refs = sqlguard.referenced_tables(sql)
    entries = validate_surfaces(register, refs)
    validate_select_star(sql, entries)
    validate_pii(sql)

    engine = adapters_lib.resolve_engine(args.engine)
    mode = "bigquery" if engine == "bigquery" else "fixture_sql"
    reason = (
        "KATTFOT_ENGINE=bigquery — running against the live warehouse"
        if mode == "bigquery"
        else f"KATTFOT_ENGINE={engine} — the SQL is running against the committed "
        f"fixture CSVs loaded into in-memory sqlite"
    )
    project = aq.resolve_project({}, mode)
    qualified_sql = qualify_sql(sql, refs, project)
    max_bytes = exploration_lib.max_bytes_billed(register)

    adapter = adapters_lib.get_adapter(args.engine)

    if args.dry_run:
        # All governance above is unchanged and already ran — a query that
        # would be refused was refused before reaching this point, not
        # estimated. Nothing below this branch executes: no run_query, no
        # rows, no manifest, no audit append (see build_dry_run_result and
        # the module docstring for why the audit chain does not get a line).
        result = build_dry_run_result(
            adapter=adapter,
            engine=engine,
            mode=mode,
            reason=reason,
            project=project,
            qualified_sql=qualified_sql,
            params=params,
            refs=refs,
            question=args.question.strip(),
            max_bytes=max_bytes,
        )
        print(json.dumps(result, default=str, indent=2))
        return

    try:
        rows = adapter.run_query(
            project, qualified_sql, params, max_bytes_billed=max_bytes
        )
    except DialectError as e:
        refuse(
            f"ERROR (EXPLORE-DIALECT): {e}\nThe sqlite fixture engine runs a "
            f"translated subset of BigQuery SQL (see _shared/dialect.py). "
            f"Rewrite with translated constructs, or run with "
            f"--engine bigquery against a live warehouse."
        )
    fixture_backed = adapter.fixture_backed

    warnings = [T3_WARNING]
    if fixture_backed:
        warnings.append(
            f"FIXTURE DATA: source mode is {mode} — {reason}. These numbers "
            f"come from committed synthetic fixtures and describe a seeded "
            f"random process, not any real business. Every downstream "
            f"rendering must carry the watermark."
        )
    if not rows:
        warnings.append(
            "Zero rows returned. Values inside the SQL (and --param bindings) "
            "match exactly — check for typos or unseen categories, or widen "
            "the window written into the query."
        )
    gr = guardrails_lib.load_guardrails()
    for surface in sorted(refs):
        warnings.extend(
            guardrails_lib.note_warnings(gr, surface, sorted(sqlguard.identifiers(sql)))
        )

    result = {
        "skill": "explore",
        "accuracy_tier": "T3",
        "question": args.question.strip(),
        "surfaces": sorted(refs),
        "source_mode": mode,
        "source_mode_reason": reason,
        "source_project": project,
        "engine": engine,
        "fixture_backed": fixture_backed,
        "engine_capabilities": sorted(adapter.capabilities()),
        "max_bytes_billed": max_bytes if mode == "bigquery" else None,
        "query": qualified_sql,
        "query_params": params,
        "rows": rows,
        "row_count": len(rows),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "agent": {
            "skill": "explore",
            "plugin_version": manifest_lib.plugin_version(),
        },
        "warnings": warnings,
    }

    # The receipt. `register_sha` replaces the governed lane's metric_def_sha:
    # there is no reviewed definition to fingerprint, but there IS an
    # allow-list version that authorized the read, and a reviewer should be
    # able to tell which one.
    result["manifest"] = {
        "plugin_version": manifest_lib.plugin_version(),
        "register_sha": exploration_lib.register_sha(),
        "query_sha": manifest_lib.query_sha(qualified_sql, params),
        "result_sha": manifest_lib.result_sha(rows),
    }

    if os.environ.get("KATTFOT_AUDIT_DISABLE") not in ("1", "true", "True"):
        user_email = None
        if mode == "bigquery":
            try:
                from _shared.identity import lookup_email

                user_email, _ = lookup_email()
            except Exception:
                user_email = None
        aq.append_audit(build_audit_record(result, user_email))

    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
