# R2 Step 2 proposal: from trend recognition to discriminating explanatory hypotheses

Status: **proposal only (2026-11-24); not implemented, not approved, not executed.** It needs founder approval, a new
pre-registration (new prompt version and criteria hash) and its own paid-run approval. Evidence base:
[R2_POSTMORTEM_r2v-b16fc9281536.md](R2_POSTMORTEM_r2v-b16fc9281536.md).

## 1. Problem

On the recorded replies, Haiku proposed specific mechanisms, but 5 of its 7 hypotheses compiled to tests that trend
extrapolation already predicts (0 informative tests). Its two most original claims could not be expressed correctly
in the measurement set. Three causes:
1. the generator was never told what makes a test informative;
2. it was not asked for competing mechanisms that predict *different* observations;
3. the measurement set cannot observe several mechanisms it proposed (for example, non-BEV volume).

The objective is **evidence-backed explanatory and strategic causal intelligence**: competing explanations, tested by
observations on which they disagree, with missing data named rather than imagined.

## 2. Proposed changes

### 2.1 Separate mechanism discovery from trend extrapolation
- Give the generator, per metric, the direction simple momentum predicts (`trend_direction`, from the registered
  `baseline.momentum` model) and state the rule: **a test that trend extrapolation already predicts does not count as
  evidence for a mechanism**.
- Require every hypothesis to carry at least one **against-trend or mechanism-specific** implication. Hypotheses
  without one are recorded as `trend_restatement` (kept, not counted as original). Validation is unchanged.

### 2.2 Competing explanations for the same movement
- A new generator output, `movements`: for each of up to 3 selected anomalies, **2 or 3 rival mechanisms** (for
  example dilution by new non-BEV models, against loss of BEV demand, against fleet timing). Each rival is a
  hypothesis with its own implications.
- Movements are chosen from the deterministic anomaly scan, so the agent explains *observed* movements and does not
  invent them.

### 2.3 Discriminating predictions
- For each pair of rivals, at least one **discriminating implication**: a pre-registered test whose outcome differs
  between them. The compiler checks this mechanically, by verifying the rivals predict opposite comparators on the
  same metric, entity and window.
- Evaluation adds **discrimination**: a test that resolves one way refutes one rival and leaves the other standing.
  This becomes the main unit of "useful learning", distinct from successful prediction and from informative
  refutation.

### 2.4 Missing measurements instead of invented explanations
- A new output, `measurement_requests`: what observation would discriminate the rivals, which provider could supply
  it, and why it is not in the evidence. These become L2 data questions, not hypotheses.
- Mechanisms that need such data stay `untested` and link to their request. The critic is asked to flag claims that
  rely on facts not in the evidence (launches, fuel splits, price moves).

### 2.5 Measurement-set expansion (where justified by the recorded failures)

| Template (proposed) | Definition | Justification |
|---|---|---|
| `non_segment_share` | entity non-segment units / market non-segment units | tests "dilution by non-BEV growth" (BYD, Tesla) |
| `entity_units_growth` | entity units, L4 against P4 (ratio) | separates share loss from volume loss |
| `segment_gap_vs_zero` | segment_share(a) − segment_share(b) compared with 0 | the correct encoding of "A exceeds B" (2025 H3) |

Each needs an ADR, compiler and registry support, and tests. All are computable from the existing DfT and
`csv_panel` evidence, so they are industry-neutral.

### 2.6 Fewer repeated schema and evidence tokens, without losing information
- Define the metric-id enum once with `$defs`/`$ref`, which structured outputs support; the local validator gains
  `$ref` support. This removes about 2,900 characters.
- Compact metric rows to `[id, value]`, since template, entity and window are encoded in the id (about 3,000
  characters).
- Drop the grammar prose from the slice (the schema carries it).
- Put the slice in a cached prefix shared by the generator and strategist calls (cache read at 0.1×; prompt caching
  enabled only for this prefix).
- Expected effect: about 30% less input, with no evidence removed.
- Guard: run a token-reduction test on the recorded replies first, checking that every metric the generator cited is
  still present in the compact slice.

### 2.7 Fresh held-out investigations for model comparisons
- **Development set:** the two dates already used (2025-11-23, 2026-05-23). These replies are used for prompt
  development only and never again for comparison.
- **Held-out set:** investigations not yet run:
  - a second market using the industry-neutral panel, where data exist (for example Norway BEV from SSB, already
    ingested);
  - additional UK dates, labelled for training-data contamination (all UK test windows up to 2026-Q2 predate or
    straddle the June 2026 cutoff, so a held-out set needs another market, or future quarters as they are
    published).
- Pre-registered criteria v2 (new hash) are frozen before the held-out runs. Comparisons across models (Haiku,
  Sonnet, Qwen) use only held-out investigations with fresh generator calls.

### 2.8 Critic evaluation that does not depend on keywords (added 2026-11-26)
The keyword screen misjudged both directions in r2r-57fa83dc8f2f. Proposed instead:
- **Structured critiques:** each critique states an issue type (from a fixed taxonomy: contradicted by evidence,
  unsupported detail, non-discriminating test, mis-encoded comparison, confounder, power) and, where it claims a
  contradiction, the metric id that shows it. Halibut checks the cited metric deterministically.
- **A planted-defect benchmark:** held-out hypothesis sets with defects of known type and location, planted by the
  engine (never by an agent), plus clean controls. Critic recall and precision are measured per defect type, with
  false alarms on clean controls counted.
- **Pre-registered, blinded manual adjudication** for free-text findings: two-rater rubric (identifies the defect /
  partially / not), raters blind to the model and to the planted list, and disagreements recorded.
- A capability claim needs recall and precision above pre-registered thresholds on held-out sets. A single reply
  (such as the r2r-57fa83dc8f2f H1 finding) is a promising observation only.

## 3. Proposed cost (to be approved separately)

| Part | Calls | Worst case |
|---|---|---|
| Offline: implement 2.1–2.6 with tests; replay the recorded replies (token-reduction check) | 0 | $0 |
| Development run: fresh Haiku generator, critic and strategist on the 2 development dates | 1 smoke + 6 | about $0.03 |
| Held-out run (later, separate approval): 2 to 4 new investigations | about 13 | about $0.05 |

## 4. What this proposal does not do

It runs no Sonnet or Qwen experiment, starts no R3, promotes no lessons and takes no real-world action. Model routing
(§7 of the postmortem) waits for the held-out evaluation.
