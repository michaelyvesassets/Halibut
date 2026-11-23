# M21: first empirical backtest (Norway BEV share of new private cars)

> **Scope.** This is an out-of-sample forecast evaluation for one
> industry-level displacement case. It does not establish causal effects of
> any policy, and it does not test competitor-targeted market engineering.
>
> Data: Statistics Norway (CC BY 4.0). Each forecast used only data available
> at its origin. History reflects today's values, since SSB publishes no
> vintages.
>
> Spec: `configs/empirical/norway_bev_v1.toml` (committed before any result
> was generated). Engine 0.3.0. Results:
> `results/backtests/norway_bev_v1_18d3cb62b078/` (git-ignored; reproducible
> from the store and the cache).

## Design

- **Origins:** 2016-01-15, 2019-01-15, 2022-01-15 and 2024-01-15. Data at
  each origin runs through the previous December.
- **Horizons:** 1–24 months, giving 96 scored forecasts per model, all on the
  same target months.
- **Halibut:**
  - 4,000 agents, monthly ticks from 1995;
  - purchase opportunity 0.5% per month;
  - social exposure to neighbours' owned cars;
  - 6 fitted parameters: BEV and hybrid intercept and trend, social weight,
    fuel-price coefficient;
  - temperature fixed at 1;
  - 282 objective evaluations × 2 seeds per origin;
  - forecasts from 4 seeds.
- **Runtime.** Measured 5.3–7.0 min per origin (estimates 6.4–9.3 min);
  24.8 min in total on 2 workers. Each origin was run as a separate
  foreground command with cached, resumable work.

## Results

MAE in percentage points of BEV share, with 96 forecasts per model:

| Model | MAE | RMSE | Bias | MASE | 80% coverage | 95% coverage |
|---|---|---|---|---|---|---|
| **logistic** | **5.38** | 6.63 | −2.77 | 1.15 | 0.80 | 0.86 |
| **Bass** | **5.45** | 6.74 | −2.97 | 1.16 | 0.80 | 0.85 |
| Halibut | 7.42 | 8.50 | −2.14 | 1.66 | 0.54 | 0.81 |
| Holt | 8.52 | 10.39 | +3.93 | 2.31 | 0.98 | 0.99 |
| drift | 8.94 | 11.44 | −1.19 | 1.77 | 0.95 | 0.98 |
| linear trend | 8.94 | 12.24 | +3.95 | 2.85 | 0.56 | 0.71 |
| naive | 11.04 | 13.50 | −9.99 | 2.11 | 0.96 | 0.98 |

**Simple diffusion baselines outperform Halibut.** The logistic and Bass
curves beat it overall, at 7–12 and 13–24 months ahead, and at 3 of 4
origins. Halibut beats the random-walk-type baselines (naive, drift, Holt) and
the linear trend overall.

MAE by horizon:

| Model | h 1–6 | h 7–12 | h 13–24 |
|---|---|---|---|
| logistic | 6.32 | 4.84 | 5.17 |
| Bass | 6.42 | 4.79 | 5.30 |
| Halibut | 6.71 | 7.42 | 7.77 |
| Holt | 5.93 | 8.62 | 9.77 |

MAE by origin. The models that beat Halibut are listed in `summary.json`.

| Origin | Halibut | logistic | best model |
|---|---|---|---|
| 2016-01 | 6.69 | 3.49 | drift 3.02 |
| 2019-01 | 6.49 | 6.53 | linear trend 6.11 (Halibut 2nd) |
| 2022-01 | 10.16 | 5.97 | Holt 4.25 |
| 2024-01 | 6.33 | 5.53 | linear trend 4.81 |

**Around the policy-dated anomalies,** all models do worse:

| Window | Halibut | logistic | best model |
|---|---|---|---|
| 2022-11..2023-03 | 11.6 | 8.1 | drift 4.2 |
| 2025-11..2026-03 | 6.2 | 4.9 | linear trend 4.0 |

Halibut has no policy inputs, so it can't anticipate pull-forward demand.

**Historical fit versus out-of-sample.** Halibut's in-sample MAE over the
last 36 months was 2.8, 5.4, 7.1 and 4.2 pp at the four origins. Its
out-of-sample MAE was 6.7, 6.5, 10.2 and 6.3 pp.

## Diagnosis (no new complexity added)

1. **Noise floor.** Observed monthly BEV share changes by 5–7 pp a month on
   average (2016–2025). Delivery timing and model launches are the likely
   cause, though that is not verified. Every model's monthly error is near or
   above this floor, and no model has MASE < 1.
2. **Unstable trend extrapolation.** Halibut's forecast bias was **+6.7 pp
   from 2016** and **−9.9 pp from 2022**; the logistic's was −0.4 and
   −4.4. The latent linear utility trend, compounded by contagion,
   extrapolates more erratically than a saturating curve.
3. **The social mechanism looks mis-specified.**
   - Exposure is to neighbours' *owned* cars. The observed BEV fleet share
     was 7% in 2018, 16% in 2021 and 32% in 2025, while sales shares were
     30–96%.
   - The fit pushes social weight to 94–95% of its range at two origins,
     which suggests the model wants more contagion than fleet exposure can
     supply. A mechanism the data cannot discipline adds variance, not skill.
4. **No information advantage.** Without prices or household-level data, the
   agent model is a 6-parameter curve that fits no better in sample than the
   3-parameter curves. In-sample residual SD at the 4 origins: 3.6, 5.3, 8.5
   and 5.7 pp for Halibut, against 2.8, 5.2, 7.5 and 6.3 for the logistic.
5. **Under-converged calibration.** The objective was still falling sharply
   in the final Nelder–Mead steps (for example 104 → 19 at the 2016 origin),
   and objective per moment is 0.6–8. Part of the gap may be optimisation,
   not structure. It is not yet known how much.
6. **Intervals are too narrow.** 80% intervals covered 54% of outcomes,
   because parameter uncertainty is excluded.

## What this does and does not show

- It **does** show that the pipeline is working:
  - data provenance;
  - as-of controls with audits;
  - common evaluation against baselines;
  - resumable runs and honest reporting.
- It **does** show that, for this target, a classical substitution curve
  forecasts better than the current agent specification.
- It **does not** show that agent-based modelling is useless here. The
  current specification lacks the information (prices, incentives as inputs,
  heterogeneity data) that would let mechanisms add value.
- It **does not** validate competitor-targeted displacement, causal policy
  effects, or any synthetic Phase 1–2 conclusion.

## Recommended next steps (in order; none adds complexity for score alone)

1. **Calibration-convergence check at one origin.** Larger DE budget and
   restarts (~15–20 min). This separates optimisation error from structural
   error before any model change.
2. **Specification test on the social mechanism.** Same origins:
   - social weight = 0 (pure latent trend);
   - `last_purchase` exposure instead of fleet exposure.

   Adopt a mechanism only if it improves *out-of-sample* error at most
   origins.
3. **Evaluate on quarterly averages as well as monthly.** That separates
   delivery noise from trend error. Report both.
4. **Add parameter uncertainty to intervals,** for example ABC over the
   calibration objective, and re-check coverage.
5. **Information the mechanisms could use:**
   - a verified, sourced price-gap or incentive-value series (needed before
     policy enters as an input, and then only with the effect estimated, not
     assumed);
   - charging-point history.
6. **Competitor-targeted validation needs brand- or model-level data**, such
   as model-level registrations. That is the true test of "Trend
   Annihilation" as defined. A source check comparable to M15 should come
   first.
