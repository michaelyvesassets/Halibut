# Future experiment: baseline-aware hypothesis generation (draft pre-registration; NOT implemented)

Status: **recorded 2026-11-27 for later.** Not implemented, not approved, not run. It needs its own contract version,
pre-registration hash and paid-run approval. Motivation: [V2_M2_SCIENTIFIC_EVALUATION.md](V2_M2_SCIENTIFIC_EVALUATION.md)
§2.2. The Holt baseline matched or beat the models' directional claims in every cell; the only results that beat every
baseline were relative-growth "overtake" calls.

## Design

1. **The deterministic engine states the expectation first.** For every quantity in the slice it gives the validated
   baseline forecasts (persistence, momentum, Holt, reference class) with their intervals. These are the "expected
   outcomes".
2. **Agents investigate departures.** For each quantity, a hypothesis may:
   - **support** the baseline, with evidence for its assumptions (for example no structural change);
   - **adjust** it, with a mechanism, a structural-change argument or evidence the baseline ignores, and a signed
     adjustment against the baseline;
   - **abstain**, when the evidence cannot justify a view.

   **All three are first-class.** None is rewarded independently of accuracy and evidence quality.
3. **Adjustments are predictions against the baseline.** Each registers the baseline and the AI-adjusted value
   (numeric) or direction (binary) in the prediction ledger, and resolves on first publication.

## Measurement (pre-registered before any run)

- **Primary:** whether AI adjustments improve on the original statistical models. Proper-score skill of
  adjusted-against-baseline forecasts on resolved prospective questions, with unique realised outcomes and nominal
  intervals (correlation acknowledged).
- **By decision type:**
  - "adjust" accuracy against the baseline;
  - "support" accuracy (should be at least the baseline's);
  - the abstention rate and the baseline's error on abstained questions (did the agent abstain where the baseline was
    weak?).
- **Decision quality:** whether strategy options built on adjusted rather than baseline expectations change
  eligibility or simulated outcomes. Reported; no claim of effectiveness.
- **Guards:**
  - no credit for disagreement or novelty as such;
  - adjustments without evidence are scored but flagged;
  - leakage labels apply;
  - the sealed holdout is used only after this contract is frozen.

## Dependencies

- The prediction ledger (milestone 1) and the secondary evaluation (milestone 2): done.
- A probabilistic or interval output in the generator contract (calibration).
- Validated Holt and reference-class baselines per quantity, already computed in `evaluation_v21`.
