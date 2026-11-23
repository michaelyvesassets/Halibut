# First live Claude benchmark: diagnostic findings (input to Master Architecture v1.0)

Status: diagnostic record. No paid calls were made during the diagnosis. **R1 (2026-11-23)
implemented most of the proposed fixes**; see "Status after R1" at the end and
`docs/architecture/R1_IMPLEMENTATION.md`.

## Run

| Item | Value |
|---|---|
| Date | 2026-11-23 |
| Market | `configs/intel/markets/uk_cars_bev_v1.toml` (UK, BYD vs Volkswagen) |
| Origins | 10 half-yearly, 2021-11-23 … 2026-05-23 (same as the deterministic reference) |
| Model | `claude-haiku-5-5`, thinking disabled, no prompt caching |
| Replays | reference `rpb7f3308e27`, Haiku `rpf5673fab06` |
| Report | `results/intel/benchmarks/benchmark_anthropic_rpf5673fab06.json` (git-ignored) |
| Spend | $0.02075 for 23 calls (hard cap $0.073; plan worst case $0.0366, or $0.0733 with retries) |
| Latency | mean 4.75 s, max 7.82 s per call |
| Evidence snapshots | hashes identical before and after the run |

| Metric | Reference | Haiku |
|---|---|---|
| Valid hypotheses | 61 | 10 |
| Rejected by validation | 0 | 17 |
| Supported / refuted | 41 / 20 | 7 / 3 |
| Direction hit rate | 0.820 | 0.800 |
| MAE | 1.230 pp | 1.949 pp |
| 80% coverage | 0.787 | 0.800 |
| Cost per useful validated result | $0 | $0.0030 |

**Reproducibility.** The run was reconstructed offline on a throwaway copy of the store, with
a provider that serves only the cached Haiku replies. The reconstruction reproduced every
metric exactly. All 10 Hypothesis prompts were byte-identical cache hits, which confirms that
the agents saw exactly the evidence recorded.

## Findings

### F1. The agents only *select* predictions the engine has already built (architectural)

`to_hypothesis` builds each prediction from engine metrics. The LLM chooses only the `kind`
(momentum, displacement or exposure) and the `subject`, and the numbers never come from the
agent. As a result:

- all 10 valid Haiku predictions are **identical** to predictions in the reference replay
  (same origin, subject, point and outcome). On those 10, the reference scores exactly the
  same (MAE 1.949 pp, hit 0.80, coverage 0.80);
- an accuracy gap between the two agent sets can come only from *which* predictions are
  selected. In this design an LLM cannot produce an original hypothesis, model or forecast;
- the displacement prediction ignores the agent's subject (it uses the market's focal and
  target), and every exposure hypothesis maps to the same market-level prediction whatever
  its subject. Two of the 10 scored predictions are therefore duplicates (8 unique).

**Architectural requirement:** explanations, hypotheses and numerical forecasts are
different outputs. Each needs its own responsibilities, validation rules and evaluation
metrics, and agents must be able to originate hypotheses and models, not only choose among
predefined ones.

### F2. The 17 "fabricated or missing metric" rejections were format errors

| Cause | Count |
|---|---|
| `evidence` returned as a dict: its keys (field names) became evidence ids | 13 |
| metric id with a value appended (`"metric:segment_share:L4:volkswagen = 8.1321"`) | 4 |
| a fabricated metric id or number | **0** |

Counterfactual (measurement only): metric ids were extracted from each reply and checked with
the **unchanged** validator, keeping the original run's trajectory. 35 of 36 proposed
hypotheses would have passed. The one exception is a momentum hypothesis for
`market:battery_electric`, an unsupported kind. Every metric id and narrative number Haiku
used exists in the evidence bundle.

The current classifier (`benchmark._failure_types`) files these format errors under
"fabricated_or_missing_metric". That label overstates the problem.

### F3. Nine hypotheses were dropped before validation, without being counted

- 8 were momentum hypotheses with subjects such as `brand:byd`, while the engine looks up `byd`;
- 1 was `market:battery_electric` (unsupported);
- a related cosmetic bug: exposure subjects become `brand:brand:tesla`.

### F4. Thirteen calls were truncated; there were no refusals

| Type | Count |
|---|---|
| truncated, `stop_reason=max_tokens` | 13: all 10 Research calls (600-token cap, ~436 input tokens) and all 3 Critic calls (1,600) |
| genuine refusals | 0 |
| invalid JSON in a complete reply | 0 |
| API errors or retries | 0 |

The Research prompt sets no limit on the number of actions or the length of reasons. The text
of failed replies is not stored, so what filled the 600 tokens cannot be confirmed. That is a
diagnostic gap. Truncated Research and Critic calls did not reduce the number of valid
hypotheses, because neither feeds scoring.

### F5. 30 calls were planned but 23 were made

The Critic runs only for origins with at least one valid hypothesis: 3 of 10 here. That gives
10 Research + 10 Hypothesis + 3 Critic calls. The plan deliberately priced a Critic call for
all 10 origins.

### F6. The comparison is not statistically meaningful

- Haiku had an effective n of 8 unique predictions, against 61 for the reference.
- Direction hit rate: Fisher exact p = 1.0. Haiku's 8/10 has a 95% Wilson interval of 0.49–0.94.
- MAE difference (Haiku − reference): bootstrap 95% CI −0.37 to +1.86 pp, which includes 0.
- Selection test: the MAE of the 8 unique Haiku picks is 1.99 pp. Random sets of 8 of the
  reference's predictions at the same 3 origins have a median of 1.20 pp, and
  P(random ≤ Haiku) = 0.98. This is weak evidence that the picks were worse than chance, and
  it is confounded: which hypotheses survived depended on formatting, and Haiku preferred
  market-level and displacement predictions, which have larger errors.

For context, the simple forecasting baselines in the reference replay forecast brand shares,
a different set of quantities. They are context, not a head-to-head comparison:

| Method | n | MAE | 80% coverage |
|---|---|---|---|
| level, empirical interval | 72 | 0.55 pp | 0.92 |
| level, naive interval | 80 | 0.61 pp | 0.75 |
| last quarter | 80 | 0.71 pp | 0.64 |
| momentum | 80 | 0.72 pp | 0.71 |

### F7. Leakage

- **Evidence:** no leakage found.
  - Each origin sees only the DfT VEH0160 quarters whose estimated release date (quarter end
    + 121 days) falls before the origin; for example, the 2024-05-23 origin sees data up to
    2023-12-31.
  - The only recorded release date (Q2 2026) came 85 days after quarter end, so the
    estimate is the more cautious one.
  - All 695 scored outcomes became available after their origins.
- **Open:**
  - historical release dates are estimated, not verified;
  - the store holds a single vintage (the latest file), so later revisions are visible at
    earlier origins;
  - Claude's training data may include UK electric-car outcomes. That could leak only
    through the agent's choices, and this run shows no benefit from it, but it cannot be
    ruled out. An architecture with original forecasts needs explicit controls for this,
    such as post-training-cutoff evaluation windows.

## Proposed fixes (deferred to the architecture milestone)

1. **Enforce the output schema when the reply is generated**, with structured outputs
   (`output_config.format`):
   - `evidence` must be an array of metric-id strings;
   - `kind` and `subject` are limited to values present in the bundle;
   - `maxItems` and length limits apply.

   Malformed output must stay rejected; the system must not repair it after the fact.
2. Bound Research and Critic replies: at most 3 actions, one critique per hypothesis, and
   short reasons.
3. Store the text of failed or unparseable replies, up to a size limit, in the call log.
4. Remove duplicate predictions (same origin and prediction) before scoring, and fix the
   exposure subject label.
5. Count drops made before validation. Split the failure types into format, schema,
   unsupported, fabricated and number mismatch.
6. Benchmark reports: include per-origin rejection reasons and note that the Critic runs
   only when there are valid hypotheses.
7. Evaluate selection against random selections from the same pool, and compare models only
   on matched predictions.

## What the run established

- The live path works end to end within the caps. The provider, ledger, validation and
  reproducibility all behaved as designed.
- There were no fabrications and no refusals, and validation caught every malformed reply.
- It did **not** establish any accuracy difference, any skill at choosing predictions, or any
  value from LLM agents in the current design. The current design cannot test whether agents
  produce original insight.

## Status after R1 (2026-11-23)

| Proposed fix | Status | Where |
|---|---|---|
| 1. Schema enforced at generation, with enums from the bundle; no repair | Implemented; **live behaviour untested** | `src/halibut/epistemic/schemas.py`, `src/halibut/agents/anthropic_provider.py` |
| 2. Bounded Research and Critic replies | Implemented: limits in prompts and schemas, enforced locally by rejection | `src/halibut/agents/orchestrator.py` (prompt v2) |
| 3. Text of failed replies stored | Implemented | `agent_calls.response_excerpt` |
| 4. Duplicate predictions removed; exposure label fixed | Implemented | `src/halibut/intel/replay.py:unique_predictions`, `to_hypothesis` |
| 5. Drops counted; failure types split | Implemented | `src/halibut/epistemic/failures.py`, `validation_records` |
| 6. Per-origin rejection reasons; conditional critic noted | Implemented | replay per-origin classes; benchmark `critic_calls` |
| 7. Matched and selection-aware comparison | Partial: matched overlap count implemented; selection-vs-random test not | `src/halibut/intel/benchmark.py:overlap` |

The offline reconstruction (`halibut intel benchmark … --reconstruct rpf5673fab06`) reproduces
this run's numbers and classifies every failure as diagnosed above.

