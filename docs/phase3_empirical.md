# Phase 3 empirical layer: measurement, calibration, backtesting (M19–M20)

Pilot: displacement of fossil new private cars by battery-electric cars in
Norway (`configs/empirical/norway_bev_v1.toml` and
`norway_bev_template.toml`). This is an *industry-level* validation case for
Halibut's displacement mechanics. It does not test competitor-targeted market
engineering (displacing a specific brand, firm or product). The code in
`halibut/empirical/` is generic: series, products, covariates, parameters and
origins all come from the pilot file.

## 1. Engine additions (engine 0.3, schema 1.2; all off by default)

| Feature | Config | Effect when enabled |
|---|---|---|
| Purchase opportunity | `[purchase_opportunity] enabled, probability` | Each tick, each consumer is in the market with probability *p*, drawn from its own random stream. Out-of-market consumers choose nothing and keep their current product. The new no-purchase cause `np_not_in_market` counts them. |
| Social signal | `social_influence.signal = "current_product"` | Neighbour exposure uses what neighbours *own* rather than what they bought last tick, the right signal for durable goods. It also feeds preference drift. |
| Drivers | `[[drivers]]` of type `product_path` | Exogenous, observed paths for product attributes, applied in every scenario including control. A path holds its last value beyond its end. `product_path` is also allowed as an intervention in non-control scenarios. |
| Expected shares | metrics `expected_share_<p>`, `in_market` | Always computed: `mean_i P_ip / mean_i (1 − P_i0)`. This is the expected purchase share without the sampling noise of realised choices. It uses no extra random numbers. |

Schema 1.0 and 1.1 configs are unchanged and still reproduce engine 0.1.0
results bit for bit (golden tests). New fields are left out of the config
hash while they hold their defaults.

## 2. Measurement model

- **Observed.** For product *p* in month *t*: `s_obs(p,t) = Σ_{series of p} x(t) / Σ_{all product series} x(t)`.
  The series are SSB 14020 counts in **one universe**: `TypeRegistrering = N`,
  new private cars. The store refuses to compute a share across universes.
  Product mapping: bev = 19 (Electric/zero-emission), hybrid = 21, fossil = 20
  plus 6 ("Other fuel", < 0.1%, merged by assumption).
- **Simulated.** `expected_share_p` at tick *k*, where tick *k* is month
  `1995-01 + k`.
- **Moments:**
  - annual means of monthly shares (bev, hybrid) for full years from 2005;
  - the last 6 observed months individually.

  Standard errors are **assumptions** (1.5 pp annual, 2.5 pp monthly). They
  combine revision risk (≤ 0.11 pp, M18), simulation noise and structural
  model error, and they set how tightly the model is asked to fit.
- **Why expected rather than realised shares.** With realistic purchase
  frequencies only about 20 of 4,000 agents buy each month, so realised
  monthly shares would be dominated by sampling noise.

## 3. What is fitted, assumed or unidentified

| Class | Items |
|---|---|
| **Fitted** (per forecast origin, data up to that origin only) | BEV and hybrid quality *intercept* and linear *trend* (utility relative to fossil); social weight (neighbour fleet exposure); fuel-price coefficient |
| **Assumptions** (fixed, stated in the template and spec) | decision temperature = 1 (scale normalisation); monthly purchase probability 0.5% (≈ 2025 new registrations / fleet); 1995 fleet all fossil; taste heterogeneity Beta(2,2); susceptibility Beta(2,2); Watts–Strogatz network (k = 10, p = 0.1); in-market households always buy; zero prices; full availability; fuel-shock loadings (fossil −1, hybrid −0.5, BEV 0); standard errors |
| **Unidentified** (fixed at neutral values) | brand loyalty, switching resistance, preference drift, household-level switching, car prices and price sensitivity, charging infrastructure, supply constraints |

**The linear latent trend absorbs everything unobserved**: falling battery
prices, range, model choice, charging and the incentives. Extrapolating it
*is* the forecast assumption. It is not a causal model of any policy.

**Policy events are not model inputs** in this pilot. The as-of view lists
which events were knowable at each origin, for reporting only. The model does
not claim effects from policy timing, so the December 2022 and December 2025
pull-forward spikes are expected to show up as forecast errors.

## 4. Information-availability controls

1. **Each origin `d` uses `store.as_of(d)` only.** That view holds values and
   revisions published on or before `d`. For history found in the first
   retrieval, "published" means `period end + 10 days`, conservative against
   the observed 3-day lag.
2. **An audit check fails the origin** if any observed month is at or after
   `d`'s month, or if anything read was published after `d`. The audit is
   stored with the result, including the events known at `d`.
3. **Covariates (petrol price) use only available months.** Later ticks hold
   the last available transformed value.
4. **Calibration moments, baselines and intervals** are all computed from the
   origin's data.
5. **Scoring happens afterwards**, against `store.latest()`. Values are
   today's, and SSB keeps no historical vintages; see M18.
6. **Tests confirm the controls:**
   - planted future revisions do not change origin inputs or baseline
     forecasts;
   - events announced after an origin are not visible from it.

Limitations: the pilot design itself (products, functional forms, ranges,
origins) was chosen by someone who knows how the history turned out. That
hindsight can't be removed by code. The spec is committed before results are
generated, so the choices are at least auditable.

## 5. Baselines and metrics

Every model sees the same observed monthly BEV share series at each origin
and is scored on the same target months:

| Baseline | Definition |
|---|---|
| naive | last observed value |
| drift | logit share, mean monthly change over the last 24 months |
| linear trend | OLS on the logit share over the last 36 months |
| Holt | Holt linear exponential smoothing on the logit share; α and β chosen by one-step SSE from 2005 |
| logistic | 3-parameter logistic `K/(1+e^{−r(t−t0)})` (Fisher–Pry substitution curve), least squares from 2005 |
| Bass | `m·F(t)`, with `F` the Bass diffusion CDF `(p, q)`, applied to the share series from 2005 |

**Intervals** follow one rule per model class:
- random-walk class (naive, drift, Holt): the one-step residual SD × √h, on
  the logit scale, mapped back by the delta method;
- curve class (linear, logistic, Bass, **Halibut**): the in-sample residual SD
  over the last 36 months, constant across horizons. Halibut adds its seed
  spread.

None of the intervals include parameter uncertainty, so they are expected to
be too narrow. Coverage is reported as measured.

**Metrics:**
- MAE, RMSE and bias in percentage points;
- MASE, scaled by the in-sample one-step naive MAE over the last 36 months;
- 80% and 95% interval coverage;
- results by horizon bucket (1–6, 7–12, 13–24 months), by origin, and for
  change windows around the policy-dated anomalies (2022-11..2023-03,
  2025-11..2026-03).

Every report lists explicitly the baselines that beat Halibut.

**Historical fit vs forecasting.** Each origin reports Halibut's in-sample
MAE (the fit window, and the last 36 months) separately from its
out-of-sample forecast errors.
