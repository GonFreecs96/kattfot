# The research behind assay — what the literature validates, critiques, and asked us to build

*A grounding review, written 2026-08. Every mechanism in `assay` makes a claim
about how governed agentic analytics should work; this document checks those
claims against the peer-reviewed and heavily-cited literature in five areas —
text-to-SQL reliability, LLM-agent security, semantic/metrics layers, data
provenance, and the statistics of maturing cohort data — and records what that
literature validated, where it found `assay` wanting, and which of its
suggestions were implemented in response. The "IMPROVE → built" items are
traceable to specific commits; the "IMPROVE → roadmap" items are deployment
infrastructure a template cannot honestly ship. Full citations are at the end.*

This is a reference document, not marketing: where the literature says a
control is weaker than its reputation, that is recorded here in the same voice
as [`governance-model.md`](../governance-model.md)'s "what this does not
guarantee" sections.

---

## 1. The core thesis is the measured consensus, not a slogan

`assay`'s one-sentence claim — *a raw text-to-SQL agent produces
plausible-but-wrong numbers, so the fix is to constrain what it can do in code
rather than review what it wrote* — is now the empirical finding of the field,
replicated across independent benchmarks.

- The same agent framework that scores **91.2%** on the academic Spider 1.0
  benchmark scores **21.3%** on Spider 2.0's real enterprise warehouse
  workloads (ICLR 2025 oral); GPT-4o falls from 86.6% to about 10%. The
  single largest error class is **wrong schema linking** (27.6% of failures) —
  the model choosing the wrong tables and columns among thousands. An
  allow-listed metric registry removes that error class *by construction*: the
  agent selects a vetted definition, it does not go hunting for columns.
- On a real insurance schema, GPT-4 answering natural-language questions
  directly scored **16.7% overall and 0% on metric/KPI questions** — and
  **54.2%** when the same model queried through a semantic/knowledge-graph
  layer (Sequeda et al., SIGMOD workshop 2024). This is close to a direct
  experimental test of `assay`'s design: the governed definition layer is the
  active ingredient, not the model.
- The intervention has been isolated to the *document*: in a paired benchmark
  (Rumiantsau & Fokeev, 2026), a **4 KB markdown semantic document** — the
  same medium as `assay`'s metric files — accounted for essentially all of the
  significant accuracy variance, while model choice within a tier accounted
  for none.
- When wrong answers carry a real cost, **abstention is the optimal policy**:
  under TrustSQL's penalty scoring most models score below a trivial
  "abstain on everything" baseline (KAIST, 2024). `assay`'s hard refusals
  (off-list metric, service-account credential, PII column) are the
  code-enforced version of the policy this literature says wins.

**Framing caveat we hold ourselves to.** The Spider 2.0 "21%" figure is from
late 2024; by mid-2026 heavily-engineered vendor agent stacks report far
higher on the public leaderboards. So the argument rests on the *structure* —
silent wrong answers, the cost of being wrong, schema-linking as the dominant
error — not on "LLMs can only do 21%," which is a moving number. The
essay and any article built on this review should do the same.

---

## 2. Security: the literature validates the architecture and named the gaps

The consensus across the 2023–2025 agent-security literature is stark and
directly supportive: **every prompt-level or model-level defense published so
far falls to adaptive attack; only architectural constraints hold** (the
adaptive-attack papers breaking StruQ/SecAlign-class defenses; the
"Design Patterns for Securing LLM Agents" consensus paper; Google's
defense-in-depth white paper putting deterministic runtime policy as the
load-bearing layer). Mapped to `assay`:

**Validated.**
- *Allow-listed metrics instead of free-form SQL* is the Action-Selector /
  Plan-Then-Execute pattern — constraining the action set is what the
  design-patterns paper says makes an agent's safety analyzable at all.
- *Code-enforced hooks instead of prompt guardrails* sits on the right side of
  the adaptive-attack line.
- *Per-user OAuth with service accounts refused in code* is exactly the
  authenticated-delegation and accountability-chain argument (South et al.,
  2025; OWASP Agentic guidance). This review's first build item strengthened
  precisely this: attribution now records the acting software agent alongside
  the human principal (W3C PROV `actedOnBehalfOf`).
- *PII denylist ordered before the allow-list* is standard default-deny
  fail-safe design.

**Critiqued, and what we did about it.**
- *Blocklist, not sandbox.* The access hook enumerates bad paths and so fails
  *open* on an unknown one, where the systems-security bar (IsolateGPT, NDSS
  2025; Anthropic's sandbox-runtime) is a sandbox that fails *closed*. This is
  a genuine limitation the template states plainly; closing it is an OS-level
  sandbox with a network egress allow-list — deployment infrastructure, so it
  is on the roadmap below, not built into a template whose promise is
  `git clone && pytest`.
- *No data-flow integrity once data is in context.* CaMeL's result is that the
  dangerous flow is untrusted *data* steering subsequent *actions*.
  `assay` constrains which actions exist — which is what shrinks the blast
  radius — but a poisoned warehouse string still enters context. The new
  **adversarial injection suite** (`tests/test_adversarial_injection.py`)
  pins the part `assay` genuinely defends: untrusted data cannot select a
  non-allow-listed action (R2/R3/R6), and a filter value is bound as a
  parameter, never interpolated.
- *The exfiltration leg is unaddressed.* Willison's "lethal trifecta" needs
  all three of private data, untrusted content, and an egress channel;
  `assay` removes the second structurally and narrows the first, but does not
  close the third. Rather than imply otherwise, the adversarial suite asserts
  the gap as a tracked test that will flip to asserting a block if egress
  control is ever added.
- *No adversarial regression suite* was the field's baseline expectation
  (AgentDojo, InjecAgent). The new suite is the first installment; a full
  attack-success-rate harness is on the roadmap.

---

## 3. Provenance: standard-aligned, with the audit log upgraded to tamper-evident

`assay`'s manifest-per-artifact, graded accuracy tiers, and offline drift check
each match a heavily-cited consensus pattern — producer-written artifact
documentation (Datasheets for Datasets; Model Cards), graded trust badges (ACM
artifact badging), and hash-based reproducibility tooling (DVC/Pachyderm;
Peng's minimum reproducibility standard). The mechanisms map cleanly onto W3C
PROV: a metric definition is a `prov:Plan`, a query run a `prov:Activity`, a
result a content-addressed `prov:Entity`, the manifest a `prov:Bundle`.

**Critiqued, and what we did about it.**
- *A flat JSONL audit log is below the tamper-evidence bar.* Crosby & Wallach
  (USENIX Security 2009) define tamper evidence against a logger that is itself
  untrusted; an append-only *convention* on a mutable file detects nothing.
  **Built:** the audit log is now **hash-chained** over its exact line bytes
  (`_shared/audit_chain.py`), with a committed-head sidecar and a
  `verify --audit-log` replay that detects any edit, reorder, insertion, or
  truncation. This is the first step Crosby & Wallach prescribe; a full Merkle
  history tree with external witnesses (RFC 6962 style) is the roadmap
  continuation, worthwhile only once multiple parties audit.
- *Missing agent attribution.* For agentic analytics, PROV's
  Agent + `actedOnBehalfOf` is the governance-relevant field, and a bespoke
  manifest that omitted it missed what makes the setting distinctive.
  **Built:** every artifact and audit record now carries the acting software
  agent (skill + plugin version) next to the human principal.
- *Bespoke vocabulary; query-granularity only; brittle result hashing.* These
  remain: a PROV-O / OpenLineage exporter (for interoperability with generic
  lineage tooling) and lightweight why-provenance (which input partitions fed
  an aggregate) are roadmap items, and result hashing already canonicalizes
  row order and numeric format in `manifest.py`.

---

## 4. The maturity mechanism is textbook — and the outline it blocked is now built

`assay`'s per-stage maturity is an *exact* instance of a problem three
literatures already solved. The cohort-by-development-week table is a **run-off
triangle** (actuarial claims reserving); the withheld quantity is **IBNR /
occurred-but-not-reported** (Lawless 1994); the whole task is the epidemiological
**nowcast**. Chain ladder is algebraically the delay-distribution correction and
is the *baseline* method the nowcasting community measures newer models against.

**Validated.**
- Per-stage horizons (rather than gating the whole funnel on the slowest stage)
  is the field-standard decomposition — nobody reserves every line of business
  on the slowest one.
- Marking the shipped lags `illustrative: true / calibrated_at: null` is the
  correct epistemic posture: the only preregistered real-time comparison of
  nowcasting methods (Wolffram et al., 2023) found **mis-specified maximum
  delays were the single biggest driver of systematic bias**, ahead of model
  choice.

**Critiqued, and what we did about it.**
- *Hand-authored lags are an assumption wearing a measurement's clothes.*
  **Built:** `calibrate_maturity.py` measures the empirical settle curve per
  stage from the spine, proposes calibrated lags at chosen percentiles,
  reports the tail mass a too-short lag silently drops, and can rewrite the
  spec block — with a truncation guard for exactly the mis-specified-max-delay
  failure Wolffram et al. flagged. On fixtures it keeps the illustrative flag,
  because a measured *generator* is not a measured business.
- *Hard-cutoff withholding is statistically wasteful — the mature answer is a
  now-cast with honest intervals.* This critique was really "the unimplemented
  skill is the statistically load-bearing part." **Built:** `funnel-nowcast`
  now ships. The Phase-4 objection (hand-authored now-cast parameters would fit
  the fixture generator) dissolved under **Mack's distribution-free chain
  ladder** (Mack 1993), which *estimates* every development factor and variance
  from the triangle at run time — nothing is authored. Two funnel-specific
  ports the actuarial literature does not need: ultimates are **capped at
  cohort size** (a funnel has a ceiling claims do not) and a **level prior**
  handles cohorts still inside a pure delay (no confident zeros).
- *Model-based intervals run too narrow* (Wolffram et al.'s strongest finding).
  **Built:** `funnel-nowcast --backtest` replays settled cohorts at every age
  using only then-observable data and reports empirical coverage next to
  nominal — on the committed fixtures it reproduces the undercoverage finding
  (~56% vs 80% nominal) and warns accordingly. Conformal inflation to the
  backtest's horizon-specific error quantiles is the documented next step.
- *release-scorecard stays an outline.* Its causal parameters (BSTS priors,
  control-series selection, pre/post windows) genuinely cannot be estimated
  from the spine the way development factors can, and a causal verdict over
  generated data would measure the generator. The registry records this, and
  it is unchanged by this pass.

---

## 5. What was built this pass (traceable), and what is deliberately roadmap

**Built into the template.**

| Change | Literature driver | Where |
|---|---|---|
| Hash-chained, replay-verifiable audit log | Crosby & Wallach 2009; RFC 6962 | `_shared/audit_chain.py`, `verify --audit-log` |
| Agent + on-behalf-of attribution | W3C PROV; South et al. 2025 | manifest + audit record |
| Maturity calibration from measured settle curves | Wolffram et al. 2023; Lawless 1994 | `stage-timing/scripts/calibrate_maturity.py` |
| `funnel-nowcast` (Mack chain ladder, capped, level prior) | Mack 1993; England & Verrall 2002 | `funnel-nowcast/scripts/funnel_nowcast.py` |
| Self-scoring backtest (empirical vs nominal coverage) | Wolffram et al. 2023 | `funnel_nowcast.py --backtest` |
| Ambiguity refusal (name every reading, never top-1) | AmbiQT (EMNLP 2023); DTE (ACL 2023) | `assay_query.py` R1-AMBIGUOUS |
| Indirect-injection adversarial suite | Greshake 2023; CaMeL 2025; Willison | `tests/test_adversarial_injection.py` |

**Deliberately roadmap, not built** — deployment infrastructure a template
cannot honestly ship, each with its literature anchor:

- **OS-level sandbox with a network egress allow-list**, replacing the
  blocklist hook and closing the exfiltration leg (Anthropic sandbox-runtime;
  IsolateGPT). This is the highest-value security upgrade and the one most
  clearly out of a template's scope.
- **Warehouse-side enforcement** — BigQuery policy tags and row-level security
  bound to the end-user identity — so the reference monitor is the data
  platform, not the client (the durable backstop `governance-model.md`
  already names).
- **PROV-O / OpenLineage export** for interoperability with generic lineage
  tooling (Marquez), and lightweight why-provenance for drift diagnosis.
- **A full adversarial eval harness** tracking attack-success-rate and
  utility-under-attack in CI (AgentDojo, InjecAgent).
- **A metrics-layer maturity set** the semantic-layer literature converges on
  but `assay` only partially has: structured CI validation of every metric's
  SQL against fixtures (test-suite verification, Zhong et al. 2020),
  additivity typing (Lenz & Shoshani 1997), a conformed-dimension registry,
  usage-based deprecation, and a golden-question regression suite
  (Minerva; dbt-llm-sl-bench).

---

## Sources

**Text-to-SQL reliability.** Yu et al., *Spider* (EMNLP 2018); Lei et al.,
*Spider 2.0* (ICLR 2025, oral); Li et al., *BIRD* (NeurIPS 2023); Zhong et al.,
*Semantic Evaluation with Distilled Test Suites* (EMNLP 2020); Chang et al.,
*Dr.Spider* (ICLR 2023); Lee et al., *TrustSQL* (2024); Lee et al., *EHRSQL*
(NeurIPS 2022); Wang et al., *Know What I Don't Know* / DTE (ACL Findings
2023); Bhaskar et al., *AmbiQT* (EMNLP 2023); Scholak et al., *PICARD*
(EMNLP 2021).

**Agent security.** Greshake et al., *Indirect Prompt Injection* (AISec 2023);
Debenedetti et al., *CaMeL* (2025) and *AgentDojo* (NeurIPS 2024);
Beurer-Kellner et al., *Design Patterns for Securing LLM Agents* (2025); Wu et
al., *IsolateGPT* (NDSS 2025); Zhan et al., *InjecAgent* (ACL Findings 2024);
Willison, *The Lethal Trifecta* (2025); OWASP *Top 10 for LLM Applications
2025* and *Agentic AI Threats and Mitigations*; South et al., *Authenticated
Delegation and Authorized AI Agents* (2025); Díaz et al., *Google's Approach to
AI Agent Security* (2025); Anthropic, *Claude Code sandboxing* /
sandbox-runtime (2025); NIST *AI RMF* (2023) and *Generative AI Profile* (2024).

**Semantic / metrics layers.** Sequeda, Allemang & Jacob, *Knowledge Graphs and
LLM Accuracy on Enterprise SQL* (SIGMOD workshop 2024); Allemang & Sequeda,
*Ontologies to the Rescue* / OBQC (2024); Rumiantsau & Fokeev, *Semantic Layers
for Reliable LLM Analytics* (2026); dbt Labs, *Semantic Layer vs. Text-to-SQL*
(2026); Snowflake, *Cortex Analyst* engineering notes; Chang et al., *Airbnb
Minerva* (2021); Uber *uMetric* (2021); Stancil, *The Missing Piece of the
Modern Data Stack* (2021); Kimball & Ross, *The Data Warehouse Toolkit* (2013);
Lenz & Shoshani, *Summarizability in OLAP* (SSDBM 1997); Xiao et al.,
*Ontology-Based Data Access: A Survey* (IJCAI 2018).

**Provenance & reproducibility.** W3C *PROV-DM* / *PROV-O* (2013); Buneman et
al., *Why and Where* (ICDT 2001); Green et al., *Provenance Semirings* (PODS
2007); Cheney et al., *Provenance in Databases* (FnT-DB 2009); Freire et al.,
*Provenance for Computational Tasks* (CiSE 2008); Peng, *Reproducible Research*
(Science 2011); ACM *Artifact Review and Badging*; Gebru et al., *Datasheets
for Datasets* (2021); Mitchell et al., *Model Cards* (FAT* 2019); Crosby &
Wallach, *Efficient Data Structures for Tamper-Evident Logging* (USENIX
Security 2009); RFC 6962 *Certificate Transparency*; OpenLineage / Marquez.

**Maturing-cohort statistics.** Mack, *Distribution-Free Chain Ladder* (ASTIN
Bulletin 1993); Renshaw & Verrall, *A Stochastic Model Underlying the Chain
Ladder* (1998); England & Verrall, *Stochastic Claims Reserving* (2002);
Lawless, *Adjustments for Reporting Delays* (1994); Höhle & an der Heiden,
*Bayesian Nowcasting* (Biometrics 2014); Günther et al., *Nowcasting COVID-19
in Bavaria* (2021); Wolffram et al., *Collaborative Nowcasting* (PLOS Comp Bio
2023); Kaplan & Meier (JASA 1958); Chapelle, *Modeling Delayed Feedback in
Display Advertising* (KDD 2014); Brodersen et al., *Inferring Causal Impact*
(Annals of Applied Statistics 2015); Roth et al., *What's Trending in
Difference-in-Differences* (2023).
