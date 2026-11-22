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

## Global sensitivity analysis (`halibut sensitivity`)

Parameters are mapped from the unit cube to their ranges (linear or log
scale). A `scale`-mode parameter multiplies the base value at several config
paths, for example the price-sensitivity medians of all segments, so that
segment heterogeneity is kept while the overall level varies.

**Outputs** are end-window means averaged over the same seeds at every design
point (common random numbers). They take three forms:

- `control.<metric>`: the level of a metric in the control;
- `effect.<scenario>.<metric>`: the paired effect, treatment − control;
- `transient.control.<metric>`: the control's end window minus its start
  window, i.e. how much the model drifts on its own.

**Morris screening.** `r` random one-at-a-time trajectories on a `p`-level
grid (default `p = 4`), with step `Δ = p/(2(p−1))`. Cost: `r(k+1)` points for
`k` parameters. Each trajectory changes every parameter exactly once, giving
an elementary effect per parameter: `EE_i = (f(x + s·e_i) − f(x))/s`, with
`s = ±Δ`. Reported per parameter:

- `μ`: the mean of the elementary effects;
- `μ*`: the mean of their absolute values (Campolongo et al. 2007), used to
  rank parameters;
- `σ`: their standard deviation. A large `σ` relative to `μ*` indicates
  nonlinearity or interactions with other parameters;
- a bootstrap 95% CI on `μ*`, resampling trajectories.

Effects are measured per unit of *normalised* range, so they compare across
parameters. Morris ranks parameters; it does not quantify variance shares.

**Sobol indices.** The Saltelli (2010) design: matrices `A` and `B` are the
two halves of a scrambled Sobol' sequence of dimension `2k`, with `N` a power
of 2. `A_B^(i)` is `A` with column `i` taken from `B`. Cost: `N(k+2)` points.
The estimators are Saltelli 2010 Table 2, (b) for first order and (f) for
total (Jansen). Both are defined for exactly this `A/B/A_B` design:

```
V    = Var([f(A); f(B)])
S_i  = mean(f(B)·(f(A_B^i) − f(A))) / V
S_Ti = mean((f(A) − f(A_B^i))²) / (2V)
```

- `S_i` is the share of output variance due to parameter `i` alone.
- `S_Ti` is its share including all interactions.
- `S_Ti − S_i` measures how much parameter `i` acts through interactions.

95% CIs come from a bootstrap over the `N` rows. An output with zero variance
gets undefined indices (reported as `null`), never fabricated values. Sobol
is run only on the parameters that Morris screening identifies as important;
all other parameters are held at their base values.

**Validation against analytic benchmarks** (automated tests):

- Ishigami (`a = 7`, `b = 0.1`): `S1` and `ST` within 0.02 of the analytic
  values at `N = 2^14`, and the bootstrap CIs cover the analytic values.
- Sobol' G function (`a = [0, 1, 4.5, 9, 99, 99]`): within 0.03 at `N = 2^13`.
- Morris on a linear function: `μ` and `μ*` exact, `σ = 0`.
- Morris on `x₀·x₁`: `σ > 0` for the interacting factors and `μ* = 0` for the
  inert one.
- Morris on the G function: the `μ*` ordering matches the analytic `S_T`
  ordering.

**Monte Carlo noise.** The fraction of output variance across the design that
is seed noise rather than parameter effect:

```
noise_share = mean_point Var_seed(Y) / n_seeds / Var_points(mean_seed Y)
```

A `noise_share` near 1 means the indices for that output are dominated by
noise and should not be interpreted. Common random numbers make the noise
correlated across points, so this estimate is conservative for differences.
**Dummy parameters:** under conformity drift, `reversion_rate` and
`decay_rate` are inactive, so their indices act as a noise floor.

**What the indices mean.** They are conditional on:

1. the stated parameter ranges, which are assumptions;
2. the drift model and the other fixed structural choices;
3. a synthetic, uncalibrated base model.

They rank which inputs *the model* is sensitive to. They do not identify
real-world drivers of demand.
