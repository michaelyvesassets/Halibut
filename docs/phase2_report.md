# Phase 2 report: validation, sensitivity and calibration infrastructure

> **Every result in this report is synthetic.** It describes how the
> `coffee_vs_matcha_v1` model responds to its own assumptions and parameter
> ranges. Those ranges are assumptions too. Nothing here has been calibrated
> or validated against real markets. Intervals are 95% CIs across seeds,
> paired by seed unless stated otherwise.
>
> Engine 0.2.0. Commands, specs and run directories are listed under each
> section, so everything is reproducible from committed configs.

## 1. Backward compatibility

Schema 1.0 configs load unchanged and keep their hashes
(`coffee_vs_matcha_v1` → `e762931a…`). They reproduce engine 0.1.0 metrics
bit for bit, enforced by golden fingerprints in `tests/golden/`. The new
metrics are added as extra columns. The full experiment now takes 17.0 s
instead of 14.5 s (+17%), because of the per-consumer feasibility metrics.

## 2. Diagnostics (M8)

Command: `halibut diagnose configs/experiments/coffee_vs_matcha_v1.toml
--warmup {0,52} --stockout-counterfactual` (5,000 agents, 20 seeds).
Results are in `results/phase2/diagnostics_w*/`.

- **Accounting.** All 11 invariants pass for every row of all 100 runs, both
  cold and warmed.
- **The Phase 1 control is not stationary.**
  - Cold start: matcha purchase share changes by −0.030 ± 0.001 between the
    first and last 4-week windows.
  - With a 52-week warm-up the change is −0.006 ± 0.001, below the warning
    threshold. Every effect is then 35–40% smaller (combined: matcha share
    0.059 → 0.036), because matcha starts from a lower, self-consistent base.
- **No-purchase causes** (control, cold start; the causes are mutually
  exclusive and sum to all non-buyers):

  | Cause | Share of no-purchases |
  |---|---|
  | voluntary | 46% |
  | nothing affordable | 45% |
  | current product infeasible | 5% |
  | nothing in stock | 4% |

  Budget constraints therefore drive about half of all no-purchases. That is a
  property of the assumed lognormal budgets.
- **Switching is mostly churn.** About 7,600 coffee↔matcha switches per run
  give a net migration of only about 325 (churn ratio 23; 15 with warm-up).
- **My Phase 1 stock-out explanation is refuted.**
  - With every product always in stock (same random numbers), the price
    war's category contraction gets *larger*, not smaller: −117 ± 11 units
    against −66 ± 9 with stock-outs.
  - The combined scenario is unchanged: −134 against −135.
- **The actual mechanism is switching costs**
  (`configs/suites/switching_cost_mechanism_v1.toml`, 2,000 agents, 10
  seeds). Effect on category units versus control:

  | Variant | Price war | Combined |
  |---|---|---|
  | v1 | −31.7 ± 9.6 | −50.3 ± 11.5 |
  | no loyalty | −32.8 ± 9.3 | −60.8 ± 18.2 |
  | no switching resistance | −13.1 ± 7.1 | −19.5 ± 8.4 |
  | neither | **+23.0 ± 7.7** | **+46.0 ± 10.7** |

  Consumers drawn to matcha take on matcha as their current product. Switching
  resistance then penalises returning to coffee, and some drop out instead.
  Remove the habit structure and the same interventions *expand* the
  category.

## 3. Assumptions vs interventions (M9, M11)

Suite: `configs/suites/drift_warmup_v1.toml`, 4 drift models × {cold,
52-week warm-up}, 2,000 agents, 10 seeds. Results are in
`results/phase2/suites/drift_warmup_v1_*`.

| Variant | Control matcha share | Price war | Distribution push | Influencer campaign | Combined |
|---|---|---|---|---|---|
| conformity, cold (v1) | 0.020 | +0.014 | +0.012 | +0.0006 | +0.040 |
| none, cold | 0.054 | +0.032 | +0.030 | +0.0023 | +0.092 |
| baseline reversion, cold | 0.024 | +0.017 | +0.014 | +0.0005 | +0.045 |
| exposure reinforcement, cold | 0.036 | +0.025 | +0.021 | +0.0010 | +0.068 |
| conformity, warm | 0.013 | +0.008 | +0.006 | +0.0007 | +0.024 |
| exposure reinforcement, warm | 0.032 | +0.021 | +0.018 | +0.0007 | +0.058 |

The treatment columns are the effects on matcha purchase share versus
control.

**Robust: matcha share and adoption gains.**
- Every intervention raises matcha share and adoption significantly under
  every one of the 8 assumption variants.
- The treatment ranking (combined > price war > distribution push >
  influencer campaign) is identical in all 8.

**Not robust: effect sizes.** The drift model alone changes each effect by up
to about 4×. ANOVA of matcha share splits the variance as follows:

| Source | Share of variance |
|---|---|
| assumption variant | 51% |
| intervention | 41% |
| variant × intervention | 7% |
| seed noise | 1% |

**Not robust: category contraction.** The sign of the category-units and
no-purchase effects *reverses* across variants for the distribution push and
combined scenarios. For the price war and influencer campaign the sign stays
negative but is not significant in every variant.

**Revenue effects are robust in sign.** The price war and combined scenarios
lower category revenue; the distribution push raises it.

**Warm-up only matters when tastes drift.** Under no drift, the cold and warm
variants give identical end-window results. The two runs start from
different states (367 of 2,000 consumers have a different current product)
but converge to identical choices by about week 8. Without drift, the only
persistent state (the current product) has a few weeks of memory. The long
Phase 1 transient therefore comes entirely from preference drift.

## 4. Global sensitivity: Morris screening (M12)

Spec: `configs/sensitivity/coffee_vs_matcha_gsa_v1.toml`. It varies 19
parameters over assumed ranges, with 20 trajectories (400 points) × 3 seeds,
on 2,000 agents. Results are in `results/phase2/sensitivity*/`. Screening
completed under the conformity, none and baseline-reversion drift models.

- **Decision temperature dominates.** It has the largest `μ*` for control
  matcha share and for the price war, distribution push and combined effects
  on share. The logit noise scale sets how strongly every utility difference
  translates into choices, so the model's conclusions are most sensitive to a
  parameter that is only identified *relative to* the utility weights. A
  calibration has to fix this scale.
- **Different outputs have different drivers:**
  - Category demand and no-purchase: budget level, preference weight, outside
    utility, consumption and price sensitivity.
  - The combined effect on category units: coffee price, matcha price and
    price sensitivity.
  - The influencer effect: matcha availability, then temperature and social
    susceptibility. This output also has the largest seed-noise share (8%).
- **Interactions are large.** `σ/μ*` is between 0.7 and 1.2 for the main
  drivers of the combined effect, so effects are far from additive. That
  justifies the variance decomposition in Sobol.
- **Rankings are stable across drift models.** Spearman correlation of `μ*`
  for the combined share effect: 0.88 (conformity vs none) and 0.95
  (conformity vs reversion). The control transient is the exception
  (ρ = 0.57). There, drift rate and preference weight dominate under
  conformity, while availability dominates with no drift.
- **Noise floor.** Inactive drift parameters (`reversion_rate` and
  `decay_rate` under conformity) have `μ* = 0` exactly, as common random
  numbers guarantee. `rewire_prob` is nearly inert except for the influencer
  effect. Noise share is ≤ 1% for every output except the influencer effect
  (8%).

## 5. Calibration infrastructure (M13)

The framework is complete and tested:

- moments (window means, paired effects, arc elasticities);
- the method-of-simulated-moments objective;
- differential evolution plus Nelder–Mead search;
- ABC rejection;
- Jacobian identifiability diagnostics and objective slices;
- twin-target generation.

The elasticity estimator matches the closed-form logit own-price and
cross-price elasticities within 5–8%.

In the twin test, the optimiser beat the truth's objective and the ABC 90%
intervals covered the truth. The diagnostics found a strong correlation
(|r| > 0.5) between outside utility and price sensitivity, because both raise
no-purchase. The point estimate of outside utility missed the truth by 22% of
its range. That miss is what weak identifiability looks like, and it is the
reason calibration reports intervals, not point estimates.

The synthetic targets for the coffee-vs-matcha twin
(`configs/calibration/coffee_vs_matcha_twin_v1_targets.toml`) were
generated, but **the full twin calibration has not been run** (see §6).

## 6. Incomplete runs (deferred, not failed)

The background job running the last three Morris screens was attached to the
Claude Code session and stopped when that session ended:

- `none` and `baseline_reversion` finished and wrote their results;
- `exposure_reinforcement` was interrupted with 450 of 1,200 tasks done. No
  results were written, but the completed tasks are in the cache.

Deferred analyses and their estimated costs on this 2-core machine:

| Analysis | Estimated cost | Command |
|---|---|---|
| Morris, exposure reinforcement (resumes from cache) | ~4 min | `halibut sensitivity configs/sensitivity/coffee_vs_matcha_gsa_v1.toml --set 'preference_drift.model="exposure_reinforcement"' --out results/phase2/sensitivity_exposure_reinforcement` |
| Sobol, 8 parameters, N = 256 | ~30–45 min | add `replicates = 2` under `[sensitivity.sobol]`, then `halibut sensitivity … --method sobol --dry-run`, then `--yes` |
| Twin calibration | ~11–16 min | `halibut calibrate configs/calibration/coffee_vs_matcha_twin_v1.toml` |

The Sobol parameter list in the spec (temperature, preference weight, coffee
and matcha price, matcha availability, social weight, budget scale, drift
rate) should be updated from the Morris results before running.

## 7. Limitations and uncertainty

- **Conditional results.** All sensitivity findings are conditional on the
  assumed parameter ranges. All robustness classes are conditional on the 8
  assumption variants and on 10 seeds per variant.
- **Smaller analysis population.** The analyses use 2,000 agents. A
  size-convergence check is implemented
  (`halibut diagnose --size-convergence`) but has not been run.
- **Morris ranks, it doesn't decompose.** The large `σ/μ*` values mean
  rankings can change in other regions of parameter space.
- **Runtime estimates.** These under-predicted heterogeneous designs by about
  1.4×. A measured overhead factor is now included; treat estimates as ±30%.
- **No real-data calibration.** There are no empirical targets, and twin
  experiments only show the method works on this model.

## 8. Recommended next steps

1. Finish the deferred runs. Resume the exposure Morris screen (~4 min), then
   run Sobol on the 8 screened parameters and the twin calibration, about an
   hour in total.
2. **Normalise the logit scale.** Fix `temperature = 1` and treat the utility
   weights as the free parameters. Temperature cannot be identified
   separately from the weights, and it currently dominates the sensitivity
   results.
3. **Revisit the habit structure before trusting any category-level
   conclusion.** Contraction versus expansion hinges on switching resistance.
   That structure needs empirical grounding, for example from repeat-purchase
   and switching data.
4. **Source first empirical targets**: category share, price elasticities and
   adoption curves. Then run a real calibration, with holdout checks reported
   separately from fitting.
