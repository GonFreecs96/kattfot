# Ontologies, Knowledge Graphs, Semantic Layers, and the Rise of Agentic Analytics

*Background reading for anyone extending `kattfot`'s governance model. Ported
from an internal research brief, generalized, and re-illustrated against
this template's own e-commerce checkout domain rather than the original's.
The external research cited (benchmarks, standards bodies, publication
dates) is real and unrelated to any private system; only the worked
examples have been swapped for `kattfot`'s own entities (`checkout_session`,
`order`, `payment`, `customer`, `shipment`) so they read consistently with
the rest of this documentation suite.*

## TL;DR
- **Semantic layers and ontologies solve different problems, and AI
  analytics agents need both: a semantic layer governs *measures* (how "cart
  conversion rate" is calculated), while an ontology encodes *meaning* (what
  a checkout session, an order, or a return actually is and how they
  relate). The single most important finding for governed-analytics work is
  that text-to-SQL agents fail not on syntax but on context — and the fix is
  to make business meaning machine-readable, starting cheaply with
  ontology-lite enrichment of an existing warehouse before ever standing up
  a graph database.**
- **The evidence is now quantitative: in a widely-cited insurance-domain
  benchmark, GPT-4 answering natural-language questions directly against a
  SQL schema scored 16.7% accuracy and 0% on schema-intensive questions;
  backing the same LLM with a knowledge graph raised accuracy to 54.2% (~3x).
  The domain in that benchmark was insurance, but the structural lesson —
  that schema-intensive questions are where an LLM most needs explicit
  relationships rather than a raw schema — transfers directly to any
  warehouse with more than a handful of joined tables, `kattfot`'s included.**
- **You do not need to "boil the ocean." A workable sequence for a project
  like this one is: (1) governed metrics exposed to agents via an allow-list
  and a manifest (which is what `query-metric` already is); (2)
  ontology-lite relationship + business-rule context alongside the metric
  definitions (which is what the entity registry and each module's
  `LINEAGE.md` already are); (3) selective property-graph use only for
  genuinely relationship-heavy problems (fraud rings across payment
  attempts, a customer's cross-order behavior); (4) a formal ontology/
  knowledge graph only where cross-system reasoning and integration justify
  it.**

## Key Findings

1. **The vocabulary is genuinely confused, and the confusion is expensive.**
   "Semantic layer" has two meanings: the BI/metrics meaning (a governed
   metric catalog like `query-metric`'s — SQL abstraction and metric
   governance) and the formal-semantics meaning (RDF/OWL, machine-
   interpretable meaning). Jessica Talisman's framing is the cleanest
   anchor: *"A semantic layer is for lookup, an ontology is for context and
   reasoning."* Michel Tricot's (Airbyte) complement: *"Your schema
   describes how data is stored. Ontology describes how your business
   actually works."*

2. **Knowledge graphs make relationships first-class objects.** In a
   relational warehouse, the relationship "this shipment fulfills this
   order" is implicit — reconstructed at query time through a `JOIN`. In a
   knowledge graph, that relationship is a stored, queryable, typed edge.
   This is why graphs outperform on multi-hop, relationship-centric
   questions.

3. **Two graph paradigms, two philosophies.** RDF/OWL (W3C standards,
   SPARQL, formal reasoning) optimizes for meaning, interoperability, and
   inference. Labeled property graphs (Neo4j, Cypher/GQL) optimize for
   traversal performance and developer ergonomics. Choosing wrong is, in
   Talisman's words, "a costly architectural mistake."

4. **GraphRAG beats vector RAG on relational/causal questions** but is more
   expensive to build and maintain; LightRAG and hybrid router patterns are
   the pragmatic middle.

5. **The industry is standardizing fast.** The Open Semantic Interchange
   (OSI), launched by Snowflake on September 23, 2025, has rapidly grown to
   include dbt Labs, Salesforce, BlackRock, AWS, Google, Databricks and
   others, and released its v1.0 spec on GitHub on January 27, 2026.
   Gartner's *Top Trends in Data and Analytics for 2026* states that
   "Semantics at the core is critical for AI accuracy and reliability,
   achieved through composite semantic layers and GraphRAG to provide
   contextual meaning," and predicts that 40% of enterprises will have
   leveraged GraphRAG techniques by 2029 to improve factual accuracy.

6. **Graph analytics is already production-proven for exactly the kind of
   relationship-heavy problems a checkout business eventually hits**: fraud
   rings across payment attempts, customer-identity resolution across
   channels, and recommendation/basket-affinity graphs. None of that is
   specific to any one industry — it recurs anywhere money moves between
   parties who might be colluding, or a business wants "the same person,
   across surfaces."

## Details

### 1. Core Concepts and Distinctions

**Data model → Semantic layer → Ontology → Knowledge graph → Agent.** Think
of these as a stack of increasing meaning:

- **Data model / schema** — how data is physically stored: tables, columns,
  types, primary/foreign keys. In `kattfot`'s world: a curated view with
  `checkout_session_id`, `customer_token`, `payment_method`, `channel`. The
  schema tells you the column is a `STRING`; it does not tell you what it
  means or how it connects to the business.

- **Semantic layer** — a governance layer over the data model that defines
  *metrics and dimensions* once, with join paths, so every consumer gets the
  same number. This is the sense `query-metric`'s metric catalog operates
  in: each metric file names a numerator, a denominator, an allow-list, and
  a tier. Business Objects coined "semantic layer" in the early 1990s for
  exactly this — shielding business users from SQL. Talisman's sharp
  historical point: the BI industry "inherited this brilliant branding," but
  the metrics-layer sense of "semantic" is *not* formal semantics. A
  semantic layer can tell you that `payment_approval_rate` =
  `SAFE_DIVIDE(SUM(payments_approved), SUM(payments_decisioned))`. It cannot
  tell you what a checkout session *is*, or whether a given session should
  be excluded from the funnel because it belongs to a retired checkout
  implementation.

- **Ontology** — a formal, machine-readable model of *what things are and
  how they relate*: classes (`CheckoutSession`, `Order`, `Payment`,
  `Shipment`, `Customer`), properties/attributes, relationships (a
  `CheckoutSession` *produces* an `Order`; a `Payment` *authorizes* an
  `Order`), and axioms/rules (an `Order` cannot exist without a confirmed
  `Payment`; a `CheckoutSession` *has-at-most-one* `Order`). The
  Airbyte/Tricot framing: ontology is "your business translated into a
  language agents can reason with." It defines entities (real-world
  objects, not tables), relationships ("encode business logic that exists
  nowhere in your schemas but everywhere in how people actually work"), and
  definitions (e.g. one team computes "return rate" over all delivered
  orders, another only over orders past the return window — both valid, but
  different, exactly the ambiguity `kattfot`'s `delivered_to_returned_rate`
  vs. `delivered_to_return_window_complete_rate` split exists to avoid).

- **Knowledge graph** — the instantiation: an ontology populated with actual
  instance data, stored as a graph of nodes and edges. The widely used
  analogy (Alation, DataHub): the **ontology is the blueprint; the knowledge
  graph is the building.** You can have a KG without a formal ontology
  (many do), but the ontology is the schema that governs which node types,
  properties, and relationships are valid. Crucially, a semantic knowledge
  graph collapses conceptual, logical, and physical layers into one
  structure and treats relationships as first-class citizens.

- **AI agent** — the consumer that reasons over the stack. The emerging term
  is the "context layer" or "enterprise context layer" (ECL): per
  Alation/Gartner framing, it curates, integrates, and filters information
  for AI models, building on the ontology to give agents grounded
  understanding of the domain plus operational state and decision history
  that a static semantic layer cannot provide.

**The "triple."** RDF stores everything as **subject–predicate–object**
triples:
```
:Order_88213      :hasPayment        :Payment_5591 .
:Order_88213       :hasCheckoutSession :CheckoutSession_A0F2 .
:Order_88213       :hasStatus          "shipped" .
```
Each triple is an atomic, independently queryable fact. The relationship
(`:hasPayment`) is identified by a URI and is itself a first-class object —
you can attach metadata to it, reason over it, and traverse it without
computing a `JOIN`. Contrast the relational equivalent, where "this order's
payment is #5591" lives implicitly in a foreign-key relationship between an
`orders` table and a `payments` table and must be reassembled by a query
writer who already knows the schema. This is the deep reason knowledge
graphs help LLMs: the relationships that a human analyst carries in their
head (and that an LLM otherwise has to *guess*) are made explicit and
machine-traversable — which is exactly the gap `kattfot`'s entity registry
(`skills/query-metric/entities/`) exists to close cheaply, short of a graph
database: it names `checkout_session → payment` as
`one_to_zero_or_one via checkout_session_id`, in a file an agent reads, so
nothing has to be inferred from column names.

### 2. Technical / Engineering Depth

**RDF/OWL vs. Labeled Property Graphs (LPG).** These are the two dominant
graph data models and they descend from different intellectual lineages —
RDF/OWL from formal logic, knowledge representation, library science, and
Tim Berners-Lee's Semantic Web; LPG from graph theory and object-oriented
databases.

| Dimension | RDF / OWL | Labeled Property Graph (LPG) |
|---|---|---|
| Atom | Triple (subject-predicate-object) | Nodes + edges, both with key/value properties |
| Standards | W3C: RDF, RDFS, OWL, SPARQL, SHACL, SKOS | ISO GQL (2024); previously no W3C standard |
| Query language | SPARQL (W3C standard) | Cypher / openCypher / GQL; Gremlin (traversal) |
| Formal semantics & reasoning | Strong — OWL is based on Description Logic; supports inference (TBox/ABox), consistency checking | No formal logic foundation; "semantics" usually implemented as server-side rules |
| Properties on relationships | Only indirectly (reification, RDF-star, named graphs) | Native — edges carry properties (weight, timestamp, confidence) |
| Best at | Meaning, cross-org integration, FAIR/linked-open-data, governance, regulatory reasoning | Multi-hop traversal performance, fraud/recommendation/network analysis, developer adoption |

Talisman's decision rule: **Use RDF/OWL when the dominant problem is
meaning, integration across organizational boundaries, formal reasoning, or
long-term governance** (life sciences, healthcare, government,
finance/regulatory). **Use an LPG when the dominant problem is operational
multi-hop traversal performance, rich edge attributes, and developer ease**
(fraud detection, recommendations, network topology — the shape a checkout
and payments business is actually likely to hit first). A pragmatic caution
from practitioners (DZone): many teams that adopt RDF use very little of
OWL's reasoning power in practice because full OWL reasoning is complex and
can be slow at scale — "if you store your data in RDF and query it in
SPARQL, you're not [necessarily] semantic; you have a graph." Some platforms
(e.g. Amazon Neptune) support both models.

**Query languages — syntax basics.**

SPARQL (RDF) — graph-pattern matching:
```sparql
# Find every payment behind a declined order
SELECT ?payment ?order WHERE {
  ?order :hasStatus "declined" .
  ?order :hasPayment ?payment .
}
```

Cypher / GQL (property graph) — ASCII-art pattern matching:
```cypher
// Same question in Cypher
MATCH (p:Payment)-[:AUTHORIZES]->(o:Order)
WHERE o.status = 'declined'
RETURN p.payment_id, o.order_id
```

**GQL is the big recent development.** Published as **ISO/IEC 39075 in
April 2024**, GQL is the first new ISO database query language in over 35
years (since SQL), developed by the same committee. It descends directly
from Cypher (created at Neo4j in 2010; opened as openCypher in 2015). A
companion standard, **SQL/PGQ** (SQL Property Graph Queries, part of
SQL:2023), lets you define and run read-only graph queries over relational
tables — relevant to any BigQuery-centric team, `kattfot`'s included, because
it means graph querying can increasingly happen *inside* a warehouse without
a separate graph database.

**Graph databases and platforms (how they compare).**
- **Neo4j** — the market-leading native LPG database; Cypher; best developer
  ecosystem, documentation, and onboarding; strong for OLAP-style graph
  analysis; ACID. Offers Neo4j Graph Analytics inside Snowflake and
  Microsoft Fabric.
- **Amazon Neptune** — fully managed AWS service; supports *both* property
  graph (Gremlin/openCypher) and RDF (SPARQL); leans OLTP; good if you're
  AWS-committed and want managed ops.
- **TigerGraph** — native parallel/distributed graph for massive-scale
  analytics and deep multi-hop traversal; GSQL language; benchmarks
  emphasize loading speed and deep-link analytics.
- **Stardog** — RDF/OWL knowledge-graph platform emphasizing reasoning and
  semantic consistency; its **Voicebox** is an AI/natural-language layer
  over the knowledge graph; differentiated by treating meaning and
  inference as first-class (valuable in regulated environments).
- **GraphDB (Ontotext / Graphwise)** — enterprise RDF triplestore with OWL
  reasoning; commonly used to host large public ontologies; strong for
  semantic interoperability and regulatory vocabularies.
- **ArangoDB** — multi-model (graph + document + key-value), AQL; flexible
  when graph and document data must coexist.
- **Apache Jena** — open-source Java framework/triplestore for RDF/SPARQL; a
  building block rather than a turnkey platform.
- **PuppyGraph / zero-ETL approaches** — expose graph querying over data
  that stays in your relational/warehouse/lakehouse, avoiding a separate
  graph store — architecturally appealing for a BigQuery-centric team like
  `kattfot`'s.

**Ontology engineering tools and methodology.**
- **Protégé** is the dominant ontology editor — free, open-source,
  developed at **Stanford University** (originating in Mark Musen's work in
  the late 1980s; ~300,000+ registered users). Protégé Desktop gives full
  OWL 2 authoring and connects to description-logic reasoners (HermiT,
  Pellet); WebProtégé supports collaborative editing.
- **Upper / foundational ontologies** provide domain-neutral scaffolding so
  your domain model is logically consistent and interoperable:
  - **BFO (Basic Formal Ontology)** — a tiny top-level ontology (36 classes,
    40 object properties, 602 axioms) created by Barry Smith and Pierre
    Grenon; **standardized as ISO/IEC 21838-2:2021**; the foundational
    ontology for 700+ ontologies and the OBO Foundry. Splits the world into
    *continuants* (objects that persist) and *occurrents* (processes that
    unfold).
  - **gist** — Semantic Arts' minimalist *enterprise* upper ontology (~100
    classes), using everyday business terms (Person, Organization,
    Agreement, Event) rather than philosophical abstractions; CC-BY 4.0;
    designed as a fast foundation for building business domain ontologies.
    `gistBFO` bridges the two.
  - **schema.org** — the lightweight web vocabulary (Google/Bing/Yahoo) —
    directly useful for a checkout business, since it already defines
    `Product`, `Offer`, `Order`, and `Organization` types many storefronts
    already emit as structured data.
- **Domain ontologies** — built on top of an upper ontology for a specific
  business (e.g. a checkout ontology with `CheckoutSession`, `Payment`,
  `Shipment`, `ReturnWindow`).
- **Methodology / building blocks** (classes, properties, relationships,
  axioms, reasoning). The workflow (e.g. Talisman's "Ontology Pipeline")
  typically moves from controlled vocabularies → taxonomies → thesauri →
  ontology → knowledge graph. You define classes and class hierarchies,
  object properties (relationships) and datatype properties (attributes),
  domain/range constraints, and axioms; a reasoner then *infers* new facts
  and checks consistency (e.g. a checkout session cannot be simultaneously
  `abandoned` and `confirmed`).

A minimal OWL/Turtle snippet for a checkout domain:
```turtle
:CheckoutSession a owl:Class .
:Order a owl:Class .
:Payment a owl:Class .
:hasPayment a owl:ObjectProperty ;
    rdfs:domain :CheckoutSession ; rdfs:range :Payment .
:hasOrder a owl:ObjectProperty ;
    rdfs:domain :CheckoutSession ; rdfs:range :Order .
# Axiom: an Order requires an approved Payment
:Order rdfs:subClassOf
    [ a owl:Restriction ;
      owl:onProperty :hasApprovedPayment ;
      owl:minCardinality 1 ] .
```

**Knowledge graph construction from warehouse data.** Three core steps:
1. **Mapping** relational/warehouse tables to the ontology — the W3C
   standard here is **R2RML** (relational-to-RDF mapping); you declare that
   the `orders` table maps to class `:Order`, the `checkout_session_id` FK
   maps to `:fromCheckoutSession`, etc. The graph can be *materialized*
   (ETL'd into a triplestore) or *virtualized* (queried in place).
2. **Entity resolution** — deciding that a `customer_token` on the checkout
   spine, a loyalty-program member id, and a support-ticket contact are the
   same real-world person. Unlike relational key-matching, graphs match
   through fuzzy text + shared relationships + entity types + attributes.
3. **Knowledge graph embedding** — representing nodes/edges as vectors for
   ML tasks like link prediction and similarity (e.g. embeddings used in
   fraud-ring detection across payment attempts).

### 3. AI Agents + Knowledge Graphs (the core)

**Why text-to-SQL agents fail — the "brilliant new hire with no
onboarding."** A capable LLM pointed at a raw schema is like a brilliant
analyst on day one: technically fluent in SQL, but ignorant of your
business. It will write syntactically perfect SQL that answers the *wrong
question*. The failure is contextual, not syntactic. Evidence:
- The peer-reviewed analysis Tu et al., *"A Study of In-Context-Learning-
  Based Text-to-SQL Errors"* (arXiv:2501.09310), examined 4,602 incorrect
  queries and found that "Syntax errors constitute 18.8% (73 out of 389) of
  all errors that cause execution failures" — i.e. the large majority of
  failures are schema-/semantic-level (wrong column, semantic
  misinterpretation), not syntax.
- A widely cited cohort-retention example: asked "what % of users purchased
  in months 1–4 after first purchase," a general LLM interpreted "month" as
  elapsed days from each user's first purchase, so a Jan-31 + Feb-1 buyer
  landed in "month 0" — confidently wrong, because the business reports by
  *calendar* month. This is exactly the shape of ambiguity `kattfot`'s own
  `cohort_week` convention exists to remove: a metric's frontmatter states
  `time_column_type` and the query builder handles the bucketing once,
  centrally, rather than leaving it to be re-derived per question.
- The headline result is the **data.world insurance benchmark** (Sequeda,
  Allemang, Jacob; arXiv 2311.07509, Nov 2023, later in GRADES-NDA 2024).
  Using GPT-4 zero-shot over the **OMG Property & Casualty insurance data
  model**, with 43 enterprise questions arranged on two axes (question
  complexity: reporting → KPIs; schema complexity: few → many tables):
  - **SQL directly on the schema: 16.7% overall accuracy.**
  - **Same LLM backed by a knowledge graph (OWL ontology + R2RML mappings,
    querying via SPARQL): 54.2%** — roughly **3×**.
  - On **high-schema-complexity questions, SQL scored 0%**; the KG raised
    these to ~35–39%. Verbatim: *"investing in Knowledge Graph provides
    higher accuracy for LLM powered question answering systems."* The
    benchmark's schema was insurance-specific (Property & Casualty), but
    nothing about the result depends on the insurance domain — the
    structural lesson (schema-intensive questions need explicit
    relationships) applies just as directly to a checkout warehouse with a
    session spine, a weekly aggregate, an entity registry, and a handoff
    edge to a second module.

**Ontologies/KGs as the "onboarding layer."** The fix that recurs across
every credible source: give the agent governed definitions and explicit
relationships so it *selects* from a curated model rather than
*improvising* SQL. As Cube puts it, "text-to-SQL gives an agent access to
your data; a semantic layer gives it understanding." Ontologies extend this
from *measures* to *meaning* and *rules* — the difference between "here's
how to compute payment approval rate" and "here's what a checkout session
is, how it relates to an order and a shipment, and the rule that a session
which never confirmed has no order at all, so an order-grain denominator is
structurally blind to the single largest exit in the funnel." (That last
rule is, verbatim, `kattfot`'s own `confirmation_creates_the_order` business
rule — a small, concrete instance of exactly the "ontology-lite" pattern
described in §5 below.)

**GraphRAG vs. vector RAG.**
- **Vector RAG** chunks documents, embeds them, and retrieves by
  similarity. Great for "find me things *like* this." It breaks on
  questions requiring reasoning *across* relationships, because chunking
  severs context and retrieval precision degrades at scale.
- **GraphRAG** (Microsoft, April 2024) extracts entities and relationships
  into a knowledge graph, then retrieves connected subgraphs and community
  summaries. It answers "find me what is *connected* to this" and supports
  multi-hop, causal, and synthesis questions. Cost is the catch — one
  comparison cited GraphRAG needing roughly ten times the tokens of a
  lighter retrieval approach to build and query.
- **LightRAG** (HKU) combines vector retrieval with a deduplicated graph and
  incremental updates — graph reasoning at a fraction of GraphRAG's cost.
- **Practical pattern:** don't choose — *route*. A lightweight classifier
  sends similarity questions to the vector store and relational/causal
  questions to the graph; or run vector-first then enrich with a graph
  pass.

**Agentic analytics architectures and the role of MCP.** The **Model
Context Protocol (MCP)**, open-sourced by Anthropic in November 2024 and
since donated to the Linux Foundation, is the emerging standard interface by
which an agent *discovers* and *queries* governed context instead of
writing raw SQL. The flow: the agent discovers available governed
metrics/dimensions, **selects** by name, and the semantic layer **compiles
to SQL under governance** (row-/column-level security applied at compile
time). `kattfot` implements the same pattern one layer down, without MCP: an
agent invokes `kattfot:catalog` to discover what is registered, selects a
metric by name, and `kattfot:query-metric` compiles that selection to a
parameterized query under the allow-list and PII checks — the same
discover-select-compile shape, expressed as skills rather than as an MCP
server, because that is the interface this template's runtime (Claude Code
/ Cursor plugins) actually offers.

**Vendor/product approaches.**
- **dbt Semantic Layer / MetricFlow** — define metrics in YAML alongside
  models; open-sourced under Apache 2.0 and contributed to OSI; native MCP
  server. Best fit if you live in dbt.
- **Cube** — headless, open-source semantic layer with caching, multi-tenant
  security, and SQL/REST/GraphQL/MCP serving; strong for embedded/product
  analytics and AI agents.
- **Snowflake Cortex Analyst + Semantic Views** — Snowflake's managed
  text-to-SQL driven by a **semantic view** (native object) or YAML semantic
  model; integrates with MCP.
- **Palantir Foundry Ontology + AIP** — the most mature "ontology-as-
  operational-layer" product: models the "nouns and verbs" of a business as
  **objects, properties, links, actions, and functions**, with security
  woven in. This is the high end of agentic analytics — expensive and
  heavyweight, but the reference architecture.
- **Databricks** — metric views in Unity Catalog.
- **AtScale / LookML / Power BI** — established enterprise semantic/metrics
  layers.
- **Stardog Voicebox**, **data.world**, **RelationalAI** — knowledge-graph-
  or context-layer-first approaches to natural-language analytics with
  provenance.

### 4. Industry Landscape and Maturity

**The maturity curve.** Most teams are at Stage 1–2 while the conversation
is at Stage 4:
- **Stage 1 — Tables exist.** Data is in a warehouse; meaning lives in
  analysts' heads.
- **Stage 2 — Semantic layer.** Metrics and dimensions are governed (a
  metric catalog like `query-metric`'s, or a dedicated tool). Numbers are
  consistent.
- **Stage 3 — Relationship & rule context documented.** The relationships
  the schema *implies but doesn't state* are written down: which entities
  connect, which records to exclude, which segments behave differently.
  (This is "ontology-lite" — where `kattfot`'s entity registry and
  `LINEAGE.md` files sit.)
- **Stage 4 — Ontology + knowledge graph.** Formal classes, relationships,
  axioms, reasoning; entity resolution across systems; agents reason over
  the graph.

**Why KGs were confined to big tech — and what changed.** Google coined
"Knowledge Graph" on May 16, 2012 (built partly on Freebase/Metaweb); it
tripled within seven months to ~570M entities/18B facts and held ~70B facts
by 2016. Facebook (Graph, 2013), LinkedIn, Microsoft (Satori/Bing), and
Amazon (product graph, Alexa) followed. These stayed inside big tech because
they required scarce specialized skills (ontologists), immature/expensive
tooling, and large teams. **What changed (2023–2026):** (a) AI agents
*exposed the gap* — agents fail precisely where semantic understanding was
supposed to live ("connectivity without semantics is just faster error");
(b) tooling barriers dropped (managed graph services, LLM-assisted ontology
construction, zero-ETL graph-over-warehouse); (c) standards converged (GQL
2024, OSI 2025–26). Gartner put knowledge graphs on its hype cycle twice —
once on the data side, once (about two years later) on the AI side — and its
*Top Trends in Data and Analytics for 2026* now frames "semantics at the
core" (composite semantic layers + GraphRAG) as "a nonnegotiable foundation"
for AI accuracy, predicting 40% of enterprises will use GraphRAG by 2029.

**Open Semantic Interchange (OSI).** Launched by Snowflake on **September
23, 2025** with Salesforce, dbt Labs, BlackRock, and RelationalAI, plus a
broad ecosystem (Alation, Atlan, Cube, Hex, Omni, Sigma, ThoughtSpot,
Mistral AI, Select Star, and others). Goal: a **vendor-neutral, open
specification for semantic metadata** so a definition of "net revenue" (or
"payment approval rate") moves between tools instead of being trapped in
one. Timeline: first working-group session October 17, 2025; **AWS and
Google joined at the November 13, 2025 working-group session** (alongside
Collibra, DataHub, Domo, Firebolt, Informatica, Instacart, JPMC, Preset,
Starburst, and Strategy); **v1.0 spec released on GitHub January 27, 2026**,
with Databricks, AtScale, Qlik and others joining; a Financial Services
Working Group formed. dbt's open-sourcing of MetricFlow (Apache 2.0) is a
core contribution. Significance: this is the most credible attempt yet to
solve semantic fragmentation — and it directly benefits agentic analytics,
which needs consistent definitions across tools. Healthy skepticism is
warranted: it's a metrics-layer standard (measures), not a full ontology
standard (meaning), and Phase-2 adoption through 2026 is the real test.

**Thought leaders and what each is known for.**
- **Jessica Talisman** (The Ontology Pipeline; ex-Adobe information
  architect) — the clearest voice distinguishing formal semantics from BI
  "semantic layers"; "semantic layer is for lookup, ontology is for context
  and reasoning"; the Ontology Pipeline methodology; RDF/OWL-vs-LPG decision
  guidance.
- **Juan Sequeda** (data.world; Head of AI Lab; co-host *Catalog &
  Cocktails*) — the KG-improves-LLM-accuracy benchmark (3× on the insurance
  schema); academic pedigree in mapping relational data to graphs (R2RML);
  "treat business context and semantics as a first-class citizen."
- **Tony Seale** ("The Knowledge Graph Guy") — the **neuro-symbolic loop**
  (LLMs generate, knowledge graphs/ontologies ground and verify);
  relationships as "first-class citizens" via open standards; "your
  ontology is your digital thumbprint."
- **Michel Tricot** (Airbyte CEO) — "schema describes how data is stored;
  ontology describes how your business works"; ontology-as-code; the
  Context Store / missing-context-layer thesis for why agents fail in
  production.
- **Dean Allemang** (author, *Semantic Web for the Working Ontologist*) —
  co-author of the data.world benchmarks; long-time RDF/OWL evangelist.
- Also: **Barry Smith** (BFO) and **Bill Inmon** (data-warehouse pioneer now
  writing on semantics).

### 5. Practical Adoption — "What to Actually Do"

**The "ontology-lite" approach.** You do not need a triplestore to capture
80% of the value. Enrich your existing warehouse documentation with
*relationships and business rules*, not just column descriptions.

*Before (typical warehouse documentation):*
```yaml
models:
  - name: fct_checkout_sessions
    columns:
      - name: checkout_session_id
        description: "Unique checkout session identifier"
      - name: order_id
        description: "The order this session produced, if any"
      - name: payment_method
        description: "How the payment was attempted"
```

*After (ontology-lite — this is, almost verbatim, the shape `kattfot`'s own
entity files already take):*
```yaml
models:
  - name: fct_checkout_sessions
    description: >
      One row per shopping cart. A CheckoutSession produces at most one
      Order, only at confirmation, after an approved Payment. A session
      that never confirms has no order_id at all — order_id is NOT a safe
      denominator for the funnel, because it is structurally blind to the
      abandoned cart, the largest single exit in this ladder.
    meta:
      entity: checkout_session
      business_rules:
        - "order_id is NULL on every session that never confirmed; use
           carts_created as the funnel denominator, never orders_confirmed."
        - "A session belongs to its CREATION cohort week forever, even if
           it confirms three weeks later — activity-time bucketing would
           attribute an outcome to a week whose traffic did not produce it."
        - "A delivered order is not a settled order: the customer has a
           30-day return window, so 'kept' is unknowable until it closes."
      relationships:
        - "checkout_session_id -> fct_payments (a session has at most one
           payment authorization attempt)"
        - "checkout_session_id <- fct_orders (an order references exactly
           one checkout session, created at confirmation)"
    columns:
      - name: payment_method
        description: >
          card_instant | wallet | bank_transfer_review. Drives settle TIME
          as well as approval rate — bank_transfer_review is routed through
          a clearing cycle and a review queue, so a blended
          submit-to-decision number is dominated by card_instant volume and
          badly wrong for the slow method.
```

This is exactly the gap Tricot describes: *"Ask a data team who owns metric
definitions and you get a clear answer. Ask who owns the entity model — who
decided which foreign key means what, and which record types to exclude —
and you get a pause."* Ontology-lite assigns and documents that knowledge.
`kattfot`'s `LINEAGE.md` per module and its entity registry
(`skills/query-metric/entities/`) are this pattern, implemented rather than
merely described.

**Documenting what the data implies but doesn't state.** The highest-value
additions are the invisible rules: "an order-grain denominator is blind to
the abandoned cart," "a return rate read without a maturity cutoff trends
toward zero at the recent end and gets misread as improving quality," "a
retired checkout version is excluded by default, and excluding it needs
both a column and a value set, or the exclusion silently does nothing."
These are precisely the things a "brilliant new hire" gets wrong and an LLM
hallucinates.

**Why AI can read a schema but cannot build an ontology.** An LLM can
introspect column names and infer plausible types and joins. It cannot know
that *your* business excludes a legacy checkout cohort from every default
read, or that "kept" versus "returned" is only knowable 30 days after
delivery, or that a specific payment method is being sunset. That knowledge
is institutional and lives in the heads of the people who have watched the
funnel for years. As Seale argues, an ontology "converts a recurring tax —
expert attention — into a one-off investment," and it is a *moat* precisely
because competitors cannot buy back the years of formalized judgment your
operators have lived through. LLMs can *assist* (propose a draft ontology
from documents, then extract instances against it — the neuro-symbolic
loop) but **human-in-the-loop is a design principle, not a workaround.**

**A practical maturity roadmap.**
1. **Govern metrics first (weeks).** Define your core measures with a
   named numerator, denominator, allow-list, and owner — get the join paths
   and filters correct. This alone removes the biggest class of agent
   errors. `kattfot`'s `AUTHORING.md` and metric template are exactly this
   step, worked through for one domain.
2. **Wire up a discovery surface (days).** Give the agent a way to
   *discover* governed metrics and documented models rather than raw
   tables — `kattfot:catalog` / `kattfot:load-context`, or an MCP server if your
   stack already has one. Test against real business questions; verify
   consistency and that the allow-list holds.
3. **Add ontology-lite context (ongoing).** Enrich your documentation with
   entities, relationships, and especially the *unwritten business rules*
   (denominator exclusions, lifecycle states, segment differences,
   maturity cutoffs). Treat it as code: version control, review, deploy.
4. **Add a graph only where relationships are the product (selective).**
   For fraud rings across payment attempts, cross-channel customer
   resolution, or a basket-affinity graph, pilot a property graph on that
   *specific* problem. Measure before scaling.
5. **Formalize to OWL/KG + reasoning only where it pays (later).** Reserve
   full RDF/OWL for cross-system integration, regulatory reasoning (payment
   compliance, tax), or when you need automated inference and consistency
   checking.

**Thresholds that should change the plan:** if agents still err *after*
governed metrics + a discovery surface, the gap is relationship/rule
context → invest in ontology-lite. If your hardest questions are inherently
multi-hop ("which payment attempts share a device fingerprint with a
confirmed chargeback") and joins are painful/slow → adopt a graph for that
domain. If you need machine-checkable rules across systems and regulators →
formalize to OWL.

### 6. Applied to E-Commerce Checkout & Fulfilment

**Domain ontology sketch.** This is, not coincidentally, close to `kattfot`'s
own entity registry — the checkout domain is a reasonable worked example of
exactly the "ontology-lite" pattern this essay recommends. Core classes:
`Party` (super-class of `Customer`, `Merchant`), `CheckoutSession` (the
cart), `Order` (created at confirmation), `Payment` (an authorization
attempt and its decision), `Shipment` (a dispatch → delivery leg), `Return`,
`Channel`, `Market`. Core relationships: a `Customer` *creates* many
`CheckoutSession`s; a `CheckoutSession` *produces* at most one `Order`, only
after an approved `Payment`; an `Order` *generates* a `Shipment`; a
`Shipment` *may generate* a `Return` within the return window. Axioms: an
`Order` requires an approved `Payment`; a `Return` can only be initiated
against a `Shipment` that has a `delivered_at` timestamp; a session's
cohort week is fixed at creation and does not change when the session later
confirms.

**Relevant standards, adapted to this domain (not insurance-specific).**
- **GS1** — the global standards body behind product barcoding (GTIN),
  location identifiers (GLN), and increasingly product data exchange for
  e-commerce; the natural vocabulary backbone if you need to interoperate
  with a supplier or marketplace catalog outside your own warehouse.
- **schema.org's Commerce vocabulary** — `Product`, `Offer`, `Order`,
  `Organization`, and `ItemAvailability` are already widely emitted as
  structured data on storefronts; a lightweight, genuinely interoperable
  starting vocabulary if you need to align an internal ontology with what
  is already public on your own site.
- **ISO 8000 (data quality) and ISO 20022 (financial messaging)** — relevant
  once payments data needs to interoperate with banking-side systems rather
  than staying internal to the checkout warehouse.

**High-value graph/KG use cases in a checkout & payments business.**
- **Fraud detection via graph analytics** — the canonical use case, and it
  transfers directly from the insurance and banking literature: fraud rings
  are *relationships* — the same card fingerprint, shipping address, or
  device recurring across ostensibly unrelated checkout sessions. Graph
  traversal and community detection surface these patterns that JOIN-heavy
  SQL misses.
- **Customer 360** — entity-resolve a person across checkout sessions,
  support contacts, and loyalty-program membership into one node, then
  traverse to all orders, returns, and interactions. Powers churn
  prediction, targeted win-back, and faster support resolution.
- **Basket-affinity / recommendation graphs** — model products, categories,
  and co-purchase edges to power "customers who bought this also bought"
  and merchandising decisions, distinct from — but complementary to — the
  governed conversion-rate metrics `kattfot` itself serves.
- **Return-fraud and abuse rings** — the same graph techniques used for
  payment fraud apply to serial-return abuse: the same address or device
  recurring across accounts with an anomalous return rate.
- **Regulatory / compliance reasoning** — payment-network rules and
  cross-border tax obligations are exactly the kind of machine-checkable
  axioms formal reasoning earns its keep on, once a business operates in
  enough markets that "which rule applies where" stops fitting in one
  person's head.

## Recommendations

1. **Start now, start cheap: govern metrics with a named numerator,
   denominator, allow-list, and owner, and expose them to an agent through
   a discovery surface it can query instead of guessing at raw tables.**
   This removes the largest, best-documented class of agent errors (wrong
   joins, wrong metric logic) with no new infrastructure. `kattfot`'s
   `query-metric` + `catalog` pairing is this step, already built.
2. **Then invest in ontology-lite enrichment of your documentation** —
   entities, relationships, and especially the *unwritten business rules*
   (denominator exclusions, lifecycle states, maturity cutoffs, per-segment
   differences). Treat the ontology as code (version control, review).
   This is where institutional knowledge becomes a durable, compounding
   asset.
3. **Adopt graphs surgically, not religiously.** Pilot a property graph only
   for genuinely relationship-centric problems — fraud rings, cross-channel
   customer resolution, basket affinity. Prove value on one use case before
   generalizing.
4. **Reserve RDF/OWL + reasoning for integration and compliance.** If/when
   you need cross-system entity resolution, regulatory reasoning, or
   machine-checkable rules across a payments network, align to public
   vocabularies (schema.org, GS1) where they cover your case, and budget
   for human expert time — the people who have watched the funnel for
   years — because the ontology *is* their formalized judgment.
5. **Track the standards.** Follow OSI (v1.0 shipped January 27, 2026) — if
   your tools support it, you get portable metric definitions across BI and
   agents. Watch GQL/SQL-PGQ adoption in your warehouse, which may let you
   do graph work without a separate database.

## Caveats

- **Hype vs. reality.** Mature: BI semantic layers, property-graph fraud
  analytics, Protégé/OWL tooling, MCP for governed-metric access. Emerging/
  unsettled: GraphRAG at production cost/scale, fully autonomous agentic
  analytics, OSI adoption (the spec is young; Phase-2 adoption through 2026
  is the real test), and warehouse-native graph (SQL/PGQ) maturity.
- **LLMs over semantic models are better but not deterministic.** Even with
  a correct semantic model, the LLM's *use* of it is probabilistic — "the
  context is a hint, not a guarantee." Production reliability needs
  deterministic compilation/guardrails (the semantic layer compiling to
  SQL, validation of generated queries — exactly what `kattfot`'s allow-list
  and refusal rules are), not just better prompts. GPT-5-class agentic
  modes still completed only ~29% of the hardest enterprise text-to-SQL
  tasks in one cited benchmark (BIRD-Interact).
- **Vendor framing colors the sources.** Much of the best-written material
  is vendor content (Cube, Snowflake, dbt, Airbyte, Alation, Atlan, Neo4j,
  data.world) with an interest in selling the conclusion. The independent
  academic results (Sequeda et al.; Tu et al.) and standards bodies (W3C,
  ISO, OMG) are the firmer ground.
- **Benchmark scope.** The data.world 3× result uses GPT-4 (2023) on a
  **Property & Casualty insurance** schema, not e-commerce, and one curated
  43-question set; the structural lesson (KGs help most on schema-intensive
  questions) is robust across domains, but treat the exact multiple as
  illustrative, not a guarantee for any other schema, including this
  template's. There is also a minor internal inconsistency in the paper's
  high-complexity figures (38.5% in the table vs. 38.7% in the text).
- **No published benchmark of this kind exists for a checkout/e-commerce
  schema specifically**, to this brief's knowledge — the domain adaptation
  in §6 above is a reasoned extension of the general finding, not a
  citation of an e-commerce-specific study. Treat it as a hypothesis worth
  testing against your own agent's failure modes, not an established
  result.
- **Ontologies decay without ownership.** Both semantic layers and
  ontologies are abstractions that depend on metadata, lineage, and
  governance to stay accurate; without an owner and a maintenance process
  they rot into misleading artifacts. `kattfot`'s own
  `doctrine/ownership-governance.md` exists because of exactly this
  failure mode. The hard part is organizational, not technical.
