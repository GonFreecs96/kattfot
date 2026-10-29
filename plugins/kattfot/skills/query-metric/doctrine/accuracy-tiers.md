# Accuracy tiers and refusal rules

The canonical source for what a governed number *claims about itself*, and for
when this runtime declines to produce one. `kattfot:query-metric` reads this file
(a consumer's own copy at `context/kattfot/accuracy-tiers.md` wins if present) and
treats it as the definition of R1–R6.

Copy it, edit the tier language to match your organisation's review reality, and
leave the refusal rules alone unless you are prepared to defend the change — they
are the part enforced in code.

## Why tiers exist

"What's our conversion rate?" has several correct answers depending on who is
asking and what they will do with it. A number good enough to prioritise next
quarter's roadmap is not good enough to file with a regulator, and pretending
otherwise is how analytics loses credibility — not through wrong arithmetic, but
through a right number used at the wrong altitude.

A tier is a **claim about the review a number has survived**, not a confidence
interval. It travels with the number into every artifact, chart, and PDF.

| Tier | Name | What it means | Who signs off | Served here |
|---|---|---|---|---|
| **T1** | Regulator-grade | Reconciles to the system of record to the cent. Reproducible on demand, with a documented control. | Finance / compliance, on a named cadence | **No** |
| **T2** | Operational | Decision-grade for product and process discussions. Definition is reviewed and version-pinned; source view is curated; known caveats are enumerated. | The metric's named owner | **Yes** |
| **T3** | Exploratory | Ad-hoc. Answers a question once. No stable definition, no review, no promise it means the same thing next week. | Nobody | **Exploratory lane only** (`kattfot:explore`) — never as a governed metric |

**The governed metric runtime serves T2 only.** That is the whole design: T1
must come from the system of record, because a governed convenience layer
cannot inherit a control it does not implement; T3 is served — but only through
the exploratory lane, where its provisionality is stamped on every result. A
metric that matters enough to be asked twice should be promoted to T2 with a
reviewed definition (`kattfot:promote` scaffolds the draft) — and then it is
served here.

## The exploratory lane

`kattfot:explore` is where a genuinely exploratory question goes instead of
being forced through a T2 metric it does not fit. The lane relaxes the
*allow-lists*, never the *account*:

- **Kept, unchanged:** per-user identity on the warehouse path, the R6 PII
  refusal (hardened, even — every identifier in the statement is scanned, not
  just dimensions and filters), the hash-chained audit log, a manifest receipt
  on every result, and the fixture watermark.
- **Relaxed, by design:** metric/dimension/filter allow-lists (replaced by a
  fail-closed register of explorable surfaces, `config/exploration.yaml`),
  the aggregate-only rule R5 (row-level output is available here — an
  individual row still cannot carry a tier, which is exactly why everything
  in this lane is stamped T3), and maturity cutoffs (an ad-hoc window lives
  inside the SQL; the T3 stamp is the caveat).
- **Added:** read-only single-SELECT enforcement and, on BigQuery, a
  per-query scan cap (`defaults.max_bytes_billed`).

The lane switch is **refer-then-confirm**: a governed refusal that the lane
might serve carries a machine-readable `EXPLORABLE` line, and the agent may
proceed only after telling the user the result will be T3 and getting an
explicit yes. Never as a silent fallback.

`tests/test_metric_catalog.py` enforces that every bundled metric is T2, so the
catalog cannot drift into serving something it has not earned.

## The refusal rules

A refusal is a feature. Each one states which rule fired, names the specific
input that caused it, and shows the allow-list so the caller can see what *is*
available. None of them proposes a workaround that violates the same rule, and
none of them writes an output file.

| Rule | Trigger | Enforced in |
|---|---|---|
| **R1** | The question does not match a registered metric | code (`find_metric_file`) — with a nearest-match suggestion |
| **R1-AMBIGUOUS** | The question matches several registered metrics about equally well | code (`ambiguous_candidates`) — every reading named, none chosen |
| **R2** | A requested dimension is not in the metric's `allowed_dimensions` | code (`validate_request`) |
| **R3** | A requested filter column is not in the metric's `allowed_filters` | code (`validate_request`) |
| **R4** | The metric's tier is not served (T1 or T3) | code (`validate_tier`) + agent |
| **R5** | The request is for individual-level rows ("show me each order") | agent |
| **R6** | A requested dimension or filter is personal PII | code (`_shared/pii.py`) — **hard, no override** |

### On R6 specifically

R6 is checked **before** the allow-lists, not after. That ordering is the point: a
metric author who allow-lists a PII column by mistake has not thereby authorized
it. The denylist covers names, contact details, government IDs, financial account
numbers, health records, dates of birth, and street addresses; it explicitly
allows opaque join keys (`customer_token`, `order_id`, `payment_token`) and
aggregate geography (`market`, `region`, `city`, `postal_code`) so real analysis
keeps working.

Two honest limits on that guard, worth knowing before treating it as complete:

1. It is **defense in depth inside the vetted helpers only.** Reach the warehouse
   any other way and it has no effect. What makes it more than advisory is the
   `PreToolUse` / `beforeShellExecution` hook, which denies every other access
   path — see `plugins/kattfot/hooks/`.
2. The durable, tool-independent backstop is **column-level security in the
   warehouse itself** (policy tags). Confirm with whoever owns your data platform
   whether that is configured. An in-code denylist is a good seatbelt; it is not
   a substitute for the column simply not being readable.

### On R5

Aggregate-only is not a privacy flourish, it is what keeps the tier meaningful.
An individual row cannot carry a caveat, cannot be reconciled, and cannot be
re-derived from a definition — so it cannot be T2 by construction. Requests for
row-level output are refused **on the governed path** and referred to the
exploratory lane (`kattfot:explore`), which serves rows precisely because
everything it serves is stamped T3 — the row does not pretend to a tier it
cannot carry. The referral needs the user's explicit confirmation, PII columns
stay refused (R6 holds in both lanes), and truly individual data access —
"show me this customer" — still belongs to whatever process your organisation
uses for it, not to an analytics lane in either tier.

## What a T2 metric owes its reader

Every metric definition in the catalog carries, in frontmatter and prose:

- a **named owner** — who to ask when the definition looks wrong (see
  `ownership-governance.md`);
- an explicit **numerator and denominator**, as columns, not as prose;
- a **maturity stage**, so the number is withheld until it has settled;
- **caveats**, enumerated and reproduced verbatim in every artifact — including
  the unflattering ones. A caveat a reader has to discover for themselves was a
  caveat you were hiding.
