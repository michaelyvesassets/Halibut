# Architecture V2, milestone 1: unified prediction ledger and automatic outcome resolution

Status: **implemented and tested offline (2026-11-27); awaiting founder review.** No paid calls, no promotion, no
real-world action. Roadmap item 1 of [MASTER_INTELLIGENCE_ARCHITECTURE_V2.md](MASTER_INTELLIGENCE_ARCHITECTURE_V2.md).
§4 and §5 are designs only.

## 1. What was built, and what was reused

| Requirement | Implementation | Reused |
|---|---|---|
| Stable claim and prediction identities | `prediction_id` = hash of kind, source, source id and question sha256; one row per (source, source id) | test ids, claim ids |
| Resolution criteria and horizons | `question_json` (quantity, target periods, comparator and reference, or point, interval and SD); `resolution_rule` = `first_publication.v1`; `horizon` = nominal, future_window or standard | compiler specs |
| Registration and evidence timestamps | `registered_at` (the real time; never backdated); `evidence_as_of`; `evidence_available_at` | hypothesis_tests, step2 prospective |
| Provenance | investigation, input id, prompt and contract version, schemas, agent kind, model per role, panel fingerprint, market-spec sha256 | investigation documents |
| Probabilistic and numerical predictions | binary (categorical, or with a `probability`) and numeric (point, 80% interval, SD) | forecasts and estimates |
| Immutable originals | SQL triggers: `predictions` cannot be updated or deleted; `prediction_resolutions` is append-only | the store's append-only pattern |
| Typed outcomes | pending, resolved, invalid (market definition changed; prospective evidence predating registration), ambiguous (exact tie), unavailable (published but not computable) | typed failures (ADR-021) |
| Idempotent resolution against independently acquired evidence | evidence comes only from the data layer (provider refreshes), never from agents; resolution ids hash the outcome, so reruns add nothing | bitemporal store, as-of retriever |
| First publication, with revisions separate | resolves on the vintage at the moment the LAST target period was first published; later revisions get their own `revision` rows; the first stands | observation revisions and `available_at` |
| No unpublished evidence | only observations with `available_at <= now`; the CLI refuses a future `--now` | as-of retriever |
| Proper scoring | numeric: absolute and squared error, 80% interval hit, Gaussian CRPS and log score; binary: hit, Brier at p = 1 for categorical claims, Brier and log score with a probability | – |
| Baselines | persistence and momentum, computed from the ORIGIN evidence only and stored at registration; scored next to every resolution | registry baselines |
| Auditable history; cost and execution tracking | every attempt is a row (pending attempts deduplicated); every run is a hash-chained event (counts, seconds, API cost $0); a cost share per prediction from its investigation | event log, spend ledger |
| Correlated questions | `correlation_group` (a claim's quantity across windows); the summary reports effective resolved groups | – |
| Durability | `predictions export` writes `registry/predictions/ledger.json` (deterministic, with a sha256) for git | registry/ (R2 closure) |

**Not promoted.** Resolutions never create lessons, validated mechanisms or shared knowledge.

## 2. Usage (offline; no API calls)

```bash
halibut predictions sync                  # register predictions of stored investigations (idempotent; skips mock/reference)
halibut predictions resolve [--dry-run]   # resolve what first publication allows now; repeatable
halibut predictions list [--temporal prospective] [--status pending] [--json]
halibut predictions show <prediction_id>  # the question plus its full resolution history
halibut predictions summary               # by contract, temporal class and kind: status, effective groups, hits vs baselines
halibut predictions export                # registry/predictions/ledger.json, for git
```

**Typical cycle** (manual now; a scheduler can call exactly the same commands later):
1. refresh the DfT data (`halibut intel investigate …`, the normal provider path);
2. run `predictions resolve`;
3. run `predictions export`;
4. commit the registry.

## 3. Results

**Offline demonstration (tests, fixture data, simulated later releases):**
- **Before publication:** a prospective v2 investigation registered on data through 2026-Q2. Every question is
  pending, and still pending at a `now` before release 2 even though the data are in the store.
- **Release 2** (2027-02-20) adds 2026-Q3/Q4:
  - the nominal questions resolve on the `published` vintage, with baseline scores;
  - the future-window questions (2027-Q1/Q2) stay pending;
  - a rerun adds nothing.
- **Release 3** (2027-05-20) revises 2026-Q3: `revision` rows are added, and the first-publication outcomes are
  unchanged.
- **Typed cases:**
  - a question "registered" after its outcome was published is `invalid`;
  - an exact tie is `ambiguous`;
  - an unknown entity is `unavailable`;
  - an edited market definition is never used (`invalid`).
- **Immutability:** UPDATE and DELETE are rejected on both tables.

**Real store (2026-11-27):**

| Item | Result |
|---|---|
| Predictions registered | 133, from the stored Claude investigations (Step 1, 2a, 2b) |
| Resolved (all retrospective) | 114 |
| Pending, prospective hypothesis tests | 12: 6 future window (2027-Q1/Q2) and 6 nominal (2026-Q3 unpublished history, 2026-Q4 nowcast) |
| Pending, prospective model forecasts and estimates | 7 |
| Agreement with the loop's original outcomes | 45 of 45 overlapping hypothesis tests |
| A second resolve | 0 new records |
| Resolution time | about 10 s per run; $0 API |

All resolved records are flagged `estimated_lag`: DfT history backfilled at our first retrieval has no observed
first-publication date. The first genuinely `published` resolutions will be the prospective ones.

**Backward compatibility:**
- 396 tests pass, and migration 9 is additive;
- frozen Step 2 criteria hash `223362694f7d254f…`, the R2 contracts, the original scores and the documents are
  unchanged;
- the 2b prospective registry verifies, and the evidence archive reproduces the investigations' metrics.

## 4. Acceptance criteria for this milestone

| # | Criterion | Status |
|---|---|---|
| M1.1 | every registered R2 prediction is in the ledger, with its real registration time, horizon, temporal class and provenance | met: 133 |
| M1.2 | no prediction resolves before all its target periods are published at `now` | met (tested) |
| M1.3 | first-publication resolution is unique and permanent; revisions are separate | met (tested) |
| M1.4 | resolution is idempotent | met: real store and tests |
| M1.5 | every outcome is typed, with no invented values | met (tested) |
| M1.6 | baselines exist for every resolvable question, from origin evidence only | met |
| M1.7 | the ledger survives store loss | met (`registry/predictions/ledger.json`) |
| M1.8 | the ledger agrees with the original R2 outcomes | met (45/45) |
| M1.9 | the first real prospective resolutions happen when DfT publishes 2026-Q3/Q4 | **pending** (external) |

## 5. Remaining limitations and dependencies

- **No scheduler.** Resolution is a CLI command; a cron or CI job can call it later, which needs no new
  infrastructure.
- **Data refresh is manual.** It runs through the existing intel workflow; there is no DfT release calendar yet.
- **Only DfT panels have a revisioned first-publication record.** `csv_panel` uses row availability.
- **R2 claims carry no probabilities.** Binary scores are categorical (hit, Brier at p = 1). Calibration needs
  probabilistic contracts, a later contract version.
- **Retrospective resolutions are leakage-prone** and are labelled as such; their first-publication values are
  estimated (backfill).
- **Mechanism or causal validation is out of scope.** Predictive support never promotes anything.

## 6. Design: secondary evaluation readout v2.1 (not implemented; the R2 scores are preserved)

A **separately versioned** readout (`r2_eval.v2.1`), computed offline from the stored documents and the ledger and
published next to the frozen scores, never replacing them.

| Topic | Rule |
|---|---|
| Baselines | A test is informative only if NEITHER persistence (`baseline.level`) NOR momentum predicts its outcome. For zero comparators, persistence is the sign of the latest gap (this removes the R2 H3 artefact). Statistical baselines (Holt, logistic, Bass) are reported where validated |
| Contradicted rivals | No dispute credit when the losing rival's account of its own explained movement contradicts the origin evidence: the claimed direction is opposite to the observed L4−P4 change, or a deterministic check shows that a cited number is wrong. The check is engine-side (claim → metric → direction), never the critic's word alone |
| Correlated windows | Count per (claim, quantity) correlation group, not per window. Effective sample size = distinct groups; prospective windows are reported separately |
| Separate quantities | (a) originality (library-based); (b) informativeness at registration (against both baselines); (c) predictive accuracy on RESOLVED questions (hit rate and proper scores against baselines); (d) causal validity: **none is claimed** from these designs. Each is reported on its own; no composite score |
| Critic | Recall and precision by defect type against an **independently audited** control set: each item reviewed by a person blind to the critic outputs, confirming exactly one defect (or none for controls) before use. Severity calibration (detections at low severity reported separately); deterministic verification of the critic's numerical claims |
| Pending | Excluded from accuracy, never imputed; reported as counts by window class and horizon until resolved |

## 7. Design: sealed holdout protocol (not to be used until the strategist v3 and critic improvements are frozen)

1. **Construction.** Questions are generated deterministically from markets and origins **never sent to any agent**:
   held-out origins, held-out market definitions, and perturbed variants of historical slices (renamed brands,
   shifted dates, rescaled volumes) to separate reasoning from recall.
2. **Sealing.** The question list and generator seed are hashed and the hash is committed. The question content is
   kept outside the development repository and loop, so no development run can see it.
3. **Use.**
   - Evaluation runs only after a contract freeze (prompt, schema and criteria hashes recorded).
   - Each holdout question is used once per frozen version.
   - Comparisons are equal-budget, with the standalone model (no Halibut scaffolding) and deterministic baselines
     on the same questions.
4. **Unsealing rules.** Results are published with the seal hash. A used holdout becomes development data and is
   replaced. Any leak (a question seen in development) invalidates it.
5. **Leakage controls.** Every outcome window is labelled against each model's training cutoff. Prospective
   questions (future windows) stay the primary evidence; perturbed historical variants are secondary.
