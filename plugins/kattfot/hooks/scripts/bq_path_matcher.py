"""Shared command-string matcher for BigQuery/GCP bypass detection.

Used by both Claude Code (PreToolUse/Bash) and Cursor (beforeShellExecution)
adapters. Denies Bash-shaped commands that reach BigQuery/GCP outside the
kattfot plugin's vetted skill scripts (see query-metric/SKILL.md "Hard
rules").

Why this exists: `_shared/identity.py`'s user-identity check and
`_shared/pii.py`'s R6 dimension/filter guard only run when a vetted helper
is invoked. Nothing at the shell-tool level previously stopped a command
from reaching the same BigQuery project via the `bq` CLI, `gcloud`, a raw
REST/curl call, or an ad hoc python one-liner using `google-cloud-bigquery`
directly — all of which share the same ADC credentials on disk but skip
those guards. SKILL.md's "Hard rules" forbid those paths by convention; the
hook adapters are the technical backstop.

This is a command-string blocklist, not a sandbox. Known gaps:
  - Cannot see the CONTENTS of a script file. `python3 some_file.py` bypasses
    detection unless the Bash command text itself names bigquery/the client
    library — a file that imports google-cloud-bigquery without saying so
    on the command line is invisible to this hook (including repo-root
    `scripts/` one-offs that are outside the plugin).
  - Matches on lexical shape. Deliberate obfuscation (base64, `eval`, an
    unfamiliar tool/language) can still slip through.
  - Only inspects shell commands; a code-execution path through some other
    tool is not covered.
  - Over-blocking is accepted where it reduces false negatives (e.g. any
    inline `python -c`/`-m` mentioning "bigquery" at all is denied, even
    a harmless `python3 -c "print('bigquery')"`) — consistent with this
    plugin's PII-guard philosophy that a wall is allowed to be blunt.
  - Subcommand lists are enumerated per tool (`bq`, `dbt`, `mf`), so a verb
    added by a future release of one of those CLIs is not matched until it is
    added here. The trade is deliberate: naming the verbs is what keeps prose
    mentioning these tools from denying itself.
  - A tool named inside a quoted argument is invisible unless the command also
    looks like a shell wrapper (see `_SHELL_WRAPPER`). That is the deliberate
    trade for not denying prose: `git commit -m "bq ls"` is allowed, and so is
    an unrecognized wrapper that executes its argument
    (`my_runner 'bq ls'`). `bash`/`sh`/`zsh -c`, `eval`, `xargs`, and `ssh`
    are covered; a wrapper outside that list is a gap.
  - Prefix commands are enumerated (`_CMD_PREFIX`), so `sudo`, `env FOO=1`,
    `timeout 30`, `nohup` and friends are seen, but an unlisted one that runs
    another command would hide it.
This raises the bar; it does not replace BigQuery-side policy tags as the
durable, tool-independent control (see SKILL.md's "Hard rules").
"""

from __future__ import annotations

import re

# --- Command position -------------------------------------------------------
#
# Every tool matcher below answers one question: is this token something the
# shell will RUN, or a word inside an argument? The original answer was
# "preceded by whitespace, an operator, or a quote" — but that class contains a
# bare space, so English naming a tool denied itself. `git commit -m "bq query
# typo fix"` and `gh pr create --body "...the dbt DAG..."` are not BigQuery
# access; discussing these tools is among the most common things typed in this
# repo, and a guard that blocks writing *about* itself trains people to work
# around it.
#
# So a real command position is required: start of string/line, or right after
# an operator that begins a new command (`;` `&&` `||` `|` `(` backtick `$(`
# newline), optionally behind prefix commands and env assignments that precede
# a real command (`sudo`, `env FOO=1`, `timeout 30`, `nohup`, the `then`/`do`
# keywords). A bare space no longer qualifies.
#
# The cost: a tool name inside a quoted argument becomes invisible — which is
# what we want for `git commit -m`, but NOT for `bash -c "bq ls"`, where the
# quoted string IS a command. `_SHELL_WRAPPER` detects that case and re-runs the
# old blunt matching, so the wall stays blunt exactly where a quoted string is
# about to be executed.
# Operators that begin a new command. `{` covers brace grouping (`{ bq ls; }`)
# and `)` covers a `case` branch (`case x in y) bq ls;;`) — both are command
# positions an operator-only list misses.
_CMD_START = r"(?:^|(?<=[;&|`({)\n]))\s*"
# Tokens that may sit between a command position and the real command. Bounded
# repetition (not `*`) keeps backtracking predictable on adversarial input.
# `-\S+\s+(?!-)\S+` comes first so a flag that takes a space-separated value
# (`sudo -u someone bq ls`) is consumed as one unit rather than stranding the
# value token in front of the tool name.
_CMD_PREFIX = (
    r"(?:(?:\w+=\S+|sudo|doas|env|command|exec|time|nohup|nice|stdbuf|"
    r"timeout|watch|ionice|setsid|then|do|else|-\S+\s+(?!-)\S+|-\S+|\d+\w?)"
    r"\s+){0,6}"
)
_CMD_POS = _CMD_START + _CMD_PREFIX
# The pre-existing, deliberately blunt boundary class — still used inside shell
# wrappers, where a quoted string is a command rather than prose.
_LOOSE_POS = r"(?:^|[\s;&|`(\"'])"
# `bash -c "..."`, `sh -c '...'`, `eval`, `xargs`, `ssh host "..."`: the
# argument is executed, so anything named inside it is a real invocation. The
# flag is matched as a cluster ending in `c` so combined forms (`bash -lc`,
# `sh -ec`) count too.
# `find -exec`/`-execdir` and `parallel` also run their argument as a command
# without a shell operator in front of it, so they belong here too.
_SHELL_WRAPPER = re.compile(
    r"(?:^|[\s;&|`(])(?:ba|z|k|a|da)?sh\s+(?:-\S+\s+)*-{1,2}[a-z]*c\b"
    r"|(?:^|[\s;&|`(])(?:eval|xargs|parallel|ssh)\b"
    r"|(?:^|\s)-exec(?:dir)?\b",
    re.IGNORECASE | re.MULTILINE,
)

# `bq` CLI subcommands that actually touch a project/dataset/table.
_BQ_SUBCOMMANDS = (
    r"(?:query|show|ls|extract|load|cp|mk|rm|head|update|version|mkdef|"
    r"partition|init)"
)
_BARE_BQ_CLI = re.compile(
    rf"{_CMD_POS}bq\s+{_BQ_SUBCOMMANDS}\b", re.MULTILINE
)
_BARE_BQ_CLI_LOOSE = re.compile(
    rf"{_LOOSE_POS}bq\s+{_BQ_SUBCOMMANDS}\b", re.MULTILINE
)

_GCLOUD_BIGQUERY = re.compile(
    rf"{_CMD_POS}gcloud\b.*?(?:(?<![\w.-])bq(?![\w.-])|\bbigquery\b)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
_GCLOUD_BIGQUERY_LOOSE = re.compile(
    r"\bgcloud\b.*?(?:(?<![\w.-])bq(?![\w.-])|\bbigquery\b)",
    re.IGNORECASE | re.DOTALL,
)

_PY_ANY = re.compile(r"(?:^|[\s;&|`(\"'])python3?\b", re.MULTILINE)
_PY_INLINE = re.compile(
    r"(?:^|[\s;&|`(\"'])python3?\s+(?:-\S+\s+)*-[cm]\b", re.MULTILINE
)
_BIGQUERY_WORD = re.compile(r"\bbigquery\b", re.IGNORECASE)
_BIGQUERY_CLIENT_SIGNAL = re.compile(
    r"google\.cloud\.bigquery|bigquery\.Client|import\s+bigquery\b",
    re.IGNORECASE,
)

_CURL_GOOGLEAPIS = re.compile(
    r"\b(?:curl|wget)\b.*googleapis\.com", re.IGNORECASE | re.DOTALL
)

# `dbt`/`mf` (dbt-metricflow) CLI invocations. Both execute against the
# warehouse via the project's own profile (ADC-backed or otherwise) entirely
# outside the plugin's identity/PII helpers. They typically use the *same*
# per-user ADC as the vetted scripts, which is exactly why they must still go
# through a vetted wrapper rather than being run ad hoc.
#
# Require a REAL subcommand, matching how `_BARE_BQ_CLI` handles `bq`. An
# earlier version accepted any following token (`dbt\s+\S`), which combined with
# the old space-permitting boundary meant any "dbt <word>" denied itself. The
# subcommand list and `_CMD_POS` are independent narrowings and both are needed:
# the list rules out `dbt promotion`, the command position rules out a real verb
# quoted inside prose (`git commit -m "fix the dbt run failure"`).
_DBT_SUBCOMMANDS = (
    r"(?:build|clean|clone|compile|debug|deps|docs|init|list|ls|parse|retry|"
    r"run-operation|run|seed|show|snapshot|source|test)"
)
_MF_SUBCOMMANDS = (
    r"(?:query|list-dimensions|list-entities|list-metrics|"
    r"list-dimension-values|list|validate-configs|health-checks|tutorial|"
    r"version)"
)
# Global flags may precede the subcommand (`dbt --profiles-dir ./x run`), and
# such a flag may take a space-separated value, so allow flag[ value] pairs.
# The value token is only ever consumed AFTER a flag, so prose like
# "dbt promotion into the DAG" still cannot reach the subcommand alternation.
_CLI_GLOBAL_FLAGS = r"(?:-{1,2}[\w-]+(?:=\S+)?\s+(?:(?!-)\S+\s+)?)*"
_DBT_CLI = re.compile(
    rf"{_CMD_POS}dbt\s+{_CLI_GLOBAL_FLAGS}{_DBT_SUBCOMMANDS}\b",
    re.MULTILINE,
)
_DBT_CLI_LOOSE = re.compile(
    rf"{_LOOSE_POS}dbt\s+{_CLI_GLOBAL_FLAGS}{_DBT_SUBCOMMANDS}\b",
    re.MULTILINE,
)
_MF_CLI = re.compile(
    rf"{_CMD_POS}mf\s+{_CLI_GLOBAL_FLAGS}{_MF_SUBCOMMANDS}\b",
    re.MULTILINE,
)
_MF_CLI_LOOSE = re.compile(
    rf"{_LOOSE_POS}mf\s+{_CLI_GLOBAL_FLAGS}{_MF_SUBCOMMANDS}\b",
    re.MULTILINE,
)

_USE_SANCTIONED = (
    "Use a vetted kattfot skill script instead — see query-metric/SKILL.md's "
    '"Hard rules" for the sanctioned-path table (e.g. kattfot_query.py, '
    "funnel_dropoff.py, kattfot_explore.py for ad-hoc T3 exploration, "
    "doctor.py --probe)."
)


def deny_reason(command: str) -> str | None:
    """Return a deny reason if `command` looks like it reaches BigQuery/GCP
    outside of the vetted analytics helpers, else None (allow).

    Deliberately does NOT special-case "the command also contains a sanctioned
    script somewhere" as an allow signal: a chained command like
    `python3 .../kattfot_query.py ... && bq query "SELECT ssn ..."` must
    still be denied for the second half.
    """
    # Inside a shell wrapper (`bash -c "..."`, `eval`, `xargs`, `ssh`) a quoted
    # string is executed, so fall back to the blunt boundary class there. Outside
    # one, a tool name must sit at a real command position — see `_CMD_POS`.
    shell_wrapped = _SHELL_WRAPPER.search(command) is not None

    def _invokes(strict: re.Pattern[str], loose: re.Pattern[str]) -> bool:
        if strict.search(command):
            return True
        return shell_wrapped and loose.search(command) is not None

    if _invokes(_BARE_BQ_CLI, _BARE_BQ_CLI_LOOSE):
        return f"Direct `bq` CLI access is blocked. {_USE_SANCTIONED}"

    if _invokes(_GCLOUD_BIGQUERY, _GCLOUD_BIGQUERY_LOOSE):
        return (
            f"A `gcloud` command referencing BigQuery is blocked. {_USE_SANCTIONED}"
        )

    if _PY_INLINE.search(command) and _BIGQUERY_WORD.search(command):
        return (
            "An inline python -c/-m invocation mentioning BigQuery is "
            f"blocked — it would skip the plugin's PII guard. {_USE_SANCTIONED}"
        )

    if _PY_ANY.search(command) and _BIGQUERY_CLIENT_SIGNAL.search(command):
        return (
            "A python invocation referencing the BigQuery client library "
            f"directly (not via a vetted skill script) is blocked. {_USE_SANCTIONED}"
        )

    if _CURL_GOOGLEAPIS.search(command):
        return f"Direct HTTP access to a Google API is blocked. {_USE_SANCTIONED}"

    if _invokes(_DBT_CLI, _DBT_CLI_LOOSE):
        return (
            "A `dbt` CLI invocation is blocked — it reaches the warehouse outside "
            f"the plugin's identity/PII helpers. {_USE_SANCTIONED}"
        )

    if _invokes(_MF_CLI, _MF_CLI_LOOSE):
        return (
            "An `mf` (dbt-metricflow) CLI invocation is blocked — it reaches the "
            f"warehouse outside the plugin's identity/PII helpers. {_USE_SANCTIONED}"
        )

    return None
