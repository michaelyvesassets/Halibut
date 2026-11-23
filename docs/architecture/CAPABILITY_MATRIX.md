# Capability Matrix: repository audit against Master Architecture v1.0

Audited 2026-11-23 at commit `f6e4904` (plus this documentation). Layers are those of
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
- 233 tests passing offline (`python -m pytest`).
- One SQLite store with migrations 1–5 (`src/halibut/data/store.py`).
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
| Model routing by task class | Missing | — | One provider and model per run (`intel/replay.py:llm_config_for`) |
| Structured-output enforcement | Missing | — | Prompt-instructed JSON plus `parse_json_text`. Cause of F2 in `docs/benchmark_haiku_v1_diagnostic.md` |
| Prompt and response cache | Implemented | `agents/llm.py:CachedProvider`, table `llm_cache` (migration 4) | Key includes provider, model, settings, role and prompt. Unparseable replies are not cached |
| Fail-closed spend control | Implemented | `agents/budget.py:SpendGuard`, `llm_spend_ledger` (migration 5) | Per-attempt reservation; application-wide caps ($5 total, $1 per run); `--max-usd` |
| Call accounting | Implemented | `agents/llm.py:record_call`, `run_costs`; table `agent_calls` | Tokens, cost, latency, cache hits, duplicate tool calls, outcome |
| Text of failed replies | Missing | — | The diagnostic gap behind F4 |
| Simulation compute pool and cache | Implemented | `batch.py` (process pool, `.halibut_cache/`) | Local machine only |
| Durable workflow / job runner | Missing | — | CLI commands run in the foreground. Ingest is idempotent (`data/ssb.py`, snapshots), but there is no resumable workflow state |
| Scheduler | Missing | — | |
| Relational storage | Implemented | `data/store.py` (SQLite, WAL, migrations 1–5) | Docstring calls the schema "Postgres/Supabase-compatible"; Postgres is **Planned** |
| Object store for raw data | Partial | `snapshots` table plus files under `data/raw/` (git-ignored) | Content-addressed; local disk |
| Observability | Partial | `fetch_log`, `ingest_runs`, `agent_calls`, run manifests (`export.py`) | No metrics or tracing system |
| Budgeted, polite HTTP | Implemented | `data/http.py:PoliteClient`, `BudgetExhausted` | Request, byte and time budgets; project User-Agent |
| Secrets handling | Implemented (single user) | Environment-only keys; `agents/llm.py:load_llm_config` rejects inline secrets; redaction in `agents/anthropic_provider.py` | No secret manager, no rotation |
| Authentication and tenancy | Missing | — | Single user, single tenant |
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
| Market definition as an object | Partial | `intel/market.py` (TOML spec: category × geography × competitors × mode) | Not stored as a world-model entity; one market |
| Documents linked to entities | Partial | `documents`, `document_entities` | GDELT metadata only |
| Assertions with confidence and contradiction handling | Missing | — | `epistemic_status` ∈ {observed, derived, asserted} only |
| Product, geography and organisation hierarchies | Missing | — | |
| Context assembly (token-budgeted slices) | Partial | `agents/orchestrator.py:evidence_bundle` | One bundle shape per role; not a general service |
| Tenancy | Missing | — | |

## L4 Autonomous AI Workforce

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Orchestrator | Partial | `agents/orchestrator.py:LLMAgentSet` | Fixed Research → Hypothesis → Critic pipeline; selective invocation; no planning or delegation |
| Research agent | Partial | `RESEARCH_SYSTEM`; offline store tools; network tools become `pending_approval` | 10 of 10 live calls truncated (F4) |
| Hypothesis agent | Partial | `HYPOTHESIS_SYSTEM`, `to_hypothesis` | **Chooses only kind and subject; numbers are engine-built (F1)** |
| Critic agent | Partial | `CRITIC_SYSTEM` | Critiques are stored; 3 of 3 live calls truncated |
| Deterministic reference workforce | Implemented | `intel/agents.py` (`ReferenceHypothesisAgent`, `ReferenceCritic`, `ReferenceScenarioDrafter`) | The benchmark baseline |
| Original hypothesis generation | **Missing** | — | The central architectural gap |
| Model-spec proposal by agents | Missing | — | `interfaces.py:ScenarioProposer` exists as a protocol only (Planned) |
| Structured agent communication | Partial | JSON bundles in, JSON out | No task contracts or schema registry |
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
| Model registry | Partial | `promotions` plus `memory.active(component)` | Versions of *components*, not a registry of specs |
| Firm and competitor strategic response | Missing | — | Products are static attributes |
| Multi-geography and multi-market models | Missing | — | |
| Supply, distribution and stock-outs | Partial | Availability in `engine.py`; stock-out counterfactual in `diagnostics.py` | Synthetic |
| Macro and economic forces | Missing | — | World Bank data is stored, not modelled |
| Hypothesis → model spec compilation | Missing | — | |
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
| LLM benchmark against reference | Implemented | `intel/benchmark.py` (plan on a throwaway copy, snapshot-hash check) | Failure taxonomy misclassifies format errors (F2) |
| Cost per useful validated result | Partial | `intel/replay.py:summarize`, `intel/benchmark.py` | Does not discount predictions the reference also made (F1) |
| Hypothesis testing (implications vs outcomes) | Missing | — | Predictions are scored; hypotheses as mechanisms are not tested |
| Matched and selection-aware comparison | Missing | Done manually in the diagnostic | |
| Duplicate-prediction removal | Missing | — | F1: 2 of 10 duplicates |
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
| Strategy and option generation | Missing | — | |
| Intervention modelling | Partial | `interventions.py` (synthetic ABM) | Not connected to real markets |
| Scenario comparison | Partial | `suite.py`, `experiment.py` (synthetic) | |
| Experiment design and protocol | Missing | — | |
| Compliance gate (jurisdictions, competition law) | Missing | — | |
| Outcome evaluation of real experiments | Missing | — | |

## L8 Command & Governance

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| CLI command surface | Implemented | `cli.py`, `analysis_cli.py`, `data/cli.py`, `intel/cli.py`, `learning/cli.py`, `agents/cli.py` | Commands listed below |
| Reports for people and agents | Implemented | `intel/report.py` (Markdown and JSON), `empirical/report.py`, `export.py` | |
| Approvals | Partial | `approved` / `approved_by` / `approved_on` in `configs/llm/*.toml`; `--yes`; `--max-usd`; `promote --approved-by`; `pending_approval` research proposals | Scattered; no approvals table |
| Budgets | Partial | LLM: `SpendGuard`; HTTP: `PoliteClient`; analyses: `time_limit_minutes` (`specs.py`) | No budget registry |
| Audit trail | Partial | `agent_calls`, `fetch_log`, `ingest_runs`, `promotions`, `knowledge_status_history`, ledger | Not unified; not tamper-evident |
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
| Epistemic labels | Partial | `epistemic_status` on entities, relationships and hypotheses; knowledge kinds | No unified claim-type taxonomy |
| Claim ledger (all types) | Missing | Split across `hypotheses`, `knowledge`, `prediction_scores` | |
| Agent-output schemas | Missing | — | |
| Temporal integrity guard | Partial | `store.as_of`, `empirical/backtest.py` audit (fails if a read is after the origin) | No vintage selection; no LLM-cutoff tagging |
| Prompt-injection defences | Missing | — | No untrusted full text yet, but required before adding any |

## CLI inventory (as of audit)

```
halibut validate | run | diagnose                               (simulation)
halibut suite | sensitivity | make-targets | calibrate | backtest  (analysis)
halibut data init | ingest | events | status | rebuild | quality
halibut intel smoke | investigate | replay | benchmark | compare
halibut learn demo | lessons | import-backtest | score | promote | rollback
halibut agents smoke | spend
```

## Schema inventory (`data/store.py`)

| Migration | Tables |
|---|---|
| 1 | sources, datasets, series, geographies, snapshots, observations, events, ingest_runs |
| 2 | fetch_log, entities, entity_identifiers, relationships, documents, document_entities, entity_observations, intel_runs, hypotheses |
| 3 | experiments, knowledge, knowledge_links, evaluations, promotions |
| 4 | prediction_scores, llm_cache, agent_calls; `knowledge.available_at` |
| 5 | knowledge_status_history, llm_spend_ledger |

## Test inventory

There are 27 test modules; offline tests are in `tests/`. Coverage by layer:

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
| L1 | 10 | 3 | 1 | 0 | 6 | Strong cost and reproducibility core; no routing, schemas, jobs or tenancy |
| L2 | 7 | 6 | 0 | 0 | 7 | Good for public statistics; no documents, finance, regulation or discovery |
| L3 | 5 | 5 | 0 | 0 | 3 | Sound bitemporal core; ontology thin; no assertions or hierarchies |
| L4 | 1 | 8 | 0 | 0 | 3 | **Weakest relative to vision**: no original hypotheses |
| L5 | 7 | 4 | 0 | 0 | 4 | Rich synthetic simulation; empirically weaker than baselines; no registry or firms |
| L6 | 8 | 2 | 0 | 0 | 4 | Solid learning discipline; hypotheses are not tested as mechanisms |
| L7 | 2 | 3 | 0 | 0 | 5 | Mostly missing beyond labels and safety rules |
| L8 | 2 | 4 | 0 | 0 | 2 | CLI-only; approvals scattered |
| EI | 4 | 2 | 0 | 0 | 3 | Validators exist; no unified ledger or schemas |
