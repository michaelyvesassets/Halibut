# Halibut

Halibut AI is a long-term market-intelligence and socioeconomic simulation
platform. This repository holds an agent-based, reproducible **synthetic
market simulation engine**:

- **Phase 1** built the engine.
- **Phase 2** added the infrastructure for validation, sensitivity analysis
  and calibration.

The first experiment is competitive displacement of coffee by matcha.

> **All results produced by this engine are synthetic.** The parameters are
> illustrative and have not been calibrated or validated against empirical
> data. Every output is labelled `"result_type": "synthetic_simulation"` and
> `"empirically_validated": false`. Calibration so far uses only
> model-generated ("twin") targets, labelled `calibrated_to_synthetic`.

## Install

```bash
pip install -e ".[dev]"     # numpy, scipy; dev: pytest
python -m pytest            # full test suite, including Phase 1 golden reproduction
```

## Commands

```bash
# Single experiments (Phase 1)
halibut validate CONFIG
halibut run CONFIG [--scenario S] [--replicates N | --seed S] [--warmup W] [--out DIR]

# Diagnostics (accounting invariants, switching, stock-outs, transients, MC precision)
halibut diagnose CONFIG [--warmup W] [--stockout-counterfactual] [--size-convergence 2000,5000]

# Analyses. Each prints a measured runtime estimate first, refuses to start
# above the spec's time limit without --yes, and supports --dry-run.
halibut suite configs/suites/drift_warmup_v1.toml                         # assumptions vs interventions
halibut sensitivity configs/sensitivity/coffee_vs_matcha_gsa_v1.toml      # Morris (default)
halibut sensitivity SPEC --method sobol [--set 'preference_drift.model="none"']
halibut make-targets configs/calibration/coffee_vs_matcha_twin_v1.toml \
    --template configs/calibration/coffee_vs_matcha_twin_template.toml --output TARGETS.toml
halibut calibrate configs/calibration/coffee_vs_matcha_twin_v1.toml

# Real-world data (Phase 3): provenance catalogue, bitemporal store, quality checks
halibut data init
halibut data ingest                       # network: Statistics Norway (CC BY 4.0), no key
halibut data events configs/data/events_norway_ev.toml
halibut data quality                      # writes data/quality_report.json
halibut data rebuild --into NEW.sqlite    # recreate a store from raw snapshots only

# Market intelligence (Phase 4)
halibut intel smoke                                              # one live request per provider
halibut intel investigate configs/intel/markets/uk_cars_bev_v1.toml --dry-run
halibut intel investigate configs/intel/markets/uk_cars_bev_v1.toml [--offline]

# Closed learning loop and agents (Phase 5; offline, mock LLM; Anthropic/Qwen require approval)
halibut intel replay configs/intel/markets/uk_cars_bev_v1.toml --agents reference
halibut intel replay configs/intel/markets/uk_cars_bev_v1.toml --agents mock --faults
halibut intel compare results/intel/replays/*.json

# Claude agents vs the fixed-rule reference (pip install -e '.[anthropic]'; key from ANTHROPIC_API_KEY)
halibut agents smoke --dry-run                       # Haiku 5.5 request settings and worst case; nothing sent
halibut agents spend                                 # application-wide ledger (all providers)
halibut intel benchmark configs/intel/markets/uk_cars_bev_v1.toml --agents anthropic --dry-run
halibut intel benchmark configs/intel/markets/uk_cars_bev_v1.toml --agents anthropic --model claude-sonnet-5-5 --dry-run
halibut intel benchmark configs/intel/markets/uk_cars_bev_v1.toml --reconstruct rpf5673fab06   # offline, from cache
halibut intel coverage configs/intel/markets/uk_cars_bev_v1.toml [--as-of 2024-05-23]       # market coverage contract
halibut learn score [--as-of DATE]        # score predictions whose outcomes are published

# Intelligence loop (R2; offline with mock agents; paid agents need a granted approval)
halibut investigation run configs/intel/markets/uk_cars_bev_v1.toml --as-of 2025-05-23 --evaluate-at 2026-11-23
halibut investigation run tests/fixtures/market_plant_milk_panel.toml --as-of 2024-06-30 --evaluate-at 2025-06-30
halibut investigation list | show ID [--json] | schema
halibut investigation validate configs/intel/markets/uk_cars_bev_v1.toml --origins 2025-11-23 2026-05-23 --evaluate-at 2026-11-23 --max-usd 0.06   # plan; add --live --approval ID to run
halibut approvals list | request paid_run investigation:MARKET --by NAME --max-usd X | decide ID granted --by NAME

# Learning memory and evaluation (Phase 4)
halibut learn demo                        # baseline -> change -> evaluation -> lesson -> informed next experiment
halibut learn lessons --tag domain:share_forecasting
halibut learn import-backtest results/backtests/<run>
halibut learn promote COMPONENT EVALUATION_ID --approved-by NAME   # explicit approval only
halibut learn rollback COMPONENT --approved-by NAME
```

Analyses run in parallel (`--workers`) and cache every simulation under
`.halibut_cache/`, keyed by config hash, seed, scenarios and engine version.
Output is identical for any number of workers.

## Layout

```
configs/experiments/   versioned experiment configs (schema 1.0 and 1.1)
configs/suites/        assumption suites (variants x scenarios x seeds)
configs/sensitivity/   parameter spaces and GSA specs
configs/calibration/   calibration specs, moment templates, synthetic targets
docs/model.md          the simulation model, assumptions, limitations
docs/analysis.md       Phase 2 statistical methods and their caveats
docs/phase2_report.md  Phase 2 findings (synthetic)
docs/m15_source_verification.md, docs/phase3_data.md   real-world sources and data quality
configs/data/          data catalogue, provider registry, sourced events, cross-table checks
configs/intel/markets/ market investigation specs
docs/phase4_intelligence.md   components, providers, competitor model, learning engine, results
docs/phase5_agents_learning.md  scorer, replay, agents, cost accounting, audit, demonstration
docs/benchmark_haiku_v1_diagnostic.md  first live Claude benchmark: diagnosis and limitations
docs/architecture/     Master Architecture v1.0 (accepted): layers, capability matrix, world model,
                       agents, simulation and learning, operating loop, roadmap, decisions
configs/llm/anthropic.toml      Claude provider: models, official prices, caps (inactive until approved)
configs/llm/qwen.toml           Qwen provider (inactive until approved)
src/halibut/
  config.py  distributions.py  population.py  market.py  network.py
  decision.py  influence.py  interventions.py  engine.py  metrics.py
  experiment.py  export.py           # simulation core and Phase 1 pipeline
  diagnostics.py                     # invariants, behavioural checks, counterfactuals
  params.py  specs.py  batch.py      # overrides, parameter spaces, parallel cached runner
  suite.py  sensitivity.py  calibration.py
  data/                              # catalogue, SQLite store, SSB adapter, events, quality (no engine coupling)
  data/providers/                    # World Bank, Wikidata, OSM/Overpass, GDELT, DfT (budgeted, logged)
  intel/                             # market specs, retrieval, signals, reference agents, workflow, reports
  learning/                          # learning memory, lineage, evaluation rules, promotions, prediction scorer
  agents/                            # LLM provider interface, cache, accounting; Research/Hypothesis/Critic
  validation.py                      # validation/safety engine
  interfaces.py                      # protocols for future AI integrations (no implementations)
  cli.py  analysis_cli.py
tests/                               # incl. golden fingerprints of engine 0.1.0
```

The simulation core does no I/O and makes no external calls. Analyses
interact with it only through configs (overrides are re-validated and hashed)
and the batch runner. That leaves room to add business agents, geographic
markets, AI reasoning and real-world data without changing the analysis layer.

## Backward compatibility

Schema 1.0 configs still load, keep their config hashes, and reproduce engine
0.1.0 results exactly. Golden tests in `tests/golden/` enforce this. Features
added in schema 1.1 (drift models, warm-up) default to the v1 behaviour, and
while they hold their defaults they are left out of the config hash.
