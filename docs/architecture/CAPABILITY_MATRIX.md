# Capability Matrix: repository audit against Master Architecture v1.0

Audited 2026-11-23 at commit `f6e4904`; **updated after R1 and R2** (see [R1_IMPLEMENTATION.md](R1_IMPLEMENTATION.md), [R2_IMPLEMENTATION.md](R2_IMPLEMENTATION.md)). Rows changed are marked *(R1)* or *(R2)*. Layers are those of
[MASTER_ARCHITECTURE.md](MASTER_ARCHITECTURE.md#3-recommended-architecture).

**Status legend**

| Status | Meaning |
|---|---|
| **Implemented** | Works and is covered by tests in `tests/`; usable today |
| **Partial** | Exists but covers only part of the architectural responsibility, or works for one case only |
| **Mocked** | A deterministic or offline stand-in exists; the real capability does not |
| **Planned** | Named in existing docs or schema comments as intended, but no code |
| **Missing** | Neither code nor a prior plan |

**Baseline facts**
- About 11,400 lines of Python under `src/halibut/`.
- 277 tests passing offline (`python -m pytest`): 233 from before R1, 26 added in R1, 18 added in R2.
- One SQLite store with migrations 1–8 (`src/halibut/data/store.py`).
- One deployable CLI: `halibut` (`src/halibut/cli.py`).
- Total live LLM spend to date: $0.02076. That is one Haiku smoke test and one 23-call
  benchmark, recorded in `llm_spend_ledger`.

---

## L1 Infrastructure & Execution

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| LLM provider interface | Implemented | `agents/llm.py` (`LLMProvider`, `LLMResponse`) | Replaceable; four implementations |
| Anthropic provider (official SDK) | Implemented | `agents/anthropic_provider.py`; `tests/test_anthropic_provider.py` (16 tests) | Live-tested once (smoke and benchmark). Haiku 5.5 default, Sonnet 5.5 priced |
| Qwen provider | Partial | `agents/llm.py:QwenProvider`; `configs/llm/qwen.toml`; `tests/test_qwen_readiness.py` | Offline-tested only; never called live |
| Mock provider | Mocked | `agents/llm.py:MockProvider`, `agents/mock.py` | Pipeline testing only. "Mock output is not intelligence" |
| Model routing by task class *(R2)* | Implemented | `agents/routing.py`, `configs/llm/routing.toml` | Role → model and output cap for the loop; one provider and spend guard per model; the legacy replay still uses one model per run |
| Structured-output enforcement *(R1)* | Implemented (offline-tested) | `epistemic/schemas.py`; `agents/anthropic_provider.py` (`output_config.format`); local validation in `agents/orchestrator.py` | Anthropic: enforced at generation (API-safe subset) **and** locally; Qwen and mock: locally only. Untested against the live API |
| Prompt and response cache | Implemented | `agents/llm.py:CachedProvider`, table `llm_cache` (migration 4) | Key includes provider, model, settings, role, prompt and *(R1)* output schema. Unparseable replies are not cached |
| Fail-closed spend control | Implemented | `agents/budget.py:SpendGuard`, `llm_spend_ledger` (migration 5) | Per-attempt reservation; application-wide caps ($5 total, $1 per run); `--max-usd` |
| Call accounting | Implemented | `agents/llm.py:record_call`, `run_costs`; table `agent_calls` | Tokens, cost, latency, cache hits, duplicate tool calls, outcome |
| Text of failed replies *(R1)* | Implemented | `agent_calls.response_excerpt` (migration 6), `agents/llm.py:record_call` | Up to 4,000 characters; typed outcomes (truncated, refusal, invalid_json, schema_invalid) |
| Simulation compute pool and cache | Implemented | `batch.py` (process pool, `.halibut_cache/`) | Local machine only |
| Durable workflow / job runner | Partial *(R2)* | `investigations` (append-only results), deterministic `input_id`, prompt cache | An identical rerun reproduces the same content from cache; there is no step-level resume or job queue |
| Scheduler | Missing | — | |
| Relational storage | Implemented | `data/store.py` (SQLite, WAL, migrations 1–5) | Docstring calls the schema "Postgres/Supabase-compatible"; Postgres is **Planned** |
| Object store for raw data | Partial | `snapshots` table plus files under `data/raw/` (git-ignored) | Content-addressed; local disk |
| Observability | Partial | `fetch_log`, `ingest_runs`, `agent_calls`, run manifests (`export.py`) | No metrics or tracing system |
| Budgeted, polite HTTP | Implemented | `data/http.py:PoliteClient`, `BudgetExhausted` | Request, byte and time budgets; project User-Agent |
| Secrets handling | Implemented (single user) | Environment-only keys; `agents/llm.py:load_llm_config` rejects inline secrets; redaction in `agents/anthropic_provider.py` | No secret manager, no rotation |
| Authentication and tenancy | Missing | — | Single user. Tenant columns exist on claims and validation records only (L3 row) |
| Reproducibility | Implemented | `config.py:config_hash`, `rng.py`, `export.py` manifests, `learning/lineage.py` | Golden tests: `tests/test_golden.py`, `tests/test_reproducibility.py` |

## L2 Information Acquisition

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Source and dataset catalogue with licences | Implemented | `data/catalogue.py`, `configs/data/catalogue.toml`, `configs/data/providers.toml` | Licence, attribution, universe dimensions |
| Statistics Norway connector | Implemented | `data/ssb.py`; `tests/test_data_ssb.py` | Norway BEV pilot data |
| UK DfT VEH0160 connector | Implemented | `data/providers/dft.py` | Makes and models, quarterly; universe is *first UK registrations* (includes imports) |
| World Bank connector | Implemented | `data/providers/worldbank.py` | Annual macro |
| Wikidata entity resolution | Implemented | `data/providers/wikidata.py` | P31, P749 (parent), P17, P452; overrides recorded as analyst assertions |
| OSM Overpass footprint | Implemented | `data/providers/overpass.py` | Presence counts, ODbL |
| GDELT GKG sample | Partial | `data/providers/gdelt.py` | Explicit sample of 15-minute files; metadata and tone only, no article text |
| RDW (Netherlands) | Missing | `configs/intel/markets/uk_cars_bev_v1.toml` comment | Host unreachable on 2026-11-23 |
| Content-addressed snapshots | Implemented | `snapshots` (migration 1), `data/store.py` | Re-ingesting identical bytes adds nothing |
| Availability dating | Partial | `observations.available_at`, `availability_basis` ∈ {published, estimated_lag, retrieval} | DfT history uses an **estimated** 121-day lag (F7); only Q2 2026 has a published date |
| Vintages and revisions | Partial | `observations.revision`, `UNIQUE(series_id, geo_id, period, revision)`; `ingest_runs.n_revised` | Schema supports revisions; historical vintages not acquired, so the store holds the latest file |
| Sourced events (policy, context) | Partial | `data/events.py`, `configs/data/events_norway_ev.toml` | Hand-curated, Norway only |
| Data-quality checks | Partial | `data/quality.py`, `configs/data/checks_norway_ev.toml`; `intel/audit.py` | Cross-table and audit checks for the datasets in use |
| Unstructured documents (full text) | Missing | — | |
| Company filings, financial data, prices | Missing | — | |
| Regulatory feeds | Missing | — | |
| Social and cultural data | Partial | GDELT tone as a media proxy | No narrative extraction |
| Commercial datasets | Missing | — | A founder decision (cost and licence) |
| Source discovery | Missing | — | Research agent can only *propose* network tools (`agents/orchestrator.py`, `PROPOSABLE_TOOLS`) |
| Refresh scheduling | Missing | — | Manual `halibut data ingest` |

## L3 World Knowledge Base

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Entities with kinds | Partial | `entities` (migration 2): company, brand, product, product_line, location, place, industry, market, segment | No consumer segment, narrative, regulation, supplier, distributor, economic force or institution kinds |
| External identifiers | Implemented | `entity_identifiers` | Wikidata QIDs, dataset codes |
| Relationships with validity time | Partial | `relationships(valid_from, valid_to, available_at, epistemic_status)` | Parent organisation, country, industry, located_at. No ownership stakes, no confidence |
| Bitemporal observations | Implemented | `observations`, `store.as_of()`, `AsOfView` | Period time × availability time |
| As-of retrieval with evidence ids | Implemented | `intel/retrieval.py` (`obs:`, `rel:`, `doc:`, `ent:`, `k:` ids) | Read-only, date-bounded |
| Derived metrics with recomputable definitions | Implemented | `intel/signals.py`; recomputed in `validation.py` | Share metrics, L4/P4 windows, decomposition |
| Universe and denominator discipline | Implemented | `data/catalogue.py` (`universe_dims`), `AsOfView.shares` | Shares only within one universe |
| Market definition as an object | Partial | `intel/market.py` (TOML spec: category × geography × competitors × mode) | Not stored as a world-model entity; *(R2)* two markets run through the loop (UK cars, fictional plant-milk panel) |
| Industry-neutral share panel *(R2)* | Implemented | `world/panel.py` (DfT and `csv_panel` adapters; 5 metric templates) | Reproduces the legacy DfT signals exactly; quarterly panels only |
| Cross-industry market coverage contract *(R1)* | Implemented | `world/coverage.py`, `configs/data/provider_coverage.toml`, `[measurement]` and competitor `identifiers` in `intel/market.py`; `halibut intel coverage` | Industry-neutral declaration checked against provider coverage; as-of evidence coverage per competitor; legacy keys still accepted |
| Documents linked to entities | Partial | `documents`, `document_entities` | GDELT metadata only |
| Assertions with confidence and contradiction handling | Missing | — | `epistemic_status` ∈ {observed, derived, asserted} only |
| Product, geography and organisation hierarchies | Missing | — | |
| Context assembly (token-budgeted slices) | Partial *(R2)* | `world/context.py:build_slice` (budgeted, as-of, anomaly scan, id enums); `reference_library` | One slice shape; no hierarchy levels yet |
| Tenancy *(R1)* | Partial | `claims.tenant`, `validation_records.tenant`; `ClaimLedger.as_of/get` filter own + public | Other tables have none; **must be extended before client data** (founder decision 7) |

## L4 Autonomous AI Workforce

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Orchestrator | Partial *(R2)* | `investigation/loop.py:run_investigation` (template plan, adaptive skips, stopping rules); legacy `agents/orchestrator.py:LLMAgentSet` kept for replays | No dynamic planning or delegation |
| Research agent | Partial | `RESEARCH_SYSTEM`; offline store tools; network tools become `pending_approval` | 10 of 10 live calls truncated (F4) |
| Hypothesis agent | Partial | `HYPOTHESIS_SYSTEM`, `to_hypothesis` | **Chooses only kind and subject; numbers are engine-built (F1)** |
| Critic agent | Partial | `CRITIC_SYSTEM` | Critiques are stored; 3 of 3 live calls truncated |
| Deterministic reference workforce | Implemented | `intel/agents.py` (`ReferenceHypothesisAgent`, `ReferenceCritic`, `ReferenceScenarioDrafter`) | The benchmark baseline |
| Original hypothesis generation *(R2)* | Implemented (mock-tested) | `agents/loop_agents.py` (generator role, schema `hypothesis_original.v1`), novelty via `world/context.py:reference_library` | Mechanisms, alternatives, assumptions, uncertainty, refutation criteria; quality with a live model is unmeasured |
| Model-spec proposal by agents *(R2)* | Implemented | `model_request` in the generator schema → `models/registry.py:register` | Family and numerical assumptions, bounds-checked; recorded as assumption claims |
| Structured agent communication *(R1)* | Partial | Versioned role schemas (`epistemic/schemas.py`), prompt versions v1 and v2 (`agents/orchestrator.py:PROMPTS`) | No task contracts between agents yet (R2) |
| Tool permissions | Partial | `OFFLINE_TOOLS`, `PROPOSABLE_TOOLS`, `validation.ACTION_TYPES_ALLOWED` | Coarse two-tier |
| Duplicate-work avoidance | Partial | Duplicate tool calls executed once and counted; cache | Nothing across investigations |
| Disagreement preservation | Partial | Critiques kept; lessons dedupe with "same-data repetitions" labels (`learning/memory.py`) | No adjudication-by-test workflow |
| Specialist agents (causal, competitive, strategy, simulation design, regulatory, financial, cultural) | Missing | — | |

## L5 Modelling & Simulation

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Agent-based consumer simulation | Implemented (synthetic) | `engine.py`, `population.py`, `network.py`, `decision.py` (multinomial logit), `influence.py`, `metrics.py`, `diagnostics.py` | Single category; weekly; common random numbers |
| Scheduled interventions | Implemented (synthetic) | `interventions.py` | Price, availability and similar levers |
| Experiment, suite and paired comparisons | Implemented | `experiment.py`, `suite.py` | Separates intervention effects from assumption effects |
| Global sensitivity analysis | Implemented | `sensitivity.py` (Morris, Sobol) | |
| Calibration (method of simulated moments, ABC) | Partial | `calibration.py` | **Synthetic twin experiments only** (stated in module docstring) |
| Empirical calibration and backtest | Implemented | `empirical/fit.py`, `empirical/backtest.py`, `empirical/measurement.py` | Norway BEV: calibrated ABM MAE 7.42 pp, beaten by logistic 5.38 and Bass 5.45 (`docs/phase3_results.md`) |
| Forecasting baselines | Implemented | `empirical/baselines.py` (naive, drift, Holt, linear, logistic, Bass) | |
| Intelligence share policies | Implemented | `intel/forecast.py` (level, momentum, last_quarter; naive and empirical intervals) | Promotable component `intel.prediction_policy` |
| Model registry *(R2)* | Implemented | `models/registry.py`, `model_versions` (migration 7) | 5 forecast families + scenario family; DiD and ABM registered as unavailable; rule `model-validation-v1`; univariate only |
| Firm and competitor strategic response | Missing | — | Products are static attributes |
| Multi-geography and multi-market models | Missing | — | |
| Supply, distribution and stock-outs | Partial | Availability in `engine.py`; stock-out counterfactual in `diagnostics.py` | Synthetic |
| Macro and economic forces | Missing | — | World Bank data is stored, not modelled |
| Hypothesis → model spec compilation *(R2)* | Implemented | `investigation/compiler.py:compile_implication`, `investigation/loop.py` | Implications → pre-registered tests; requested family + mandatory baselines per test target |
| Scenario drafts | Partial | `intel/agents.py:ReferenceScenarioDrafter` | Validated, synthetic preview only |

## L6 Learning & Evolution

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Prediction spec v2 and automatic scoring | Implemented | `learning/predictions.py` (`score_due`, interval score, Brier) | No hindsight; nothing scored twice |
| Historical replay with publication cutoffs | Implemented | `intel/replay.py` | 10 half-yearly origins |
| Shared learning memory with trust rules | Implemented | `learning/memory.py`; tables `knowledge`, `knowledge_links`, `knowledge_status_history` | Agent output enters as `proposed`; status is as-of |
| Versioned evaluation rule | Implemented | `learning/evaluate.py` (`paired-dev-holdout-v1`) | |
| Promotion with named approver; rollback | Implemented | `memory.promote/rollback`; `halibut learn promote --approved-by` | No promotions recorded yet |
| Experiment lineage | Implemented | `learning/lineage.py`, `experiments` | |
| R2 live validation runner (reference vs mock vs Claude) | Implemented (offline-tested; not run live) | `investigation/live_validation.py`, `investigation/evaluation.py`; `halibut investigation validate` | Pre-registered criteria; see [R2_LIVE_VALIDATION_PLAN.md](R2_LIVE_VALIDATION_PLAN.md) |
| LLM benchmark against reference | Implemented | `intel/benchmark.py` (plan on a throwaway copy, snapshot-hash check) | *(R1)* R1 failure taxonomy; `overlap_with_reference`; conditional-critic rule reported |
| Offline reconstruction of a past run *(R1)* | Implemented | `intel/benchmark.py:reconstruct`, `halibut intel benchmark --reconstruct` | Cached replies + recorded outcomes; no requests; reproduced the Haiku benchmark exactly |
| Cost per useful validated result | Partial | `intel/replay.py:summarize`, `intel/benchmark.py` | *(R1)* Counts unique predictions; the overlap with the reference is reported, but the ratio itself does not yet discount it |
| Hypothesis testing (implications vs outcomes) *(R2)* | Implemented | `investigation/compiler.py` (`resolve`, `hypothesis_status`), `hypothesis_tests`, `test_results` | Trend-consistency labelled; survival ≠ causal proof; one 2-quarter window per investigation |
| Matched and selection-aware comparison | Partial *(R1)* | `intel/benchmark.py:overlap` (matched identity count) | Selection-vs-random test not implemented |
| Duplicate-prediction removal *(R1)* | Implemented | `intel/replay.py:unique_predictions`; ledger `duplicates` links | Raw and unique counts reported |
| Error analysis | Partial | Manual diagnostics (`docs/phase3_results.md` §Diagnosis, the benchmark diagnostic) | |
| LLM-cutoff-aware evaluation | Missing | — | |
| Learning-loop demonstration | Implemented | `learning/demo.py`, `halibut learn demo` | |

## L7 Strategic Intelligence

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Strategic modes (enforcement, continuation, annihilation) | Partial | `intel/market.py` (`mode`, `focal`, `target`), `intel/__init__.py`, `intel/agents.py` | Used as hypothesis labels |
| Lawful-lever whitelist | Implemented | `validation.ALLOWED_LEVERS` | 9 levers |
| Prohibited-tactic detection | Implemented (backstop) | `validation.PROHIBITED` (regex) | Not a compliance review |
| Opportunity discovery | Missing | — | |
| Strategy and option generation *(R2)* | Implemented (mock-tested) | Strategist role (`strategy_options.v1`), `investigation/strategy.py:validate_option` | Competing options with every required field; effects are agent assumptions |
| Intervention modelling | Partial *(R2)* | `models/registry.py:share_shift` (scenario model on real baselines); `interventions.py` (synthetic ABM) | Assumption-driven, uncalibrated |
| Scenario comparison | Partial *(R2)* | Simulations per option × competitor response (`investigation/strategy.py:simulate`); `suite.py` (synthetic) | No ranking beyond the assumed effects |
| Experiment design and protocol *(R2)* | Implemented (draft only) | `investigation/strategy.py:experiment_protocol` | Pre-registration hash, power and MDE, guardrails; never executed |
| Compliance gate (jurisdictions, competition law) | Partial *(R2)* | `investigation/strategy.py:compliance_checklist`; `strategic_proposal` claim rules | Generic legal domains and mode-specific flags; always pending human review; not jurisdiction-specific law |
| Outcome evaluation of real experiments | Missing | — | |

## L8 Command & Governance

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| CLI command surface | Implemented | `cli.py`, `analysis_cli.py`, `data/cli.py`, `intel/cli.py`, `learning/cli.py`, `agents/cli.py` | Commands listed below |
| Reports for people and agents | Implemented | `intel/report.py` (Markdown and JSON), `empirical/report.py`, `export.py` | |
| Approvals *(R2)* | Implemented | `control/approvals.py`, `approvals` + `approval_decisions` (migration 7), `halibut approvals` | Named-person decisions; agents can't request paid runs or actions; legal review + outcomes for experiments; enforced for paid investigations; sole-operator self-approval of small paid runs with attestation (`configs/governance/approvals.toml`, ADR-019); single-use approvals (`approval_uses`, migration 8). Legacy flags still gate the older commands |
| Budgets | Partial | LLM: `SpendGuard`; HTTP: `PoliteClient`; analyses: `time_limit_minutes` (`specs.py`) | No budget registry |
| Audit trail *(R1)* | Partial | `epistemic/events.py` (`events_log`, hash-chained, append-only) for LLM calls, claims, validation outcomes, status changes, promotions and replays; older tables unchanged | Ingestion and fetches do not emit events yet |
| Permissions and roles | Missing | — | |
| Policy registry | Partial | `validation.py` constants | Hard-coded |
| Dashboard or console | Missing (by decision) | Earlier phases: "do not build a dashboard" | Deferred ([ADR-010](ARCHITECTURE_DECISIONS.md#adr-010)) |

## EI Epistemic Integrity plane

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Evidence-existence and as-of checks | Implemented | `validation.validate_hypothesis`, `retriever.exists` | |
| Metric recomputation | Implemented | `validation.py` + `intel/signals.compute_share` | Tolerance 1e-9 |
| Narrative-number check | Implemented | `validation.check_numbers`, `allowed_numbers` | |
| Evidence-quality audit | Implemented | `intel/audit.py` | Windows, denominators, universe, completeness |
| Epistemic labels *(R1)* | Implemented | Claim types in `epistemic/claims.py` | Legacy knowledge kinds kept; candidate explanations still stored there as `hypothesis` (see R1 §5) |
| Claim ledger (all types) *(R1)* | Implemented | `epistemic/claims.py`; tables `claims`, `claim_links`, `claim_status_history` (migration 6) | Written by the workflow and scorer; the scorer still reads `knowledge` |
| Agent-output schemas *(R1)* | Implemented | `epistemic/schemas.py` (research.v2, hypothesis_selection.v2, critic.v2, smoke.v1) | Enums from the bundle; never repaired |
| Failure taxonomy and validation records *(R1)* | Implemented | `epistemic/failures.py`; `validation_records` (migration 6) | Every rejection and drop recorded with class and raw item |
| Temporal integrity guard | Partial | `store.as_of`, `empirical/backtest.py` audit; *(R1)* claim evidence checked as of the claim's date, forecasts must resolve after it, claims invisible before `available_at`, status as of a date | No vintage selection; no LLM-cutoff tagging |
| Prompt-injection defences | Missing | — | No untrusted full text yet, but required before adding any |

## CLI inventory (as of audit)

```
halibut validate | run | diagnose                               (simulation)
halibut suite | sensitivity | make-targets | calibrate | backtest  (analysis)
halibut data init | ingest | events | status | rebuild | quality
halibut intel smoke | investigate | replay | benchmark [--reconstruct ID] | coverage | compare
halibut learn demo | lessons | import-backtest | score | promote | rollback
halibut agents smoke | spend
halibut investigation run | list | show | schema           (R2 loop)
halibut approvals list | show | request | decide           (R2 control plane)
```

## Schema inventory (`data/store.py`)

| Migration | Tables |
|---|---|
| 1 | sources, datasets, series, geographies, snapshots, observations, events, ingest_runs |
| 2 | fetch_log, entities, entity_identifiers, relationships, documents, document_entities, entity_observations, intel_runs, hypotheses |
| 3 | experiments, knowledge, knowledge_links, evaluations, promotions |
| 4 | prediction_scores, llm_cache, agent_calls; `knowledge.available_at` |
| 5 | knowledge_status_history, llm_spend_ledger |
| 6 *(R1)* | claims, claim_links, claim_status_history, validation_records, events_log (append-only triggers); `agent_calls.response_excerpt` |
| 7 *(R2)* | approvals, approval_decisions, model_versions, hypothesis_tests, test_results, investigations (append-only) |

## Test inventory

There are 33 test modules; offline tests are in `tests/`. R1 added test_epistemic_claims (7), test_epistemic_events (4), test_schemas_and_failures (9) and test_r1_pipeline (6); R2 added test_r2_loop (12) and test_r2_registry_approvals (6). Coverage by layer:

| Layer | Test modules |
|---|---|
| L1 | test_anthropic_provider (16), test_qwen_readiness (11), test_params_batch (7), test_reproducibility (5), test_golden (3) |
| L2 | test_data_ssb (8), test_providers (8), test_data_quality_events (5), test_data_store (10) |
| L3 | test_data_store, test_intel (4) |
| L4 | test_phase5 (10), test_intel |
| L5 | test_engine_v03 (7), test_decision (11), test_influence (7), test_interventions (6), test_network (5), test_population (4), test_metrics (4), test_diagnostics (11), test_drift_models (7), test_suite (5), test_sensitivity (9), test_calibration (5), test_empirical (9) |
| L6 | test_learning (6), test_phase5 |
| L8 | test_export_cli (4), test_config (6) |

## Summary by layer

Counts are rows of the tables above. Items noted as *Planned* inside a row (Postgres in L1; the `ScenarioProposer` protocol in L4) are counted under the row's own status.

| Layer | Implemented | Partial | Mocked | Planned | Missing | Verdict |
|---|---|---|---|---|---|---|
| L1 | 13 | 4 | 1 | 0 | 2 | Cost and reproducibility core; *(R1/R2)* schemas, failed-reply text, task routing; no job queue or tenancy |
| L2 | 7 | 6 | 0 | 0 | 7 | Public statistics plus *(R2)* a generic CSV panel provider; no documents, finance, regulation or discovery |
| L3 | 7 | 6 | 0 | 0 | 2 | Bitemporal core; *(R1/R2)* coverage contract, industry-neutral panel, context slices; no assertions or hierarchies |
| L4 | 3 | 8 | 0 | 0 | 1 | *(R2)* original-hypothesis generator, critic and strategist roles (mock-tested); live quality unmeasured |
| L5 | 9 | 3 | 0 | 0 | 3 | *(R2)* model registry with validation; univariate; scenario model assumption-driven; no firms or causal identification |
| L6 | 11 | 3 | 0 | 0 | 1 | *(R2)* hypothesis tests and statuses, forecast scoring; one evaluation window per investigation |
| L7 | 4 | 4 | 0 | 0 | 2 | *(R2)* competing options, simulations, compliance checklist, draft protocols; no opportunity discovery or real experiments |
| L8 | 3 | 3 | 0 | 0 | 2 | *(R2)* approvals record with authorization checks; CLI and read-only API; no console (deferred) |
| EI | 8 | 1 | 0 | 0 | 1 | *(R1)* ledger, schemas, events, failure taxonomy; prompt-injection defences missing |
