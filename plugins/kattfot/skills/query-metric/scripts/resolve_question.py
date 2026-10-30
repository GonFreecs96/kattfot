#!/usr/bin/env python3
"""resolve_question.py — a deterministic question → request mapping for query-metric.

**This is not an LLM.** It is keyword and alias matching over the registered
catalog, and it exists so the agent's Step 1 / Step 4 / Step 5 in `SKILL.md` —
"identify the metric, extract dimensions and filters, validate" — have a
code-level counterpart that a regression suite can pin by name. The golden
question suite (`tests/golden/questions.yaml`, `tests/test_golden_questions.py`)
drives this function with a phrasing and nothing else: the expected answer never
enters here.

What it does, in order:

  R5   row-level ask ("each order", "by session id")     -> refuse before lookup
  1.   time range     ("last 12 weeks", "from A to B")   -> last_N_weeks | A:B
  2.   dimensions     ("split by channel", "per market")  -> vocabulary, else slug
  3.   filters        ("for bank transfers", "in the uk", "where col = val")
  4.   metric         longest alias / display-name / name phrase found in the
                      question; exactly one metric wins, otherwise the stopword-
                      stripped remainder is handed to `kattfot_query.find_metric_file`,
                      which is the real R1 / R1-AMBIGUOUS / did-you-mean path
  5.   `kattfot_query.load_metric`, `validate_tier` (R4), `validate_request`
                      (R6 first, then R2, R3) — the helper's own functions, unchanged

Everything that *refuses* is the helper's code; this module only decides what to
hand it. The vocabularies below are the honest scope: a phrasing outside them
does not resolve, and the suite says so by name rather than by a pass rate.

Usage (a pre-pass the agent may consult; it never queries anything):

    python3 resolve_question.py "payment approval rate for bank transfers in the uk"
    -> {"metric": "payment_approval_rate", "dimensions": [], "filters": {...}, ...}

Exit 0 on a resolution, 1 on a refusal (the refusal is printed as JSON too).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kattfot_query as aq  # noqa: E402


# ---------------------------------------------------------------------------
# Vocabulary — the deterministic scope. Extend here, never by inference.
# ---------------------------------------------------------------------------

#: Words carrying no metric meaning; dropped before the R1 fallback so a miss
#: like "what is our revenue per visitor" reaches the helper as
#: `revenue_per_visitor` and gets a real did-you-mean, not a stopword soup.
STOPWORDS = frozenset("""
a an and are as at be been being by can could did do does for from give got has have
how i in is it its just last let me my of on or our over please s show tell that the
their there these this those to us was we week weekly weeks were what whats when
where which who will with would you your trend trending looking like currently
right now lately recent recently rate rates of
""".split())
# `rate` is a stopword on purpose: every registered name ends in it, so it adds
# nothing to a fuzzy miss and would pull every miss toward a tie.

#: Words that name the time grain, not a dimension. "by week" is how the agent's
#: own examples phrase the cohort grain; it must not become an R2 on `week`.
GRAIN_WORDS = frozenset({"week", "weeks", "cohort week", "cohort weeks", "weekly"})

#: Dimension phrase -> column. Anything not here is slugified as typed, so an
#: unknown "by device type" reaches `validate_request` as `device_type` and the
#: helper refuses it under R2 with the real allow-list.
DIMENSION_VOCAB = {
    "channel": "channel",
    "channels": "channel",
    "acquisition channel": "channel",
    "payment method": "payment_method",
    "payment methods": "payment_method",
    "payment type": "payment_method",
    "market": "market",
    "markets": "market",
    "category": "category",
    "categories": "category",
    "product category": "category",
    "checkout version": "checkout_version",
    "checkout versions": "checkout_version",
}

#: Filter value phrase -> (column, bound value). Longest phrase wins; a phrase
#: is consumed once it matches so "bank transfer" cannot also read as a bare
#: dimension. Values are the fixture catalog's own; see data/kattfot/sample.
FILTER_VOCAB = {
    "bank transfers": ("payment_method", "bank_transfer_review"),
    "bank transfer": ("payment_method", "bank_transfer_review"),
    "card payments": ("payment_method", "card_instant"),
    "cards": ("payment_method", "card_instant"),
    "card": ("payment_method", "card_instant"),
    "wallets": ("payment_method", "wallet"),
    "wallet": ("payment_method", "wallet"),
    "the uk": ("market", "uk"),
    "uk market": ("market", "uk"),
    "united kingdom": ("market", "uk"),
    "the us": ("market", "us"),
    "us market": ("market", "us"),
    "united states": ("market", "us"),
    "canada": ("market", "ca"),
    "canadian market": ("market", "ca"),
    "mobile app": ("channel", "mobile_app"),
    "the app": ("channel", "mobile_app"),
    "in app": ("channel", "mobile_app"),
    "on web": ("channel", "web"),
    "the web": ("channel", "web"),
    "web channel": ("channel", "web"),
    "social": ("channel", "social"),
    "marketplace": ("channel", "marketplace"),
    "apparel": ("category", "apparel"),
    "clothing": ("category", "apparel"),
    "beauty": ("category", "beauty"),
    "electronics": ("category", "electronics"),
    "home goods": ("category", "home"),
    "new checkout": ("checkout_version", "v2"),
    "v2 checkout": ("checkout_version", "v2"),
    "v2": ("checkout_version", "v2"),
    "legacy checkout": ("checkout_version", "v1-legacy"),
    "old checkout": ("checkout_version", "v1-legacy"),
    "v1": ("checkout_version", "v1-legacy"),
}

#: R5 — individual-level asks. A property of the question, not of the arguments
#: (`accuracy-tiers.md`), so it is decided before any lookup: "list every order"
#: is refused as R5 whether or not a metric is named alongside it.
R5_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"\b(each|every|individual|single)\s+(order|cart|session|customer|shopper|payment|shipment)s?\b",
        r"\blist\s+(the\s+|all\s+|every\s+|each\s+)?(orders|carts|sessions|customers|shoppers|payments|shipments)\b",
        r"\b(order|session|cart|customer|shipment|payment)\s+(id|ids|number|numbers)\b",
        r"\bper\s+(order|cart|session|customer|shopper)\b",
        r"\brow\s+level\b",
        r"\bwhich\s+(customers|shoppers|orders|carts|sessions)\b",
        r"\bwho\s+(bought|ordered|returned|paid|abandoned)\b",
    )
)

_DIM_RE = re.compile(
    r"\b(?:(?:split|broken\s+down|grouped|sliced|cut|segmented)\s+)?(?:by|per|across)\s+"
    r"(?P<dims>.+?)"
    r"(?=\s*\?|\s*,?\s+(?:for|in|on|over|last|since|between|from|where|with|filtered|"
    r"restricted|during|this|and\s+the|to|by|split|broken|grouped|sliced|cut|segmented|"
    r"what|whats|how|show|give|tell|which|is|are|was|were|did|do|does)\b|\s*,?\s*$)"
)
_EXPLICIT_FILTER_RE = re.compile(
    r"\b(?:where|filtered\s+to|filter(?:ed)?\s+on|restricted\s+to)\s+"
    r"(?P<col>[a-z][a-z0-9]*(?:[ _][a-z0-9]+){0,2}?)\s*(?:=|is|equals)\s*(?P<val>[a-z0-9_.-]+)"
)
_LAST_N_WEEKS_RE = re.compile(r"\b(?:over\s+|in\s+|for\s+)?(?:the\s+)?last\s+(\d+)\s+weeks?\b")
_FROM_TO_RE = re.compile(r"\b(?:from|between)\s+(\d{4}-\d{2}-\d{2})\s+(?:to|and|until|through)\s+(\d{4}-\d{2}-\d{2})\b")
_RULE_RE = re.compile(r"^ERROR \((?P<rule>R\d(?:-[A-Z]+)?)\)")
_AMBIGUOUS_LIST_RE = re.compile(r"about equally well: (?P<names>.+?)\. Refusing")
_DID_YOU_MEAN_RE = re.compile(r"Did you mean: (?P<names>.+?)\?")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Resolution:
    metric: str
    dimensions: list = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    time_range: str = ""  # "" = the metric's default_lookback_weeks
    matched_on: str = ""  # the alias / name phrase that identified the metric

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "dimensions": list(self.dimensions),
            "filters": dict(self.filters),
            "time_range": self.time_range,
            "matched_on": self.matched_on,
            "resolver": "deterministic alias matching (resolve_question.py), not an LLM",
        }


@dataclass
class Refusal:
    rule: str          # R1 | R1-AMBIGUOUS | R2 | R3 | R4 | R5 | R6
    message: str
    candidates: list = field(default_factory=list)  # R1-AMBIGUOUS readings / R1 did-you-mean

    def to_dict(self) -> dict:
        return {"refusal": self.rule, "candidates": list(self.candidates), "message": self.message}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, apostrophes dropped, punctuation other than `_ = , ?` and the
    hyphen in a date to one space. A hyphen between letters becomes a space, so
    "end-to-end" and "end to end" are one phrase; `2026-06-01` and `v1-legacy`
    keep theirs. Commas and question marks survive as their own tokens because
    they end a dimension phrase ("by market, what is ...")."""
    t = text.lower().replace("'", "").replace("’", "")
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)          # end-to-end -> end to end
    t = re.sub(r"[^a-z0-9_=,?-]+", " ", t)                 # keep _ = , ? and date hyphens
    t = re.sub(r"\s*([,?=])\s*", r" \1 ", t)               # punctuation as its own token
    return re.sub(r"\s+", " ", t).strip()


def load_catalog() -> list:
    """Every registered metric's matching surface: name, display_name, aliases.

    Same two roots, same precedence, as the helper itself (consumer dir first,
    bundled catalog second, consumer wins on a `name:` collision), so a consumer
    override's aliases are the ones that match.
    """
    seen: dict = {}
    for root in (aq.metrics_dir(), aq.bundled_metrics_dir()):
        if not root or not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            m = aq.FRONTMATTER_RE.match(path.read_text())
            if not m:
                continue
            try:
                fm = aq.yaml.safe_load(m.group(1)) or {}
            except aq.yaml.YAMLError:
                continue
            name = fm.get("name")
            if not name or name in seen:
                continue
            aliases = fm.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = []
            phrases = {normalize(name.replace("_", " "))}
            if fm.get("display_name"):
                phrases.add(normalize(str(fm["display_name"])))
            phrases.update(normalize(str(a)) for a in aliases if str(a).strip())
            seen[name] = {"name": name, "phrases": sorted(p for p in phrases if p)}
    return list(seen.values())


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _blank(text: str, span: tuple) -> str:
    return text[: span[0]] + " " * (span[1] - span[0]) + text[span[1]:]


def _slug(phrase: str) -> str:
    """A dimension phrase outside the vocabulary becomes the column the user
    typed: plural stripped, spaces to underscores. `address` keeps its double s
    so the R6 denylist still sees it."""
    p = phrase.strip()
    for lead in ("the ", "each ", "a "):
        if p.startswith(lead):
            p = p[len(lead):]
    if p in DIMENSION_VOCAB:
        return DIMENSION_VOCAB[p]
    if p.endswith("ies"):
        p = p[:-3] + "y"
    elif p.endswith("s") and not p.endswith("ss"):
        p = p[:-1]
    return DIMENSION_VOCAB.get(p, p.replace(" ", "_"))


def extract_time_range(text: str) -> tuple:
    m = _LAST_N_WEEKS_RE.search(text)
    if m:
        return f"last_{int(m.group(1))}_weeks", _blank(text, m.span())
    m = _FROM_TO_RE.search(text)
    if m:
        return f"{m.group(1)}:{m.group(2)}", _blank(text, m.span())
    return "", text


def extract_dimensions(text: str) -> tuple:
    dims: list = []
    for m in list(_DIM_RE.finditer(text)):
        raw = m.group("dims")
        for piece in re.split(r"\s+and\s+|\s*,\s*", raw):
            piece = piece.strip()
            if not piece or piece in GRAIN_WORDS:
                continue
            col = _slug(piece)
            if col in GRAIN_WORDS or not col:
                continue
            if col not in dims:
                dims.append(col)
        text = _blank(text, m.span())
    return dims, text


def extract_filters(text: str) -> tuple:
    filters: dict = {}
    for m in list(_EXPLICIT_FILTER_RE.finditer(text)):
        col = m.group("col").strip().replace(" ", "_")
        filters.setdefault(col, m.group("val"))
        text = _blank(text, m.span())
    for phrase in sorted(FILTER_VOCAB, key=len, reverse=True):
        m = re.search(rf"\b{re.escape(phrase)}\b", text)
        if not m:
            continue
        col, val = FILTER_VOCAB[phrase]
        filters.setdefault(col, val)
        text = _blank(text, m.span())
    return filters, text


def match_metric(text: str, catalog: list) -> tuple:
    """(name, phrase) when exactly one metric owns the longest matching phrase
    and no other metric matches at all; (None, "") otherwise.

    Two metrics both matching — "approval rate and decline rate" — is NOT
    resolved by picking the longer phrase. That would be the silent top-1
    commitment R1-AMBIGUOUS exists to refuse, so it falls through to the
    helper's own ambiguity path.
    """
    hits = []
    for entry in catalog:
        best = ""
        for phrase in entry["phrases"]:
            if len(phrase) > len(best) and re.search(rf"\b{re.escape(phrase)}\b", text):
                best = phrase
        if best:
            hits.append((entry["name"], best))
    if len(hits) == 1:
        return hits[0]
    return None, ""


def metric_phrase(text: str) -> str:
    """What is left of the question once dimensions, filters, the time range and
    stopwords are gone, as a snake_case token for the helper's R1 path."""
    tokens = [
        t for t in normalize(text).split()
        if t not in STOPWORDS and re.fullmatch(r"[a-z0-9_-]+", t)
    ]
    return "_".join(tokens)


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def _refusal_from(exc: SystemExit) -> Refusal:
    msg = str(exc.code if exc.code is not None else exc)
    m = _RULE_RE.match(msg)
    rule = m.group("rule") if m else "ERROR"
    candidates: list = []
    for rx in (_AMBIGUOUS_LIST_RE, _DID_YOU_MEAN_RE):
        lm = rx.search(msg)
        if lm:
            candidates = [c.strip() for c in lm.group("names").split(",") if c.strip()]
            break
    return Refusal(rule=rule, message=msg, candidates=candidates)


def resolve(question: str, catalog: list | None = None):
    """Map one natural-language phrasing to a `Resolution` or a `Refusal`.

    Pure with respect to the question: nothing about the expected answer is an
    input. The catalog is read from the same roots the helper uses; pass one in
    only to avoid re-reading it per call.
    """
    text = normalize(question)
    for rx in R5_PATTERNS:
        if rx.search(text):
            return Refusal(
                rule="R5",
                message=(
                    f"ERROR (R5): '{question}' asks for individual-level rows; this "
                    f"path serves aggregates only. Rows are available in the "
                    f"exploratory lane (kattfot:explore), stamped T3, with your "
                    f"explicit confirmation."
                ),
            )

    time_range, rest = extract_time_range(text)
    dims, rest = extract_dimensions(rest)
    filters, rest = extract_filters(rest)

    cat = catalog if catalog is not None else load_catalog()
    name, matched = match_metric(text, cat)
    try:
        if name is None:
            # The helper's own R1 path (`find_metric_file`, via `load_metric`):
            # an exact name resolves, a near-tie refuses R1-AMBIGUOUS, a miss
            # refuses R1 with a did-you-mean.
            matched = metric_phrase(rest)
            metric = aq.load_metric(matched)
        else:
            metric = aq.load_metric(name)
        aq.validate_tier(metric)
        aq.validate_request(metric, dims, filters)
    except SystemExit as exc:
        return _refusal_from(exc)

    return Resolution(
        metric=metric["name"],
        dimensions=dims,
        filters=filters,
        time_range=time_range,
        matched_on=matched,
    )


def main(argv: list | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        sys.stderr.write("usage: resolve_question.py \"<natural-language question>\"\n")
        return 2
    out = resolve(" ".join(args))
    print(json.dumps(out.to_dict(), indent=2))
    return 0 if isinstance(out, Resolution) else 1


if __name__ == "__main__":
    sys.exit(main())
