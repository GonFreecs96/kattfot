# What is assay? — What It Is, Who It's For, and Why It Matters

*A plain-language introduction. If you want the control-by-control detail
instead, go straight to [`governance-model.md`](governance-model.md).*

## The one-sentence version

`assay` is a working template that shows how to let an AI agent answer plain-
English questions about a business's own data — and get back a number that a
stranger, months later, can still tell whether to trust.

## The problem it solves

Ask an LLM-backed agent to "pull the conversion rate" and it will happily
write you a query. That query will run, return a plausible-looking number,
and be wrong in ways that are invisible at the moment it is produced —
because it counted the wrong denominator, or it included cohorts too young to
have finished converting, or it quietly used a service-account credential
that bypasses every access control the warehouse team thought it had.

None of those failures are the model being careless. They are what happens
when an agent is given the same tool a human analyst has — SQL access — with
none of the judgment an experienced analyst has accumulated about *this*
warehouse's particular landmines. A text-to-SQL agent is a brilliant new hire
on day one: fluent in syntax, ignorant of the business.

The conventional answer is "review the query before it runs." That does not
scale, and it puts a human back in the loop for exactly the class of question
self-serve analytics was supposed to remove. `assay`'s answer is different:
constrain what the agent is *able* to do, in code, before it ever writes a
query — an allow-list instead of a review, a refusal instead of a warning,
a receipt instead of a promise.

## Who this is for

`assay` is a template, not a product — there is no live install to describe.
Three groups get direct value from reading it:

**1. A team building agentic analytics over their own warehouse.** If your
plan is "wire an agent to BigQuery and let people ask questions," this is the
worked example of what that requires beyond the wiring: an identity model
that survives an audit, an allow-list that turns "off-limits" into a clean
refusal instead of a wrong answer, and a way to prove six months later that
a number still means what it claimed to. Fork it, replace the `checkout`
module with your own domain (see [`adding-a-module.md`](adding-a-module.md)),
and you inherit the governance engine unmodified.

**2. Someone evaluating whether "governed analytics" is real or a slide.**
Every mechanism described here is checked by a test, not merely documented.
`scripts/demo_skills.py` runs all sixteen functional skills in an environment
scrubbed of credentials and fails if any of them cannot; `tests/test_pii_refusal.py`
asserts a personal column is refused even when a metric's own frontmatter
allow-lists it by mistake. Read the code, not just this doc — or, to see the
output without cloning anything, go straight to
[`examples/`](../examples/): a real governed artifact, its chart, and its
PDF export, side by side.

**3. Anyone comparing this to a plain BI dashboard.** A dashboard shows you
numbers someone already vetted. An agent that can run *new* queries on
request needs a different kind of safety: not "is this dashboard correct"
but "can this agent produce an incorrect one, and would anyone notice." That
is the question this template is actually answering.

## A concrete picture

Picture a fictional D2C retailer's checkout team. A product manager wants to
know whether a payment-flow change moved cart abandonment. Without
governance, an agent given warehouse access has two ways to get this wrong
quietly: it can measure the wrong thing (an order-grain rate, which is blind
to the abandoned carts that never became orders — the single biggest leak in
the funnel), or it can measure the right thing over the wrong window (last
week's cohort, whose return-window outcome will not be knowable for another
month, read as though it already were).

`assay`'s answer to the first is a metric definition with a named
denominator (`carts_created`, not `orders_confirmed`) and a caveat that says
why. Its answer to the second is a maturity cutoff withheld **per stage** —
so the cart-to-checkout-start rate stays fresh through last week while the
return-window rate honestly waits for cohorts that have actually had 30 days
to send something back. Neither answer is a smarter prompt. Both are code
that runs before the query does, and both are why the artifact this produces
carries a `maturity_cutoff` field a reviewer can check rather than a
narrative they have to trust.

## What makes it trustworthy

Three design choices make it defensible to hand this kind of access to an
agent at all.

**It runs as the person asking, not as a shared key.** Every warehouse query
authenticates via the individual human's own browser login. A service-account
or impersonated credential is refused outright — not flagged, refused — so
every query is attributable to a real person and nobody's access outlives
their departure from the team.

**It cannot invent a query.** For a governed question, the agent selects
from a pre-vetted, allow-listed metric definition rather than writing SQL
from scratch. Ask for something outside the allow-list and you get a clean
refusal naming the rule and the specific input, not an agent's best guess at
what you might have meant.

**Every answer carries a receipt.** A content-addressed manifest —
fingerprints of the metric definition, the exact query, and the result — ships
with every artifact. `assay:verify` reads that receipt back offline, months
later, and tells you whether the definition has since changed underneath the
number you are holding.

## Where it is today

`assay` is Phase 4 complete (plus a 2026-08 research pass): all sixteen functional skills run, offline,
against committed synthetic fixtures, and that claim is checked by a script
rather than asserted in this paragraph. The research pass promoted
`funnel-nowcast` from outline to functional — a cohort-maturation now-cast
built on Mack's distribution-free chain ladder, which *estimates* every
development factor from the spine at run time rather than baking in a fitted
curve, so it can exist honestly in a template whose fixtures are synthetic
(the numbers describe the generator and are watermarked; the mechanism is what
a real deployment runs). Three more skills — a release scorecard with a
causal verdict, a customer-grain journey analysis, and a wiki-publishing path
— remain documented outlines, because giving them honest numbers needs
parameters (causal priors, control-series selection) that are judgment calls
against a *real* book and, unlike development factors, cannot be estimated from
the spine. Fitting them to this template's seeded fixture generator would
produce a verdict that describes the generator, not a business.

The honest summary: the governance engine is complete, the checkout module is
a full worked example end to end, and the remaining three skills are a
deliberate, stated gap rather than an oversight — see the module registry's
`excluded:` entries for the specific reason behind each one. The research
pass's full evidence base — what the literature validated, critiqued, and
asked the template to build — is in
[`reference/research-review.md`](reference/research-review.md). Alongside the
code sit the documentation suite under [`docs/`](.) and real committed output
under [`examples/`](../examples/).

## The case, in a sentence

An agent with unconstrained warehouse access is not a faster analyst, it is
an unaudited one — and the fix is not a smarter prompt, it is code that
refuses cleanly, runs as a real person, and leaves a receipt. That is what
this template is a worked example of.
