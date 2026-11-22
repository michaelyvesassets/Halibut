# Halibut

Halibut AI is a long-term market-intelligence and socioeconomic simulation
platform. This repository holds **Phase 1**: a reproducible, agent-based
synthetic market simulation engine written in Python. Its first experiment is
competitive displacement of coffee by matcha.

> **All results produced by this engine are synthetic.** The parameters are
> illustrative and have not been calibrated or validated against empirical
> data. Every output file is labelled `"result_type": "synthetic_simulation"`
> and `"empirically_validated": false`.

## Install

```bash
pip install -e ".[dev]"     # runtime dependency: numpy; dev: pytest
```

Requires Python 3.11 or later.

## Use

```bash
halibut validate configs/experiments/coffee_vs_matcha_v1.toml
halibut run configs/experiments/coffee_vs_matcha_v1.toml --out results
halibut run CONFIG --scenario price_war --replicates 5       # subset (control is always included)
halibut run CONFIG --seed 1 --seed 2
python -m pytest
```

A run writes `results/<experiment>_<config-hash>/` containing:

- `manifest.json`: provenance, versions and the resolved config.
- `summary.json`: per-scenario results and paired comparisons against control.
- `timeseries.csv`: weekly metrics for every scenario and seed.
- `aggregate.csv`: weekly means with 95% CIs across seeds.
- `segments.csv`: weekly metrics per consumer segment.

## Layout

```
configs/experiments/   versioned TOML experiment definitions
docs/model.md          the mathematical model, assumptions and limitations
src/halibut/
  config.py            schema, validation, config hash
  distributions.py     attribute distributions
  population.py        synthetic consumers (columnar numpy arrays)
  market.py            products
  network.py           Watts–Strogatz social network
  decision.py          multinomial logit choice with budget and stock constraints
  influence.py         preference drift
  interventions.py     scheduled price, availability, positioning, population and seeding changes
  engine.py            weekly simulation loop (no I/O)
  metrics.py           weekly market metrics
  experiment.py        scenarios × seeds, paired statistics, displacement decomposition
  export.py            JSON and CSV output
  interfaces.py        protocols for future AI integrations (no implementations)
  cli.py               command-line interface
tests/
```

The simulation core (`engine` and the modules it uses) does no I/O and makes no
external calls. Storage, the CLI and future AI or visualisation layers sit
outside it.

See [docs/model.md](docs/model.md) for the model.
