# R2 Step 2a assessment (`r2s2a-c6fea6716c9a`, 2026-11-26)

Status: **offline assessment (2026-11-27); no paid calls; nothing changed in the active contract, the frozen criteria
(hash `223362694f7d254f…`) or the original results** (`results/r2_step2/run2a-20261126T212414Z/`).

| Item | Value |
|---|---|
| Spend | $0.0395569 (cap $0.10) |
| Exit code | 4 (K2 failed) |
| Passed | A1–A6, B1–B6, D1, E1, K1 |
| Failed | K2 |
| Not evaluable in 2a (`null`) | C1–C4, P1–P2 |

## 1. The three useful findings, checked independently

The frozen definition of a validated useful result: an original hypothesis, no failed test, and at least one passed
informative test (against the trend, or on a disputed quantity of a gated competing pair).

### H1 (UK BEV, origin 2025-08-23, arm C)

- **Observed movement:** BYD's BEV mix fell from 100.0% (P4) to 71.85% (L4), while its BEV segment share rose from
  0.75% to 2.89% and its non-segment share rose from 0.00% to 0.29%.
- **Mechanism:** BYD broadened into non-BEV (hybrid) models, diluting its mix; BEV demand did not fall.
- **Competing explanation (H2):** BYD's BEV demand weakened in absolute terms.
- **Supporting evidence:** segment_mix L4, units_growth L4, non_segment_share L4. The last is an extended
  measurement; arms A and B could not see it.
- **Distinguishing prediction (2025-Q2/Q3):** BYD segment share stays above 2.89% (H1) or falls below it (H2). Also
  registered: non-segment share stays above 0.29%.
- **Outcome:** 6.02% and 1.55%. H1 passed both tests; H2 failed.
- **Assessment: weak as a discovery.**
  - The rival was already contradicted at the origin (BYD's BEV share had quadrupled); the critic said so.
  - The decisive test was trend continuation for H1 (`trend_consistent = true`).
  - The mechanism is directly visible in the new non-segment-share metric. That makes H1 a well-grounded explanation
    of the observed movement, not a forecast discovery.
  - The critic's high-severity "factual contradiction" against H1 was **itself wrong**: it read non-segment share
    (a share of the whole non-BEV market) as a share of BYD's own units.
- **Leakage:** high. BYD's UK plug-in hybrid launch was widely reported before the model's June 2026 cutoff, and the
  outcome window predates it.
- **Open questions:** the split between plug-in hybrid, hybrid and petrol is unobserved (recorded as information gap
  G1); fleet and import timing.

### H4 (UK BEV, origin 2026-02-23, arm C)

- **Observed movement:** Volkswagen's BEV mix rose from 10.93% to 19.10%; its BEV segment share rose from 5.51% to
  7.70%; its non-segment share was 9.31%, then 9.39%.
- **Mechanism:** a denominator effect: Volkswagen's non-BEV volume is shrinking.
- **Competing explanation (H3):** a genuine BEV volume push.
- **Distinguishing prediction (2025-Q4/2026-Q1):** Volkswagen's BEV segment share falls below 7.70% (H4) or stays
  above it (H3). Also registered: non-segment share falls below 9.39%.
- **Outcome:** 5.60% and 9.05%. Both H4 tests passed, and **both were against the trend**. H3 failed.
- **Assessment: the strongest finding, but a predictive signal with an unsupported mechanism.**
  - At the origin, the past movement was a BEV gain (H3's account) with flat-to-rising non-BEV share.
  - The critic flagged this as a high-severity factual contradiction citing the right metrics. The frozen evaluation
    does not use critiques.
  - So H4 correctly predicted a reversal, but for a reason the evidence does not support.
- **Leakage:** moderate to high. Volkswagen's 2025-Q4/2026-Q1 UK results predate the cutoff.
- **Open questions:** competition. BYD overtook Volkswagen in the same window (a sibling test, H2.I4, passed at
  +2.34 pp). Also model cycles, fleet timing, and reversion after a strong year.

### H6 (UK BEV, origin 2026-02-23, arm C)

- **Observed movement:** Ford's BEV segment share rose from 1.67% to 5.11% and its mix from 4.83% to 20.18%; its
  non-segment share fell from 6.80% to 5.82%; its units fell 2%.
- **Mechanism:** Ford's BEV range expansion lifts its mix while non-BEV volume contracts.
- **Competing explanation (H5):** the mix gain comes from a shrinking non-BEV base, not BEV expansion.
- **Distinguishing predictions:**
  - Ford's BEV segment share stays above 5.11% (H6) or falls below it (H5);
  - the Ford–BYD gap falls below zero (BYD overtakes Ford).
- **Outcome:** 7.27% and −0.66 pp. Both H6 tests passed; H5's share test failed (its total-share test passed).
- **Assessment: moderate to weak.**
  - H5 was contradicted at the origin (Ford's BEV share had tripled).
  - The disputed test is trend continuation for H6.
  - The one against-trend success (BYD overtakes Ford; momentum predicted the opposite) follows from BYD's faster
    growth, not from H6's Ford mechanism.
- **Leakage:** high. Ford's 2025 BEV ramp and BYD's rise were widely reported.

**Overall:**
- the frozen result stands at 3 useful results;
- independently, **1 is a strong predictive signal with an unsupported mechanism (H4), 1 is moderate (H6) and 1 is
  weak (H1). None is a validated mechanism discovery**;
- under a stricter reading (at least one passed against-trend test and no failure), **2** remain: H4 and H6;
- all three are retrospective, with substantial leakage risk, so only Step 2b's prospective windows can separate
  reasoning from recall.

**Evaluation weaknesses found** (secondary readout proposed in §7; the frozen primary is not changed):
1. Dispute credit counts a trend-consistent test as informative even when the rival was already contradicted at the
   origin. This happened in 2 of the 3 pairs (H2 in 2025, H5 in 2026).
2. "Useful" ignores evidence-cited factual-contradiction critiques (H4).
3. Critiques cannot become a gate until their numbers are verified deterministically: one of the four
   factual-contradiction critiques in arm C was wrong.

## 2. The A/B/C comparison

| Check | Finding |
|---|---|
| Identical base evidence | A5 passed: same panel fingerprint per origin. A and B saw the same 50 metrics; C saw 84 (the extended set, by design) |
| Comparable rules | One common method (`evaluation.common`) for all arms; competing pairs are detected from predictions whether declared or not |
| Reference libraries | Identical across arms at each origin: only claims with an earlier as-of count, so same-date sibling runs are excluded and execution order within an origin has no effect. 2026-02-23 investigations are judged against all arms' 2025-08-23 claims and the Step 1 claims, symmetrically. C's own 2025 dilution claim made its 2026 BYD hypothesis a restatement, the intended behaviour |
| Criterion scope | **B1–B5 and K1–K2 are measured on arm C only.** B3 (competing pair) was required only of C, and **arm B had 0 competing pairs**. B6 compares B with A |
| B6 (+0.375) | Comes entirely from arm B's 3 against-trend predictions, **all refuted** (0 confirmations). It is a contract-plus-presentation effect on risk-taking, not on accuracy |
| Dispute credit | Without it, arm C's informativeness is 8/15 = 0.53 (B4 still passes); with it, 11/15 = 0.73 |
| Sample | 2 investigations per arm; arm C's 2025-08-23 run accepted only 2 hypotheses. **No statistical claim is possible** |

Partial rejections (hypotheses accepted/rejected; other rejected items):

| Arm, origin | Hypotheses | Other rejections |
|---|---|---|
| A 2025-08 | 4/0 | 0 options |
| A 2026-02 | 2/0 | 1 option (one-entity gap target, ADR-021) |
| B 2025-08 | 3/1 | fabricated evidence id; 3/3 options; 1 critique; 1 gap |
| B 2026-02 | 5/1 | direction contradiction; 4/4 options; 3 critiques; 2 gaps (to rejected hypotheses); 1 explanation |
| C 2025-08 | 2/0 | 1 explanation; 4/4 options |
| C 2026-02 | 6/0 | 2 explanations; 3 critiques; 1 gap; 4/4 options |

The rejections do not change the hypothesis results: B1 is computed on hypotheses, at 8/8 for arm C. The critique
rejections reduce critic coverage, and the strategist rejections are the whole of K2.

## 3. Strategic intelligence: why K2 failed

| Arm | Options proposed | Accepted | Rejected | Cause |
|---|---|---|---|---|
| A (v1) | 9 | 8 | 1 | gap target without a second entity |
| B (v2) | 7 | 0 | 7 | `numeric_claims[].text` longer than 24 characters |
| C (v2) | 8 | 0 | 8 | same |

**All 15 v2 rejections had a single cause.** The model wrote phrases ("BYD L4 segment share of 2.8878%") where the
contract expected the bare number token. The 24-character limit is **invisible to the model**: `maxLength` and
`maxItems` are stripped from the API schema because structured outputs do not enforce them. The same invisible limits
also rejected 7 critiques (evidence lists of 4 against a limit of 3) and 2 explanations (8 evidence ids against 6).
**This is a contract design defect on Halibut's side.**

Counterfactual check of the declared numbers themselves (rejected options taken from the validation records; it does
not change any result):

| Failure category | Count |
|---|---|
| Invented numbers | **0** |
| Incorrect calculations | **0**: all 16 observed or calculated values reproduce exactly |
| Assumed values | 15 of 18 equal an assumed magnitude; 3 are range midpoints (1.0 for 0.5–1.5), a format problem |
| Missing declarations | some derived numbers left undeclared (for example 28.15 = 100 − 71.85, the gap −4.11, a P4 value) |
| Objective/success contradictions | **0** of 15 |
| Unsupported assumptions, insufficient evidence | none found |

**Recommendation: separate calculation from reasoning,** in a separately versioned strategist contract (proposed,
not implemented):
- the engine publishes a **quantity catalogue**: each metric, L4−P4 differences, pairwise gaps, complements and
  ratios, each with an id and a verified value;
- the strategist references quantity ids in text (`{Q12}`) and gives assumed magnitudes only in the numeric
  low/high fields;
- any digit in free text is rejected, and the engine renders the numbers;
- every enforced limit is stated to the model.

Integrity becomes stronger, since a number can only come from the catalogue or from a labelled assumption field. The
eligibility rules are unchanged.

## 4. Efficiency (API usage, 2 investigations per arm)

| Arm | Input tokens | Output tokens | Cost | Per useful | Per informative confirmation | Per informative refutation |
|---|---|---|---|---|---|---|
| A | 44,385 | 13,841 | $0.01136 | none | none | none |
| B | 45,340 | 16,455 | $0.01276 | none | none | $0.00425 |
| C | 58,656 | 19,080 | $0.01541 | **$0.00514** | $0.00220 | $0.00385 |

Smoke test: $0.0000308. All costs are incremental; there were no cache hits and no reuse.

- **Generator:** input fell 18% from A to B (20,042 to 16,488), as E1 required. Output rose 41% (5,991 to 8,429:
  rivals, gaps, mechanisms). At 5 times the input price, **the extra output costs more than the input saved**.
- **Largest waste:** **37% of v2 spend went to strategist calls whose output was rejected** ($0.0104 of $0.0282 in
  B and C). Fixing contract usability (§3) is the biggest efficiency gain.
- **Critic input grew 2.3–3.3 times**, because it now sees the evidence. Its cost stays small ($0.0027 per arm), and
  it produced the H4 and H2 catches. Keep it.
- Prompt caching would save little: the shared prefixes are small next to the per-origin evidence.

## 5. Step 2b

- **Data freshness:** DfT was retrieved on 2026-11-23 (2026-Q2 is the latest published quarter), so it is **fresh
  until 2026-11-30T13:00Z**. After that, the run refuses until the data are refreshed.
- **Registration now:** 2026-Q3 would be `unpublished_historical`, 2026-Q4 `partially_elapsed`, and the future window
  2027-Q1/Q2. The protocol and hash are unchanged.
- **Recommendation: proceed with 2b unchanged,** to keep comparability. Do not patch v2 inside the frozen benchmark.
- **Expect K2 to fail again** because of the known contract defect; report it, do not fix it within 2b.
- **The critic benchmark (C1–C4) may be depressed** by the same invisible evidence limit. Report, as a clearly
  labelled offline diagnostic, the critiques rejected only for that limit. The frozen scores are unchanged.
- **Strategy:** use a **separately versioned strategist experiment** (strategy contract v3, §3), first on the
  development dates, then on held-out and prospective origins with its own pre-registration.

## 6. Multi-model pathway (proposal; nothing implemented or run)

What the measured Step 2a results say about each role:

| Role | Share of arm C cost | Step 2a evidence | Proposal |
|---|---|---|---|
| Hypothesis generator | 45% | Produces the intelligence. Weaknesses: rivals contradicted at the origin (2 of 3 pairs); a mechanism contradicted by evidence (H4) | **First candidate for a more capable model (Sonnet)**: role-specific, on held-out and prospective origins, with the same common method |
| Strategist | 38% | Failures were contract usability, not reasoning: numbers correct, 0 contradictions | **Fix the contract first (v3)**, then test Sonnet on strategy quality only if Haiku passes v3 |
| Critic | 17% | One correct high-value catch (H4), one factual error (H1); benchmark pending (2b) | Decide after C1–C4; a candidate for a cheaper or local model if the benchmark is strong |

Experiments (each separately pre-registered and approved; arms on identical evidence; costs from the official price
table at proposal time):
1. **Role swap:** Sonnet generator with the Haiku critic and strategist, against all-Haiku. This is the measured
   form of "80/20".
2. **"70/30":** Sonnet generator and strategist (after v3), with the Haiku critic.
3. **Sonnet strategist only:** v3 contract.
4. **Independent Qwen benchmark:** all roles, same contracts and criteria (Qwen config exists; approval-gated).
5. **Mixed collaboration:** for example a Qwen or local critic with a Claude generator, then locally hosted Qwen.

Allocation follows measured per-role performance and cost per validated result, not token quotas. The router
fingerprint keeps every model mix's results separate.

## 7. Proposed follow-ups (not implemented; need approval)

1. **Secondary evaluation readout v2.1** (versioned; the frozen primary is unchanged):
   - no dispute credit when the losing rival was contradicted at the origin;
   - flag "useful" results carrying an evidence-cited factual contradiction, once the critic's numbers are verified
     deterministically.
2. **Make every enforced schema limit visible** to the model (contract version bump).
3. **Strategy contract v3** (quantity catalogue).
4. **The post-hoc diagnostic for 2b** described in §5.
