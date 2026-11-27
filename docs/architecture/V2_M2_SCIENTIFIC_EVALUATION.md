# Architecture V2, milestone 2: scientific evaluation (secondary readout `r2_eval.v2.1`)

Status: **implemented and tested offline (2026-11-27); awaiting founder review.** No paid calls; the frozen R2 scores,
criteria (hash `223362694f7d254f…`) and contracts are unchanged. Readout file:
`registry/evaluations/r2_eval_v2.1.json`.

## 1. Implementation summary

| Requirement | Implementation (`src/halibut/investigation/evaluation_v21.py` unless noted) |
|---|---|
| A. Baselines | **persistence** and **momentum**, stored in the prediction ledger at registration; **Holt** (share-like quantities); a **reference-class base rate**: across all entities and all past 2-quarter windows inside the origin panel, how often the quantity rose against its own L4 (n ≥ 20 windows). **All baselines use origin evidence only.** Skill tiers for correct resolved predictions: `consensus` (every committed baseline also right), `beats_some`, `beats_all` (at least 2 committed baselines, all wrong). A test committed against by fewer than 2 baselines is "weakly assessed" |
| B. Dimensions | reported separately with no composite score: originality; informativeness (no committed baseline predicts it); hypothesis quality (an engine check of the mechanism's claimed direction against the observed change; critic flags reported as unverified); predictive accuracy; calibration ("not assessable": categorical claims); mechanism discrimination; causal validity ("not assessed: no identification design"); decision relevance; computational efficiency |
| C. Competing mechanisms | each candidate pair is classified as `genuine`, `contradicted_at_registration`, `pure_negation`, `near_duplicate`, `not_same_observation`, `not_interpretable` or `no_opposition`. **Only genuine pairs** whose disputed tests resolved one way earn credit. Plausibility is flagged "not machine-assessable" (uncertainty preserved) |
| D. Integrity | outcome groups (the same realised value counts once) give unique realised outcomes, which are not independent observations; nominal Wilson 95% intervals; separate cells for retrospective, unpublished-historical, nowcast and future, and for each provenance basis: `estimated_lag`; `first_capture_inferred` (a heuristic, not verified publication); `first_capture_unverified`; per-test leakage risk against the model's training cutoff; pending tests are never scored |
| E. Critic foundations | `critic_benchmark.build_v2` (purified: one defect per item, clean controls grounded only in evidence, audit checklist, status `proposed`); `score_v2` separates detection, classification, severity accuracy, evidence-reference accuracy, false positives on clean items, misdirected high-severity claims, generic caveats (neither credited nor penalised) and schema failures. The best critique per item counts, so **extra critiques never add credit**; results are labelled UNAUDITED until a person audits the items |
| Sealed holdout | `investigation/holdout.py` with `registry/holdout/seal.json`: 16 unused (market, origin) entries (UK BEV and UK hybrid, 2023-08-23 to 2025-05-23), salted hashes only. `run_investigation` refuses a sealed entry unless the freeze-time evaluation token matches; none exists yet |

**CLI** (offline):
```bash
halibut investigation secondary-eval results/r2_step2/run2a-*/r2s2a-* results/r2_step2/run2b-*/r2s2b-* \
    [--out registry/evaluations/r2_eval_v2.1.json]
halibut investigation critic-audit-build configs/intel/markets/uk_cars_bev_v1.toml --as-of 2026-02-23 --out <candidate.json>
halibut predictions sync && halibut predictions resolve   # the ledger the readout uses (milestone 1)
```

**Tests:**
- **415 pass:** 15 new for milestone 2 and 9 targeted milestone 1 checks.
- **Covered:** pair classes; evidence consistency; origin-only baselines; Wilson intervals; frozen readout unchanged
  after the secondary evaluation; pending never scored; purified and blinded benchmark; no volume reward; holdout
  refusal in the loop.
- **Backward compatibility:** frozen hash pinned; the 2b registry verifies; the v1 reconstruction and Step 1 tests
  pass; the holdout guard does not affect any development origin.

## 2. R2 reassessment: frozen against secondary (thresholds set before looking)

### 2.1 The previously useful findings

| Finding | Frozen | Secondary v2.1 | Reason |
|---|---|---|---|
| 2a H1 (BYD dilution, 2025-08-23) | useful | **no** | rival H2 contradicted at registration (claims BYD BEV decline; BYD BEV share rose from 0.75% to 2.89%), so no discrimination credit; its own tests are trend-consistent (`consensus` or `beats_some`) |
| 2a H4 (VW denominator, 2026-02-23) | useful | **no** | its mechanism is contradicted by the evidence (the engine check; the critic also flagged it). Its against-trend tests were right (`beats_all` on non-segment share), but a correct prediction from a contradicted mechanism is not a useful mechanism result |
| 2a H6 (Ford expansion, 2026-02-23) | useful | **yes, as a predictive result only** | **reconciled 2026-11-27.** Engine-only: a genuine pair with H5 plus a discrimination win. With the accepted manual review (`registry/evaluations/manual_reviews.json`): H5 is contradicted (it says "not BEV expansion" while Ford's BEV share tripled), so the pair is `contradicted_at_registration` and **no discrimination credit** is given. H6 still qualifies because its BYD-overtakes-Ford gap call beat both committed baselines (`beats_all`). That is a correct prediction, **not a validated mechanism** |
| 2b H3 (BMW hybrid substitution) | useful | **no** | persistence and Holt already predicted the "BMW stays below Toyota" outcome (the H3 artefact) |

**1 of 4 survives (H6), as a correct predictive result, not as mechanism or discrimination evidence.** Caveats:
- The engine check missed H5's contradiction, because it compares only the mechanism's direction on the explained
  metric. The human overlay is reported separately (`useful_v21_engine_only` against `useful_v21`).
- Leakage risk is high (the window predates the model's cutoff).
- The resolution basis is `estimated_lag` (backfilled history).
- It is predictive support only, not causal evidence.

### 2.2 Resolved retrospective predictions against baselines

All bases are `estimated_lag`. Hit rates are over **unique realised outcomes**. These are NOT statistically independent: quantities in the same market and quarters share shocks. The Wilson 95% intervals are therefore **nominal and understate the uncertainty**.

| Experiment, contract | Resolved (independent) | Hit rate (95% CI) | Holt | Momentum | Reference class | Skill tiers |
|---|---|---|---|---|---|---|
| Step 1 r2r-878 (v1) | 6 (6) | 0.83 (0.44–0.97) | 0.83 | 0.83 | 0.50 | consensus 3, some 2 |
| 2a A (v1) | 6 (6) | 0.83 (0.44–0.97) | 0.83 | 0.83 | 0.17 | consensus 1, some 4 |
| 2a B (v2 base) | 8 (7) | 0.64 (0.30–0.88) | 1.00 | 1.00 | 0.67 | consensus 3, some 2 |
| 2a C (v2 extended) | 15 (12) | 0.79 (0.51–0.93) | 0.92 | 0.60 | 0.58 | consensus 4, some 4, **all 3** |
| 2b A (v1) | 4 (4) | 0.75 (0.30–0.95) | 1.00 | 0.75 | 0.75 | consensus 2, some 1 |
| 2b C (v2 extended) | 6 (5) | 0.90 (0.46–0.99) | 1.00 | 0.83 | 0.40 | consensus 1, some 4 |

**Pending, never scored:** 2b, 6 future and 6 nowcast-window binary tests (3 and 3 per arm).

**Main findings:**
1. **The Holt baseline is as good as, or better than, the models' directional claims in every cell.** On retrospective
   data, Claude's hit rates do not exceed a simple statistical extrapolation.
2. **Only arm C produced `beats_all` results (3):** BYD overtakes Volkswagen, BYD overtakes Ford, and Volkswagen's
   non-segment share falls. All three are relative-growth "overtake" calls that the naive baselines missed. Two come
   from hypotheses excluded as useful (refuted or contradicted), so they are **predictive signals, not mechanism
   evidence**.
3. Arm B's informative tests were all refutations (as in the frozen readout).
4. Every interval is wide: 4 to 12 unique, correlated outcomes. **No statistical superiority can be claimed.**

### 2.3 Competing mechanisms

| Contract | Genuine | Contradicted at registration | Pure negation | No opposition |
|---|---|---|---|---|
| v2 base (2a) | 0 | – | 1 | 2 |
| v2 extended (2a, 2b) | 2 (1 resolved, for H6; 1 prospective and pending) | 2 | 1 | 3 |

Evidence-contradicted hypotheses: v2 base 3, v2 extended 5, v1 0. The v1 mechanisms are vaguer, so the engine check
can rarely contradict them; that is not a sign of better quality.

### 2.4 The 2b critic, rescored

The 2b items are the v1 benchmark items, with known impurities. The score is UNAUDITED and diagnostic only, and the
frozen C1–C4 are unchanged.

| Measure | Result |
|---|---|
| Detection | 1.00 (5/5) |
| Classification | 0.80 (the mis-encoded test was filed under the wrong dimension) |
| Severity accuracy | 0.75 (the wrong number was flagged only at low severity) |
| Evidence-reference accuracy | 1.00 |
| False positives on clean controls | 1 |
| Misdirected high-severity claims | 1 |
| Schema failures | 4 |

The frozen precision of 0.33 mostly reflected benchmark impurity and generic caveats. The structured scorer separates
those from real errors.

### 2.5 Remaining limitations

- **Samples are tiny**, and every resolved outcome is retrospective with backfilled first-publication dates.
- **Evidence consistency checks only the mechanism's direction.** Plausibility, magnitude and the quality of a
  rival's reasoning need audited judgement.
- **The reference-class baseline prices only "against own L4" comparisons.** Gaps rely on persistence and momentum
  (2 baselines).
- **Calibration cannot be assessed** until contracts state probabilities.
- **The critic benchmark v2 is unaudited.** A person must audit its items (a candidate can be built with
  `critic-audit-build`).
- **The holdout seal is a procedural and code barrier, not a secret or leakage-resistant dataset.** The candidate
  rule is public, the 16 entries can be enumerated, and the outcome windows predate the model's training cutoff.
  Prospective questions registered after the freeze are the leakage-resistant holdout.

## 3. Sealed holdout protocol (final; not to be executed until strategist v3 and the critic improvements are frozen)

1. **Selection** (deterministic, `holdout.candidates`): all unused quarterly origins from 2023-08-23 to 2025-05-23 for
   UK BEV and UK hybrid: **16 entries**. Prospective questions registered after the freeze are the primary holdout,
   because they are free of leakage.
2. **Separation:**
   - only salted hashes are in git (`registry/holdout/seal.json`, seal sha256 recorded);
   - outcomes are never computed into development artefacts;
   - perturbed variants (renamed brands, shifted dates) are generated only during the evaluation run.
3. **Access control:**
   - `run_investigation` calls `holdout.guard` first and refuses a sealed entry;
   - it opens only when `HALIBUT_HOLDOUT_EVAL_TOKEN` matches the sha256 the founder records at freeze time
     (`eval_token_sha256` is null now);
   - any development use of an entry is a leak and retires that entry.
4. **Versioning:**
   - an evaluation records the frozen prompt, schema and criteria hashes and the seal version;
   - each entry is used once per frozen version, then retired in the next seal version (`holdout.v2`);
   - comparisons are equal-budget, against the standalone model and the deterministic baselines.

## 4. Architecture V2 progress

| Roadmap item | Status |
|---|---|
| 1. Prediction ledger and automatic resolution | **done** (milestone 1, pushed); awaiting DfT's 2026-Q3/Q4 release for the first prospective resolutions |
| 2. Evaluation integrity v2.1 | **done** (this milestone; awaiting review) |
| 3. Strategist contract v3 | next (recommended) |
| 4. Critic validation | foundations done (benchmark v2, scorer); needs a human audit of the items, then an active-critic change |
| 5. Sealed holdouts | protocol final, seal committed; evaluation waits for the freezes of 3 and 4 |
| 6–8 | not started |

## 5. Recommendation for milestone 3

**Strategist contract v3** (engine-calculated quantity catalogue, numeric references, visible schema limits),
developed on the development dates only. In parallel, **the founder audits the purified critic benchmark** (a
person-only task; no code).

Why this order:
- **K2 failed twice for contract reasons**, and the strategist is the decision layer, which is Halibut's purpose
  beyond forecasting.
- **v3 is cheap:** offline, then about $0.01–0.02 for a development run.
- **It is a prerequisite for freezing** before the holdout.

**A finding to carry into milestone 3 and the generator work:** Holt matches or beats the models' directional
claims. A future generator contract should see the baseline forecasts and be asked for implications that **disagree
with them for a stated reason**. That is a measurable way to aim at the "overtake"-type signals that beat every
baseline here.
