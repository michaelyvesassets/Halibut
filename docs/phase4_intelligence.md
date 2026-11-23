# Phase 4: market intelligence foundation and continuous learning (M22–M28)

> **Epistemic labels used throughout Halibut.**
> - *Observed*: a fact from a cited source.
> - *Derived*: computed by Halibut from observed facts, with a recomputable
>   definition.
> - *Hypothesis*: proposed and untested.
> - *Synthetic*: model output from uncalibrated assumptions.
>
> Agent-generated content is never stored as an observation.

## 1. Logical components

These are module boundaries, not services.

| Component | Modules | Responsibility |
|---|---|---|
| **Shared Evidence and Experiment Store** | `halibut.data.store` (SQLite, migrations 1–3) | Bitemporal observations; raw snapshots; entities, identifiers, relationships; documents; fetch log; experiments; knowledge; evaluations; promotions |
| **World Model** | entities, relationships, observations, `halibut.intel.retrieval` | What is known about markets, brands, companies, products and places, read as of a date |
| **Intelligence System** | `halibut.data.providers.*`, `halibut.intel.signals`, `halibut.intel.workflow` | Acquire evidence under budgets; resolve entities; derive signals |
| **Simulation Engine** | `halibut.engine`, `decision`, `influence`, … (unchanged) | Agent-based market dynamics; scenario runs |
| **Strategy System** | `halibut.intel.agents`, market modes | Hypotheses in enforcement, continuation or annihilation framing; legitimate levers; scenario drafts |
| **Learning / Evaluation Engine** | `halibut.learning.*` | Record experiences, extract lessons, evaluate under explicit rules, promote only with approval, roll back |
| **Validation / Safety Engine** | `halibut.validation` | Evidence existence and availability, metric recomputation, no-hindsight predictions, allowed levers, prohibited tactics, read-only action policy |

## 2. Providers (verified 2026-11-23 from this Codespace)

| Provider | Access | Licence | Live result |
|---|---|---|---|
| World Bank WDI API v2 | open | CC BY 4.0 (most series) | ✅ smoke 20.9 s; investigation 3.8 s (3 indicators) |
| Wikidata (search + SPARQL) | open; project User-Agent | CC0 | ✅ 16 requests; 7 of 8 brands resolved, MG left **unresolved** (never guessed) |
| OSM via Overpass | open, few slots | **ODbL (share-alike)** | ✅ 1 country query, 129.8 s; earlier attempts returned 502 or timed out (flaky) |
| GDELT 2.0 GKG bulk files | open | free with citation | ✅ 12 sampled files, 64 MB in total; the DOC API returned **HTTP 429** here, so it is not used |
| UK DfT VEH0160 | open | OGL v3.0 | ✅ 10.3 MB CSV, 2014 Q3 – 2026 Q2, make × model × fuel |
| RDW (Netherlands) | n/a | CC0 (catalogue) | ❌ `opendata.rdw.nl` has **no public DNS record** (3 resolvers); not used, not substituted |

Requests identify the project only:
`halibut-research-client/0.3.0 (+https://github.com/michaelyvesassets/Halibut)`.
They carry no personal contact data. If a provider requires a contact email, the
project owner must supply a verified one.

**Definitions preserved.**
- DfT first registrations "include both new vehicles and vehicles imported
  into the UK at the point of first UK registration". They are reported as
  that universe, never as new-car sales.
- Make-level and model-level series are separate universes.
- GDELT records match a brand only when an organisation name equals an alias,
  so "ford" never matches "stanford". GDELT uses FIPS country codes: the UK is
  `UK`.

## 3. Competitor intelligence model

- **Brands** (`brand:<id>`, the market-facing identity) are distinct from
  **operating companies** (`wd:Q…`), **parents** (`parent_organization`),
  **product lines** (`product:dft:<make>:<model>`, `product_of`) and
  **locations** (`osm:<type>/<id>`, `located_at`).
- Each relationship records its epistemic status:
  - Wikidata claims are `observed`;
  - resolution links are `derived`, with their method;
  - spec overrides are `asserted`.
- A **market spec** (`configs/intel/markets/*.toml`) defines the category,
  geography, as-of date, competitor set, strategic mode, focal and target
  competitors, the evidence plan and hard limits. It is the only
  industry-specific input.
- **Trend Annihilation** is competitor-targeted displacement: a focal
  challenger against a named target, from a local business up to an industry.
  The same code applies to any market for which evidence exists.

## 4. Demonstration: `halibut intel investigate configs/intel/markets/uk_cars_bev_v1.toml`

UK cars, as of 2026-11-23. Mode: annihilation, focal BYD, target Volkswagen.
The demo is the UK fallback for the RDW-based Dutch demo.

**Live run:** 36 requests, 64.0 MB, 273 s; all 5 evidence steps ok. An
**offline** re-run (`--offline`, evidence already stored) takes about 1 s.

Observed and derived signals (first UK registrations). L4 = 2025 Q3 – 2026 Q2;
P4 = the four quarters before.

| | Share P4 → L4 | Battery-electric segment share P4 → L4 | BEV mix L4 | Mapped OSM dealers |
|---|---|---|---|---|
| BYD | 1.24% → 3.19% | 3.60% → 7.01% | 53.9% | 24 |
| Volkswagen | 9.05% → 8.44% | 7.81% → 6.03% | 17.5% | 126 |
| Market BEV share | | 21.61% → 24.50% | | |

**Output: 7 hypotheses**, each passing validation:
- every cited metric recomputed from its definition;
- every evidence ID available as of the run date;
- every prediction dated after that date.

They are recorded as *proposed* knowledge with predictions and critiques.
Examples:
- *annihilation, low confidence*: within BEV registrations, BYD gained
  3.41 pp while Volkswagen lost 1.78 pp, "consistent with displacement". The
  critic adds that third-brand flows can produce the same pattern.
- *continuation*: Volkswagen's 17.5% BEV mix is below the market's 24.5%.

**Scenario draft:** a 3-product configuration (focal, target, others),
validated by the engine. Its control reproduces the observed starting segment
shares. It runs illustrative legitimate levers: focal price −10%, range
expansion, a defensive price cut by the target. It is labelled synthetic and
exploratory.

**Limits found:**
- the media sample is too thin (14 matches in 12 files; 0 for BYD or Tesla);
- MG is unresolved;
- OSM counts measure mapping completeness;
- prices, fleet versus private buyers, and switching are unobserved.

## 5. Continuous learning (shared learning memory)

The four steps are kept separate:

1. **Record.** `experiments` store lineage: code commit and dirty flag,
   engine version, config hash, data snapshot hashes, parameters, seeds,
   metrics, and the baseline. Rationale and expected improvement are stored
   with every candidate, and failed experiments are kept.
2. **Extract.** `knowledge` holds hypotheses, predictions, outcomes, lessons,
   critiques, questions and proposals. Statuses are proposed, tested,
   validated, rejected, inconclusive and superseded. `knowledge_links`
   records supports, contradicts, critiques, derived_from, tests, supersedes
   and considered, so disagreement is preserved.
3. **Validate.** The rule `paired-dev-holdout-v1` (`halibut.learning.evaluate`)
   requires all of the following:
   - development units chosen in advance;
   - the candidate wins at least 60% of them;
   - the paired 95% CI of the improvement excludes 0;
   - the held-out mean also improves, and the candidate is not worse than the
     simple baseline on held-out data.

   Fewer than 3 units always gives *inconclusive*. One favourable result
   never promotes.
4. **Update.** `promotions` activate a candidate only with an eligible
   evaluation **and** a named person (`--approved-by`). Promotions are
   reversible (`halibut learn rollback`). Nothing is deployed automatically.

**Trust rules (enforced in code):**
- agents can submit only *proposed* knowledge;
- *validated* requires an eligible evaluation of the same experiment;
- knowledge cannot write observations;
- promotion by an agent or engine identity is refused.

**Learning-loop demonstration** (`halibut learn demo`; real Norway data, as of
each origin, 1.7 s):

| Step | Development MAE (6 origins) | Held-out MAE (3 origins) | Rule outcome |
|---|---|---|---|
| Baseline: logistic, full history | 2.41 / 2.69 / 4.74 / 12.30 / 7.04 / 6.00 | 6.85 / 3.97 / 5.75 | — |
| Change 1: fit window 60 months | won 2/6, CI includes 0 | **improved** (4.84 / 4.13 / 5.16) | **inconclusive → hold** |
| Agent reads lesson 1, proposes an ablation: window 96 | won 3/6, CI includes 0 | mixed | **inconclusive → hold** |

The first change looked good on held-out data. The rule still refused to
promote it, because development evidence did not support it. Each lesson
records that reason.

**Cross-agent use in the intelligence workflow.** The investigation retrieves
lessons, outcomes and questions for its domains. The recorded M21 outcome
(diffusion baselines beat the agent model) **changes the run's behaviour**:
predictions use persistence and scenario drafts are marked exploratory, with
the cited knowledge ID. Every new hypothesis links to the items it
`considered`.

`halibut learn import-backtest` records the M21 backtest as an experiment and
outcome. It also opens a question: does the deficit persist after calibration
converges?

## 6. Safety boundaries

- **Read-only, propose-only agents.** `check_action` allows read and propose
  actions only. No posting, messaging, purchasing, outreach or engagement
  automation exists.
- **Levers** must be legitimate competition: price, product range or quality,
  distribution, availability, service, disclosed advertising, financing, or
  support infrastructure.
- **Prohibited tactics are rejected:** fake reviews, impersonation, bots,
  astroturfing, misinformation, sabotage, collusion, spam.
- **No Qwen, Supabase or dashboard.** The interfaces in `halibut.interfaces`
  are ready for them.

## 7. Remaining work (explicit)

- **Prediction scoring.** Predictions are stored with `resolvable_after`
  dates, but there is no automatic scorer yet that marks them supported or
  refuted when DfT publishes the next quarters. The first resolvable date is
  2027-09 under the current conservative rule.
- **Lesson generalisation** beyond one pilot. Validating a lesson across
  markets needs multi-market evaluations. The schema supports this
  (units = markets); no demo yet.
- **Critique and disagreement from independent agents.** Only one
  deterministic critic exists; Qwen agents will be additional, separately
  identified contributors.
- **Learned policies inside the intelligence workflow.** The reference agent
  applies retrieved outcomes through an explicit rule (`forecast_policy`).
  There is no learned or parameterised policy yet.
- **Provider gaps:**
  - MG entity resolution needs an analyst override (`wikidata = "Q…"`) or
    better type hints;
  - GDELT needs a larger, still bounded sample, or an approved query channel,
    to be informative;
  - RDW must be retried from another network;
  - OSM history is not captured.
