# Phase 3 data system and M18 data-quality checkpoint

> Data: Statistics Norway (SSB), <https://www.ssb.no>, licensed CC BY 4.0.
> Halibut aggregates and transforms it. No model results appear in this
> document: everything below is observed data or a property of the data.

## 1. Components (M16–M17)

| Component | Role |
|---|---|
| `configs/data/catalogue.toml` | Sources (licence, attribution, access) and datasets. Each dataset records the publisher's definition, `evidence_class`, `universe_dims`, a conservative publication lag, and caveats. |
| `configs/data/events_norway_ev.toml` | Sourced policy and context events. Each records what is *observed* separately from an *assumed* effect, with a verification grade (`primary`, `secondary` or `conflicting`). |
| `configs/data/checks_norway_ev.toml` | Pilot-specific cross-table consistency checks, kept as data rather than code. |
| `halibut.data.store` | SQLite store with schema migrations. Bitemporal observations; values are never updated in place (each change is a new revision). Events. Ingest audit log. |
| `halibut.data.ssb` | PxWeb adapter. Requests are spaced at least 2.5 s apart, with bounded retries and no retry on 4xx. It parses json-stat2 and saves content-addressed raw snapshots before parsing. |
| `halibut data …` | `init`, `ingest`, `events`, `status`, `quality`, and `rebuild`, which recreates a store from the raw snapshots alone with no network access. |

Raw snapshots (`data/raw/`) and the store (`data/*.sqlite`) are git-ignored.
Running `halibut data rebuild` reproduces the store exactly from the
snapshots (tested). The schema is plain SQL and Postgres-compatible, so a
Supabase backend would implement the same `Store` and `AsOfView` interface.

### Leakage controls

- **`AsOfView(t)`** returns only the latest revision with `available_at ≤ t`
  and only events with `available_at ≤ t`. It records the newest
  availability actually read, for audit. Tests plant future values and
  revisions and check they are invisible.
- **Availability of history found in the first retrieval** is estimated as
  `period_end + publication_lag_days`, never later than the release we saw.
  The catalogue lags are conservative compared with the lags actually
  observed (table below), and `halibut data quality` fails if an observed
  lag exceeds the catalogue value.
- **Revisions** found by later retrievals become new revisions, available
  from the release that revealed them. **Revisions made before our first
  retrieval are invisible.** SSB publishes no vintages, so a backtest sees
  today's values for history. §2.4 bounds how much this matters for shares.
- **Events** are known from their verified announcement date, or else from
  an explicit conservative date, or else from the effective date.
- **Shares** are computed only within one universe (for example new cars
  *or* second-hand imports, never both), and periods with missing values
  are omitted rather than treated as zero.

## 2. Data-quality findings (M18)

Store contents: 264,450 observations across 4 datasets and 994 geographies,
plus 14 events. Every check passed except the warnings listed below.

### 2.1 Coverage and completeness (national)

| Dataset | Frequency | Coverage | Missing | Gaps | Observed lag / assumed |
|---|---|---|---|---|---|
| 14020 first registrations by fuel group (new, used import) | M | 1995-01 – 2026-10 (382 months) | 0 | 0 | 3 / 10 days |
| 12906 first registrations, detailed fuel, by region | A | 2019 – 2025 | 0 | 0 | 65 / 75 days |
| 07849 fleet stock by fuel, by region | A | 2008 – 2025 | 0 | 0 | 65 / 120 days |
| 09654 petrol / diesel retail prices | M | 1986-08 – 2026-10 | 0 | 0 | 19 / 35 days |

### 2.2 Definitions and universes

- **Type of registration** (SSB labels): `N` "New vehicles", `B`
  "Second-hand imported vehicles". SSB publishes no fuller definition on the
  pages located. The data clearly treats them as different markets.
- **The two universes differ sharply.** In June 2025 the battery-electric
  share was 96.8% of new private cars against 59.1% of second-hand imports.
  Second-hand imports were 12.6% of all first registrations in 2015 and 3.1–4.3%
  in 2023–2025. All share targets use `N` only.
- **Fuel groups in 14020:** 19 Electric/zero-emission, 20 Fossil, 21 Hybrid,
  6 Other.
  - Plug-in and conventional hybrids are **not** separated monthly. The split
    exists only annually, from 2019, in 12906.
  - The approximate mapping to 12906 is 19 ≈ Electricity, 21 ≈ the four
    hybrid codes and 6 = Other. Where hydrogen belongs is ambiguous, because
    year-by-year differences don't identify it.
- **07849 has no hybrid category**: hybrids count under their combustion
  fuel. Its fuel classes must never be combined with 14020's groups.

### 2.3 Cross-table consistency

Table 14020 summed by year against 12906 national totals, new private cars:

| | 2019 | 2020–2024 | 2025 |
|---|---|---|---|
| Total difference | −1.3% | < 0.1% | −1.4% (176,847 vs 179,397) |
| BEV share difference (percentage points) | −0.04 | −0.02 to −0.06 | −0.11 |

The tables are compiled at different times, so recent monthly values can be
superseded without notice. **Shares are robust to this (≤ 0.11 pp); totals
are not (up to 1.4%).**

### 2.4 Diffusion milestones (observed, new private cars)

Battery-electric share of monthly registrations:

| Milestone | First month reached |
|---|---|
| > 1% | 2011-01 |
| > 50% | 2019-03 |
| > 90% | 2024-01 |

The 2025 annual share was 95.9%. That gives 382 monthly observations
covering the entire S-curve, from near zero to near saturation.

### 2.5 Anomalies

- **Pull-forward before tax changes.** Robust outliers in total new
  registrations appear in **2022-12** (39,499, z = 5.6) and **2025-12**
  (35,156, z = 3.8). These are the months before VAT changes for EVs on
  2023-01-01 and 2026-01-01 (both `primary` events). The timing is an
  *observed association* consistent with pull-forward, not an estimated
  causal effect. Used imports are also flagged in 2025-12 and 2026-02.
- **Structural zeros in regional tables.** Codes not valid in a given year
  are reported as 0:
  - 2019 populates 19 counties and 423 municipalities;
  - 2022 populates 12 counties and 359 municipalities;
  - 2025 populates 17 counties and 358 municipalities.

  This follows Norway's 2020 and 2024 territorial reforms. Regional analysis
  needs a code-validity and harmonisation map first. National series are
  unaffected.

### 2.6 Policy and context evidence

There are 14 events. 4 are `primary`, 9 are `secondary` and 1 is
`conflicting` (the annual-tax start date). The early incentives (1990–2015)
rest mostly on secondary timelines, and their dates must be confirmed in
primary documents before they become model inputs.

**Not ingested yet**, each still to be measured rather than narrated:
- charging infrastructure (SSB 14725 covers only 2024–2025; NOBIL needs a
  key);
- car prices and the BEV–ICE price gap, which no public source here covers;
- interest rates (Norges Bank, open API, verified reachable);
- electricity prices (SSB 14491 / 14493).

## 3. Feasibility checkpoint for M19–M21

**Viable calibration target: yes.** The monthly battery-electric share of
*new* private cars, national, 1995–2026:
- it measures actual purchases (registrations);
- it has no gaps;
- its share is insensitive to known revisions;
- it spans the full displacement curve.

Fossil and hybrid shares give two more moments. Total registrations should
be treated as exogenous at first, because they depend on macro conditions
not modelled here.

What the data can and cannot identify:

| Identifiable from shares | Not identifiable without more data |
|---|---|
| timing, steepness and saturation of displacement | individual switching, loyalty, social influence (only their aggregate consequences) |
| size of pull-forward around dated policy changes | car prices and their gap; any "price sensitivity" is confounded with policy and model availability |
| response to fuel-price movements (weak, confounded) | charging availability; regional heterogeneity, until codes are harmonised |

**Model changes required in M19** (all optional and off by default, so
existing configs and golden results are unchanged):
1. **Purchase-opportunity model.** A per-tick probability that a consumer is
   in the market (cars are bought every few years).
2. **Time-varying product attributes** driven by observed covariates (fuel
   prices) and dated events through an explicit, versioned
   **measurement/policy mapping**. Every mapping coefficient is either
   calibrated or declared as an assumption.
3. **Monthly ticks.** The engine is unit-agnostic, and `weeks` is just the
   number of ticks. 382 monthly ticks cost about 7× a 52-tick run.

**Honest expectations.** A three-parameter logistic or Bass curve is a
strong competitor on an S-curve this clean. The backtest will report
Halibut's error against these baselines at each forecast origin. If Halibut
doesn't beat them, that will be the reported result.

**Estimated compute for M19–M21** (2,000 agents, 382 monthly ticks ≈ 0.5 s
per run):

| Stage | Estimate |
|---|---|
| Calibration, per forecast origin (~300 evaluations × 3 seeds) | ~5–10 min |
| Five rolling origins | ~0.5–1 h |
| Baselines | seconds |

Every run prints an estimate first, needs `--yes` above the time limit, and
resumes from the cache.
