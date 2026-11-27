# Master Intelligence Architecture V2 (proposal for review)

Status:
- **Proposal (2026-11-27); not approved; nothing implemented.**
- It deepens, and does not replace, the eight layers of [MASTER_ARCHITECTURE.md](MASTER_ARCHITECTURE.md) v1.0.
- It consolidates the founder's direction (the ChatGPT discussions and an independent Claude Opus assessment, as
  summarised by the founder on 2026-11-27) with the measured R2 evidence
  ([R2_STEP2A_ASSESSMENT.md](R2_STEP2A_ASSESSMENT.md), [R2_STEP2B_ASSESSMENT.md](R2_STEP2B_ASSESSMENT.md)).

Halibut stays an **autonomous market-intelligence, economic-simulation and strategic-decision system**. Forecasting is
one instrument of verification, not the product.

## 0. Principles carried forward, and new ones

**Carried forward from v1.0:**
- explanations, hypotheses, forecasts and simulations stay separate;
- official numbers come only from registered models;
- promotion needs an evaluation rule and a named person;
- agents propose and the engine verifies;
- every paid action is approved and capped;
- nothing acts on the world without approval and legal review.

**New in V2, learned from R2:**
1. **Predictive support is not causal validation.** Separate ledgers, labels and promotion rules (§B).
2. **Every evaluation is checked against naive baselines on both sides.** Persistence AND momentum; disputes only
   against rivals consistent with the evidence available at the time; correlated questions deduplicated.
3. **Contract usability is part of integrity.** Every limit the engine enforces is visible to the model, and every
   quantity the engine can calculate is calculated by the engine (§H).
4. **Feedback is produced continuously and resolved automatically,** with leakage labels and sealed holdouts (§D, §E).
5. **Cost is measured jointly with quality.** API, data, infrastructure and human-validation costs, per validated
   outcome (§G).

## 1. Capability map against the eight layers and the repository

Status: **I** implemented; **P** partially implemented; **M** missing; **S** premature or scientifically speculative
now.

### A. Specialised intelligence

| Capability | Layer | Already in the repository | Mechanism to add | Depends on | Evaluation | Status |
|---|---|---|---|---|---|---|
| A1 Causal | L5, L6 | ABM engine with interventions and paired seeds (`engine`, `interventions`, `experiment`, `suite`); experiment protocols with eligibility; causal.did marked unavailable | A causal-claim type separate from predictive support; identification designs (difference-in-differences, synthetic control, regional rollouts) gated by data availability; DAG and assumption records per mechanism | regional or intervention data (missing); world-model registry (B) | identification checks (pre-trends, placebo); never promoted from predictive accuracy alone | **P/S**: national aggregates cannot identify causes |
| A2 Adversarial | L4 | Critic with structured dimensions; competing mechanisms with gates | A red-team role (seeks evidence that refutes a hypothesis or strategy); a cross-family critic (§F) | F; purified critic benchmark | planted-defect recall and precision per type; false alarms on clean controls | **P** |
| A3 Counterfactual | L5, L7 | `share_shift` scenarios; competitor-offset scenarios; ABM interventions | Counterfactual questions on the world model ("absent X, the metric would be…"), scored where natural experiments resolve them | B, D | scored on resolved natural experiments only | **P** |
| A4 Second- and third-order | L5, L7 | competitor responses in strategy options (enum) | Response chains as explicit, forecastable subclaims (actor → response → effect on a metric), each resolvable | D, H | resolution of each link; calibration by order | **M** |
| A5 Cross-industry | L3, L6 | industry-neutral panel, `csv_panel` adapter, coverage contract; two UK segment markets | Reference classes of mechanisms across markets (an adoption S-curve, an incumbent mix cushion), with transfer tests | B; more markets with brand panels (Norway needs brand data) | out-of-market prediction against an in-market baseline | **S** now: one industry, one source |
| A6 Information-gap | L2, L6 | `information_gaps` records (missing measurement, value, the hypotheses they distinguish, sources); memory questions; autonomous research disabled | Value of information (§C); a gap's fate (filled, refuted or obsolete) | C, D | realised information gain per gap filled | **P** |
| A7 Temporal | L3, L5 | bitemporal store, as-of retrieval, window classes, publication lag, prospective protocol | Multi-horizon questions (short, medium, long), event-time alignment, regime-change detection | D | horizon-specific calibration | **P** |
| A8 Uncertainty | L5, L6 | 80% intervals, coverage, unvalidated-model labels, typed unavailability | Proper scoring (log, CRPS, Brier); calibration and resolution decomposition; calibrated abstention and forecastability scores; ensembles | D | reliability diagrams; sharpness at fixed calibration | **P** |

### B–H. Platform capabilities

| Capability | Already in the repository | Missing | Status |
|---|---|---|---|
| **B. Executable world model** | Claim ledger (12 types, status history, links); model registry (families, validation rule, backtests, reproducibility hashes); learning memory with gated promotion; ABM engine with calibration (SMM/ABC, synthetic) and sensitivity (Morris/Sobol) | A versioned **mechanism registry**: mechanism → relationships → causal assumptions → mathematical form → parameters → reference class → uncertainty, with provenance, validity interval and applicability boundary. Models PROPOSE revisions; an independent validator decides eligibility; promotion needs approval; every version can be rolled back. **Predictive gains are not causal validation** | **P** |
| **C. Active sensing** | information-gap records; provider catalogue with costs and quality checks; budget guard | A value-of-information estimator (expected reduction in decision loss, or the expected information gain between rival mechanisms); acquisition cost per source; stopping rules (budget, diminishing information gain, deadline); deduplication of requests across investigations; no autonomous fetching until approved | **M** (recording only) |
| **D. Continuous outcome resolution** | hypothesis_tests and test_results, the compiler's resolve, prospective registration with window classes, a tamper-evident registry export | **A unified prediction ledger** (tests, forecasts, estimates and simulations under one question schema); **a scheduler** (data refresh, then first-vintage resolution of pending questions, then scoring); proper scores; calibration and resolution metrics; question generation with **correlation control** (deduplicated windows; effective sample size); forecastability modelling and abstention | **P** |
| **E. Scientific evaluation** | pre-registered criteria hashes; matched arms; common evaluation method; leakage labels; critic benchmark; deterministic and mock baselines | **Sealed holdouts** (questions hidden from development); synthetic and perturbed historical benchmarks (renamed entities, shifted dates, injected shocks) to separate reasoning from recall; standalone-model baselines (the same model without Halibut); architecture ablations; **equal-budget** comparisons; the ten evaluation dimensions (below) | **P** |
| **F. Collective intelligence** | per-role routing with the router fingerprint in every id; Anthropic and Qwen providers (Qwen approval-gated) | Independent parallel reasoning; cross-family critique; error-correlation measurement; calibrated aggregation (weights from resolved performance); adaptive escalation (a cheap model first, escalate on low confidence or disagreement); marginal contribution per model and role | **M** (the routing foundation exists) |
| **G. Computational efficiency** | prompt cache with exact keys, verified reuse, `$defs` deduplication, compact evidence, parallel batch engine, spend ledger per role | Institutional-knowledge reuse across investigations (shared findings, not just cached replies); task deduplication; dynamic budgets per investigation value; conversion of verified recurring procedures into deterministic functions; joint quality, cost, latency and reliability reporting, including **data, infrastructure and human-validation costs** | **P** |
| **H. Strategic intelligence** | options, compliance checklist, share-shift simulation, experiment protocol, eligibility, approvals | **Strategist contract v3** (an engine-calculated quantity catalogue, numeric references, visible limits); decomposition of a decision into forecastable subclaims, causal assumptions, actor responses, scenarios and measurable outcomes; robust decision-making (regret over scenarios); game-theoretic response models (S: later, once response links are scored) | **P** |

**The ten evaluation dimensions (E),** each with a verified measure, an intermediate measure and a baseline:

| Dimension | Verified measure | Intermediate quality measure | Baseline |
|---|---|---|---|
| 1. Observation | evidence fidelity; reproduced numbers | explanations accepted | deterministic anomaly scan |
| 2. Hypothesis generation | original hypotheses consistent with the evidence available at the time | mechanism diversity | reference workforce |
| 3. Discrimination | resolved discriminating pairs | gated competing pairs | none, by construction |
| 4. Prediction | proper scores on resolved questions | informative share against persistence and momentum | persistence and momentum models |
| 5. Calibration | reliability and resolution | interval width | climatological baseline |
| 6. Causal reasoning | identified effects (designs only) | assumption completeness | none |
| 7. Strategic reasoning | resolved subclaims of options | contract validity; eligibility | template strategies |
| 8. Cross-domain transfer | out-of-market scores | – | in-market baseline |
| 9. Learning | score improvement on sealed holdouts after promotions | lessons proposed | frozen-knowledge rerun |
| 10. Efficiency | cost per verified outcome | tokens per call | standalone model |

**Intermediate quality (contract compliance, informativeness at registration) is always reported apart from
verified real-world intelligence (resolved, scored outcomes).**

## 2. Roadmap

Ordering principle: **make the evidence loop trustworthy and self-feeding first, then make comparisons, then widen
capability.** Every item is incremental, separately approved, and has measurable acceptance criteria.

| Priority | Item | Why now | Complexity, cost | Acceptance criteria |
|---|---|---|---|---|
| **1** | **Durable prospective resolution and a unified prediction ledger** (founder's #1, agreed) | 12 predictions are pending; without automatic resolution there is no verified feedback | medium; $0 API (data refresh is free) | every pending question resolves automatically on its first published vintage; registry hashes verified; proper scores recorded; idempotent reruns; the export to git is automatic |
| **2** | **Evaluation integrity v2.1** (moved up: was implicit in the founder's #3 and #4) | Every later comparison (strategist, Sonnet, Qwen) inherits today's artefacts: H3; dispute credit against contradicted rivals; double-counted windows; momentum-only baselines | low; $0 | recomputed offline on 2a and 2b as a **secondary, versioned readout** (the frozen results stay unchanged); tests for each artefact; correlated windows deduplicated (effective sample size reported) |
| **3** | **Strategist contract v3** (founder's #2) | K2 failed twice for a contract reason; 37% of v2 spend was wasted | low to medium; about $0.01–0.02 development run | ≥ 80% of options valid on the development dates; 0 undeclared numbers; every number traceable to a catalogue id or a labelled assumption; eligibility unchanged |
| **4** | **Critic validation** (founder's #4) | Self-contradictory critiques and an impure benchmark; the critic cannot be a gate until fixed | low; about $0.005 per benchmark run | a purified benchmark (one defect per item; clean controls grounded only in evidence); deterministic verification of the critic's cited numbers; recall and precision by type with confidence intervals; severity calibration |
| **5** | **Sealed holdouts and leakage-resistant benchmarks** (founder's #3, after 2) | Every retrospective result so far carries leakage risk | medium; $0–0.05 | a sealed question set, hashed and never used in development; perturbed historical variants (renamed brands, shifted dates); a standalone-model baseline on the same questions |
| **6** | **Role-specific model comparisons** (founder's #5) | Only meaningful after 2–5; otherwise model effects are confounded with known defects | medium; about $0.05–0.20 per comparison (priced from the official table at proposal time) | equal-budget arms; Sonnet generator first (§F), then the strategist on v3; Qwen benchmark; marginal contribution per role; error correlation between model families |
| **7** | **Active-sensing foundation** (founder's #6, first half) | Needs resolved outcomes (1) to estimate information value | medium; $0 until fetching is approved | gaps ranked by expected information gain divided by cost; deduplication across investigations; stopping rules; **no autonomous fetching** without approval; measured realised information gain |
| **8** | **Executable world-model foundation** (founder's #6, second half) | The registry, ledger and memory exist; the mechanism registry needs the resolution loop (1) to validate anything | high; $0 | versioned mechanisms with provenance, validity interval, applicability and rollback; independent validation gate; no causal promotion from predictive accuracy; promotion only by a named person |
| later | collective aggregation and escalation; second- and third-order chains; counterfactual scoring; robust decisions; cross-industry reference classes | depends on 1–8 and on more markets and data | – | per item, at proposal time |
| premature now | causal identification on national aggregates; game-theoretic equilibria; autonomous research; local Qwen in production; large multi-agent workforces | data or validation are missing | – | revisit when their dependencies are met |

**Where this challenges the founder's ordering:**
- **Evaluation integrity moves to #2**, because it is cheap and blocks credible conclusions from every later step.
- **Sealed holdouts follow the strategist and critic repairs**, because those repairs must be developed on
  development data before a holdout is spent on them.
- **Active sensing and the world model** are split and placed after the resolution loop, which they both depend on.

## 3. What this proposal does not do

It implements nothing and starts no paid run. It does not promote lessons, change the active contracts or the frozen
Step 2 benchmark, enable autonomous research, or act on the world. Each roadmap item needs its own approval,
pre-registration where it involves evaluation, and a spending cap.
