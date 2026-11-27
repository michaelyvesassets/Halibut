# R2 Step 2b assessment and closure of the frozen R2 phase (`r2s2b-33f66362ef91`, 2026-11-27)

Status: **offline assessment (2026-11-27); no paid calls; frozen criteria (hash `223362694f7d254f…`), active
contracts and original results unchanged** (`results/r2_step2/run2b-20261127T121630Z/`).

**Frozen result:**

| Outcome | Criteria |
|---|---|
| Passed | A1–A6, B1, B4, B5, C1, C3, C4, D1, K1, P1, P2 |
| Failed | B2, B3, C2, K2 (expected) |
| Not applicable (`null`) | B6, E1 (no arm B in 2b) |

## 1. H3, the one retrospective useful result: an evaluation artefact

**H3** (UK hybrid-electric (petrol), origin 2026-02-23, arm C): *"BMW is gaining hybrid petrol share while losing
total share: the hybrid push is substituting for other BMW powertrains."*

- **Tests:**
  - I2, BMW–Toyota gap below zero: passed, −4.94 pp, flagged against-trend;
  - I3, BMW non-segment share below 5.92%: passed, 4.83%, trend-consistent.
- **Why it counted:** I2 was "informative" only because the momentum baseline extrapolated the narrowing gap past
  zero.
- **Why that is an artefact:** at the origin BMW trailed Toyota by 3.88 pp, so **level persistence predicts I2**.
  "The leader stays ahead" is a trivial comparison. The frozen definition tests informativeness against momentum
  only.
- **Verdict:** **an evaluation artefact, not a meaningful predictive finding.**
  - The substitution mechanism is plausible and consistent with I3, but I3 is trend continuation.
  - The rival (H4) made no opposite prediction on a shared quantity.
  - Leakage risk is high: the outcome window predates the model's training cutoff.
- **Check on Step 2a:** under the persistence check, 2a's H6 gap test still stands: Ford led BYD at the origin, so
  persistence predicted the opposite outcome. H4's against-trend tests also still stand.

## 2. The critic: reasoning failures versus schema failures

**Benchmark:** 2 blinded sets, 5 planted defects and 4 clean controls; 2 calls, $0.0027586.

| Category | Items |
|---|---|
| Correct detections (4 of 5) | contradicted assumption (Tesla's 100% segment mix, cited correctly); unsupported detail; trend-only test; wrong number (found, but at **low** severity, so it adds nothing to precision) |
| Missed (misclassified) | the mis-encoded gap test. The critic saw that "the implication is a comparison of levels" but filed it under *causal_identification* (not *test_validity*) and misread the comparator as "above zero": partial reasoning, wrong classification |
| Valid critiques of **unplanted** weaknesses (benchmark impurity) | "no dealer-network data" on the wrong-number hypothesis (true: the planted hypothesis also carries an unevidenced mechanism); a segment-wide-growth alternative on a clean control whose "range fit" mechanism is unevidenced; "the tests cannot isolate" on the unsupported-detail hypothesis |
| Genuine reasoning false positives | numerical-accuracy critiques of correct numbers on a clean control (wrong dimension); a duplicate low-value critique; two **self-contradictory** critiques ("BYD's segment_share fell from 1.71% to 4.75%? No: it rose"; "Tesla rose 13.32% to 10.66%, falling") |
| Schema failures | 4 critiques rejected only because they cited 4 evidence ids against an invisible limit of 3 |

**Separately labelled offline diagnostic** (limit-only rejections restored; **never replaces the frozen C1–C4**):

| Metric | Frozen | Diagnostic |
|---|---|---|
| Recall | 0.80 | 0.80 |
| Precision | 0.33 | 0.23 |
| Clean false-alarm rate | 0.00 | 0.25 |
| Evidence-reference accuracy | 1.00 | 1.00 |

The hidden critiques were mostly low quality, including both self-contradictory ones, so **the invisible limit did not
depress the frozen critic score**. The real problems are:
1. **benchmark impurity:** planted and clean items carry unevidenced mechanisms by construction, which understates
   precision;
2. **genuine critic errors:** severity calibration, dimension choice and occasional numerical confusion.

Requirement F5 should purify the benchmark (one defect per item; clean controls grounded only in evidence) and verify
the critic's numerical claims deterministically.

## 3. Why Step 2b did not replicate Step 2a

1. **By design, half the investigations cannot resolve yet.** The two prospective UK BEV investigations have 12
   pending tests and 0 outcomes, so no useful result was possible.
2. **The mechanism gates did their job.** In the hybrid market, arm C declared 3 rival pairs:
   - one was a pure negation (MG expansion against a fleet one-off, opposite directions on the only shared quantity);
   - two made no opposite prediction on a shared quantity.

   No pair counted, so B3 failed. In 2a, 3 of 3 C pairs passed. This is the gates rejecting weak competition, not a
   pipeline failure.
3. **Accumulated institutional knowledge lowered novelty.** By the prospective date, BYD dilution and market-wide BEV
   growth were in the reference library (Step 1 and 2a claims), so the prospective H2 was a restatement. This is
   correct behaviour: repeating a known insight is not original.
4. **A different and calmer market.** The hybrid segment had no BYD-like structural shock. Arm A produced 0 original
   hypotheses there.
5. **Generator variance and contract misuse.** The prospective C investigation accepted only 2 hypotheses. Two
   implications used `above_zero` with `units_growth` and were not testable (the schema allows that comparator for
   every template, which is a contract-clarity issue).
6. **The sample is tiny** (2 investigations per arm). Replication cannot be judged from it.

**Informativeness (C = 0.4167, frozen):**
- 6 of the 12 registered tests are informative by item: 4 pending prospective tests on one disputed quantity (BYD
  segment share, nominal and future windows), the MG refutation and the H3 artefact;
- the frozen evaluator counted 5, because a future-window test shares its implication id with its nominal test and
  one overwrote the other;
- counting nominal and future windows separately would **double-count highly correlated predictions**. Per
  implication, the figure is 4/9 = 0.44;
- B4 passes under every reading. Correlated windows must be deduplicated in the next evaluation version.

## 4. Resolved retrospective results and pending prospective predictions

| Set | Registered tests | Resolved | Passed / failed | Useful (frozen) | Assessment |
|---|---|---|---|---|---|
| 2a UK BEV (retrospective) | A 6, B 8, C 15 | all | per [R2_STEP2A_ASSESSMENT.md](R2_STEP2A_ASSESSMENT.md) | C: 3 | none is a validated mechanism discovery |
| 2b UK hybrid (retrospective) | A 4, C 6 | all | A 3/1, C 5/1 | C: 1 (H3) | artefact (§1) |
| 2b UK BEV (**prospective**) | A 6, C 6 | **0** | pending | – | 6 nominal (2026-Q3 unpublished historical, 2026-Q4 nowcast) and 6 future (2027-Q1/Q2) |

Pending predictions are never merged with resolved ones. They resolve under the frozen protocol on the first
published vintage: 2026-Q3/Q4 on DfT's next releases, 2027-Q1/Q2 in later 2027.

## 5. Durability of the prospective record (verified)

All 12 pending tests are in `hypothesis_tests`, and their stored spec sha256 matches the report. They carry their real
registration times (2026-11-27T12:17:24Z and 12:18:04Z), their window classes and the frozen protocol. The evidence
snapshot is DfT VEH0160, retrieved 2026-11-23T13:00Z (sha256 `204fb505…`, raw file present), and the panel
fingerprint `3568da11…` is shared by both arms.

**Gap closed:** these records lived only in git-ignored local storage, a single copy.
`scripts/export_prospective_registry.py` now writes a small, tamper-evident registry:
`registry/prospective/r2s2b-33f66362ef91.json` (12 tests; specs, hashes, timestamps, snapshot hashes; registry sha256
`fbf12b1d…`; no credentials). `--verify` recomputes it against the store, and it is committed to git.

## 6. Expenditure

| Run | Cost | Ledger entries |
|---|---|---|
| Step 2b | **$0.0281639** (smoke $0.0000308; critic benchmark $0.0027586; 4 investigations $0.0253745) | 15 |
| Step 2a | $0.0395569 | 19 |
| Step 1 (all revalidation attempts) | $0.0063141 | 7 |
| r2v (original live validation) | $0.0053219 | 3 |
| **R2 total** | **$0.0793568** | 44 |
| All-time ledger | $0.1001157 | 68 |

## 7. Closing R2: what was learned

- **Engineering is reliable:** typed failures, isolation, reuse, prospective registration and spend safeguards all
  held.
- **The v2 contract makes Haiku produce testable, competing, partly against-trend predictions** that v1 did not
  (informativeness 0 against 0.42–0.73). **No validated mechanism discovery has been demonstrated**, and every
  resolved finding carries leakage risk.
- **Evaluation needs four fixes before any model comparison:**
  1. informativeness against persistence as well as momentum;
  2. no dispute credit against rivals already contradicted by the evidence;
  3. deduplicated correlated windows;
  4. a purified critic benchmark with deterministic verification of the critic's claims.
- **The strategist's failure is in Halibut's contract (invisible limits), not in its numbers.**
