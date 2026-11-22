# Halibut Phase 2 analysis methods

> Every analysis here is applied to a **synthetic, uncalibrated** model. Its
> results describe how the model responds to its own assumptions and
> parameters. They are not evidence about real markets.

## Experiment suites: assumptions vs interventions (`halibut suite`)

A suite is a full factorial design: **variants** (assumption sets, each
defined by config overrides) × **scenarios** (interventions plus control) ×
**seeds**. Every (variant, seed) pair shares one world and common random
numbers across scenarios. For an end-window outcome `Y(a, s, r)` (variant `a`,
scenario `s`, seed `r`), the suite reports:

| Quantity | Definition |
|---|---|
| intervention effect under `a` | `τ_a,s = mean_r [Y(a,s,r) − Y(a,control,r)]` |
| assumption effect on the baseline | `mean_r [Y(a,control,r) − Y(ref,control,r)]` |
| interaction | `mean_r [τ_a,s,r − τ_ref,s,r]`: how far the intervention effect depends on the assumption |
| ANOVA shares | balanced layout, seed as a block. The sums of squares for variant, scenario, variant×scenario, seed and residual add to the total. The residual pools seed×factor interactions (Monte Carlo noise). |
| ranking agreement | Kendall's τ between each variant's ranking of treatments and the reference variant's ranking |

All intervals are paired-by-seed Student-t 95% CIs.

**Robustness classes** for each (treatment, metric) pair:

- `robust`: the 95% CI excludes 0 with the same sign in **every** variant;
- `reversed`: significantly positive in at least one variant and
  significantly negative in at least one other;
- `assumption_dependent`: everything else, i.e. significant only in some
  variants. A non-significant effect is never called robust.

Caveats:

- The classes are conditional on the variants chosen. "Robust" means robust
  *to these alternatives*, not to every plausible model.
- They are also conditional on the number of seeds. With few seeds, small
  effects come out `assumption_dependent` because the intervals include zero,
  not because the assumptions matter.
- Variants that change population parameters also change the worlds. Pairing
  by seed still blocks the shared randomness, but the worlds are not identical
  across such variants.

**Runtime control.** Before running, a suite measures the cost of one scenario
run per variant (`seconds_per_scenario_run`) and prints an estimate. It
refuses to start above `time_limit_minutes` unless given `--yes`.
`--dry-run` prints the estimate only. Results are cached by config hash, seed,
scenario list, engine version and evaluator version.
