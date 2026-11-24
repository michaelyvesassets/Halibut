# Postmortem: R2 live validation r2v-b16fc9281536 (2026-11-24)

Status: **offline analysis complete; no further paid calls made.** The original experiment, its outputs
(`results/r2_validation/run-20261124T021311Z/`) and its pre-registered criteria (hash
`c9fcd277e0fb3d897a993d31a5b4dff90643d539d07ceb3f3c6818d8f66aaf5d`) are unchanged. Its recorded result stands:
pipeline criteria A2 and A3 failed, and B1–B5 failed. Related: [R2_LIVE_VALIDATION_PLAN.md](R2_LIVE_VALIDATION_PLAN.md),
[ADR-020](ARCHITECTURE_DECISIONS.md#adr-020).

## 1. Summary

| Question | Answer |
|---|---|
| Was it a failure of intelligence or of engineering? | **The stop was an engineering failure.** Measured by the unchanged criteria, the intelligence is **specific but weakly falsifiable**; see §3 |
| What stopped both investigations? | Each Haiku reply contained **5 explanations**. The prompt said "up to 4" and the local schema says `maxItems: 4`. Claude structured outputs do not enforce `maxItems` (Halibut strips it from the API schema), so the model was never constrained. Halibut's validator then treated the over-long *explanations* array as a whole-reply violation and discarded every valid hypothesis with it. The stopping rule ended the investigation before the critic and strategist |
| Where did it originate? | Validator and orchestration design (array overflow handled as a top-level failure), combined with a schema limit the provider cannot enforce. The trigger was the model ignoring a soft count instruction |
| Model, adapter, prompt, schema, validator, orchestration? | Model: exceeded a count (minor). Adapter: correct. Prompt: stated the limit; the provider cannot enforce it. **Schema/validator: primary.** **Orchestration: amplified** it into a stop |
| Why did the smoke test pass? | Its schema (`{"ok": boolean}`) has no arrays and no keywords the provider skips. It proved transport and structured-output acceptance, not the contract semantics of real investigation schemas |
| Spend | $0.005322: 1 smoke call + 2 generator calls, all `stop_reason = end_turn`; no retries; no truncation |

## 2. Root cause in detail

| Item | 2025-11-23 | 2026-05-23 |
|---|---|---|
| Request id | req_011ChLwmSvd7AmDDiw1bcY5m | req_011ChLwo32W5FacEvPgAsxG7 |
| Stop reason | end_turn | end_turn |
| JSON parse | valid | valid |
| Input / output tokens | 10,026 / 3,596 | 10,019 / 2,985 |
| Latency | 17.2 s | 14.6 s |
| Explanations / hypotheses returned | 5 / 4 | 5 / 3 |
| Recorded rejection | `$.explanations: more than 4 items (5)` → `call.schema_top_level` → status `stopped` | same |
| Item-level schema errors, missing fields, wrong types, bad evidence ids | **none** (all 17 items) | **none** |
| Narrative-number check | all pass | all pass |

Both replies were complete and parseable. The failure-reply text was not stored because the calls
succeeded; the complete replies are in the prompt cache. They are preserved verbatim as
`tests/fixtures/haiku_r2v_b16fc9281536_generator_replies.json`.

**Provider-specific issue.** The structured-output subset sent to Claude omits `maxItems`, `maxLength` and
`minItems` (documented as unsupported). Any limit expressed only with those keywords is advisory to the
model and enforced only after generation.

## 3. Intelligence assessment (independent of formatting; nothing here is counted as a result)

### Hypotheses found in the raw replies

| Date | Ref | Hypothesis (abridged) | Mechanism | Evidence ids real? | Assessment |
|---|---|---|---|---|---|
| 2025-11-23 | H1 | BYD's BEV mix falls because its **non-BEV registrations grow** (dilution), not BEV losses | Product-mix dilution | yes | Specific and plausible. Consistent with publicly reported BYD plug-in hybrid launches in the UK, so **possibly recalled** from training data. Its test (BYD segment share stays above 3.60%) does **not** discriminate dilution from simple growth |
| 2025-11-23 | H2 | Ford's BEV mix gain is a **product-led step-up**, not a fleet batch | New or refreshed models | yes | Plausible; probably informed by product knowledge outside the evidence. Test = trend continuation |
| 2025-11-23 | H3 | **VW and MG trade BEV share** (inverse coupling) | Brand substitution | yes | The most original claim. Its test was **mis-specified**: a VW–MG *gap* compared with MG's share *level* (4.14%). The description ("VW exceeds MG") actually held (+2.17 pp), but the test as compiled would have *failed* it |
| 2025-11-23 | H4 | Tesla's segment share falls through displacement by BEV entrants; its total share is held up **by non-BEV volume** | Competitive entry | yes | **Contradicted by the evidence**: Tesla's segment mix is 100% (all BEV), so it has no non-BEV volume. Its total share rose because the segment grew. Not caught: the critic never ran, and assumptions are not cross-checked against metrics |
| 2026-05-23 | H1 | BYD's falling BEV mix reflects a **deliberate hybrid and petrol range expansion** | Range expansion | yes | Plausible. "Petrol" is not supported: the evidence does not split non-BEV fuels. Its first test (segment mix stays below 58.35%) does test dilution |
| 2026-05-23 | H2 | Ford's BEV mix gain reflects a **launch or fleet programme** | Supply-side | yes | Restates trend continuation. Ford's segment share then fell (5.86% against 6.17%) |
| 2026-05-23 | H3 | Share is redistributed toward volume brands **with recent BEV launches**; MG loses | Competitive launches | yes | Original framing, but "recent launches" is outside the evidence. Its test was **mis-specified** in the same way as 2025 H3 (an MG–Kia gap against Kia's level), making it trivially true |

**Explanations** (10): 9 accurate. One is a factual error that passed the number check: 2026 E2 says "total
share **fell** from 0.44% to 2.47%", which is a rise.

**Numerical assumptions.** `stat.holt` with an invented parameter `smoothing_level = 0.3`. The registry
correctly rejected it ("not a parameter"); `causal.did` was correctly rejected as unavailable.

### What the fixed pipeline does with these exact replies (offline replay; diagnostic only)

The replay used the recorded replies served from cache. The critic and strategist are recorded as *not
reproducible*, because they never ran live.

| Date | Accepted | Original / restatement | Testable | Tests | Informative tests | Results | Statuses |
|---|---|---|---|---|---|---|---|
| 2025-11-23 | 4 of 4 | 1 / 3 | 3 | 3 (+1 not testable) | **0** | 3 passed | 3 survived (all **weak**), 1 untested |
| 2026-05-23 | 3 of 3 | 1 / 2 | 2 | 3 (+1 not testable) | **0** | 2 passed, 1 failed | 1 survived (weak), 1 refuted (a restatement), 1 untested |

**Conclusion.** Haiku's explanations and mechanisms are specific and mostly evidence-faithful, with one
contradicted assumption and one direction error. But **its observable implications are almost all "the
trend continues"**. Five of the seven hypotheses compile to exactly the tests trend extrapolation already
predicts, and its two genuinely original claims (brand-pair substitution) were mis-encoded. Under the
pre-registered criteria the intelligence would still fail B2 (originality: 1 per date), B3
(falsifiability 0.75 and 0.67) and B5 (informativeness 0%). It would pass B1 (fidelity 1.0) and B4
(testability 0.75).

Contributing causes:
- the prompt never told the generator that trend-consistent tests are uninformative;
- the measurement grammar cannot express mechanism-discriminating observables such as non-segment units
  or growth decomposition.

So the weakness is part model and part contract. It is not established that Haiku *cannot* produce
informative hypotheses.

## 4. Efficiency (generator input about 10,000 tokens against the mock's "2,680")

The mock's figure is an **estimate** (characters ÷ 4 of the prompt text only). The live figure is the
provider's count, which includes the schema and structured-output overhead. Measured at 2025-11-23:

| Component | Characters | Share | Necessary? |
|---|---|---|---|
| System prompt | 1,218 | 6% | yes |
| Slice: metric rows (50 rows) | 5,961 | 32% | values yes; **51% of row characters repeat what the id already encodes** (template, entity, window) |
| Slice: anomalies | 1,773 | 9% | yes (pre-computed movements); partly repeats metric values |
| Slice: market, grammar, windows, coverage | 1,437 | 8% | mostly; the grammar text repeats the schema |
| API schema (structured output) | 8,235 | 44% | yes, but **the 50-entry metric-id enum appears 3 times** (4,404 characters) |
| Provider overhead | about 200 tokens | — | inferred from the smoke call (229 tokens for about 200 characters) |
| Message history | none | — | single-turn |

The tokenizer runs at about **1.9 characters per token** on this dense JSON, which explains the
remaining gap.

Cost per call was about $0.0026. **Output tokens were 64% of the cost** (3.6k × $0.50/M against 10k ×
$0.10/M).

Avoidable duplication (proposals, not implemented):
1. Define the metric-id enum once with `$defs`/`$ref`, which structured outputs support (saves about
   2,900 characters).
2. Compact metric rows to id and value (saves about 3,000 characters).
3. When the strategist runs, place the slice in a shared cached prefix (cache read at 0.1×).
4. Cap explanations through the prompt (they consumed output without feeding tests).

Together, 1 and 2 save about 30% of input. At Haiku prices that is about $0.0003 per call; it matters for
Sonnet, at about 20× the price.

## 5. Fixes (implemented; offline-tested; strict validation preserved or strengthened)

| # | Fix | Why it is not a weakening |
|---|---|---|
| 1 | **Per-array rejection** (`split_multi`): an array that exceeds its limit is rejected **in full** (recorded as `schema.array_limit`, with its items kept verbatim), and the other arrays are judged on their own. The run stops only if the *hypotheses* array is unusable | Nothing is trimmed or repaired; the over-long array contributes nothing |
| 2 | **Like-with-like tests** (`compile_implication`): a reference metric must measure the same quantity as the implication; a share gap may only be compared with its own latest window | Stricter: mis-specified tests are now `not_testable` with a reason, instead of producing wrong verdicts |
| 3 | **Canonical signatures and prior-knowledge library**: comparing a metric with its own L4 is the same claim as `increase_vs_L4`; the reference library holds only earlier-dated, non-mock, non-reference hypotheses | Corrects a measurement bug. Originality no longer depended on which workforce ran first at the same date. The criteria text and hash are unchanged |
| 4 | **Direction check** (`check_directions`): "fell from 0.44% to 2.47%" is rejected (`validation.direction_contradiction`) | Stricter evidence fidelity |

Regression tests: `tests/test_r2_postmortem.py`, 8 cases built on the recorded replies. They cover:
- the recorded failure is detected and no longer discards valid hypotheses;
- hypotheses-array overflow still stops the run;
- the direction error is caught, and only it;
- mis-specified gap tests are refused;
- canonical signatures;
- novelty is independent of sibling runs;
- an end-to-end loop with 5 explanations completes and records the rejection.

All 291 tests pass.

## 6. Proposed R2 revalidation (needs a new, separate approval)

**Step 1, engineering revalidation.** Same design, dates, evaluation date, prompts, schemas and criteria.
The prompts are unchanged, so the generator requests hit the prompt cache and **reuse the two recorded
Haiku replies at no cost** (verified offline: 1 cache hit per date). This completes the original experiment
on identical intelligence. Live calls: 1 smoke + 2 critic + 2 strategist = **5 calls**.

- **Worst case:** about $0.018, or $0.036 if every call is retried.
- **Expected:** about $0.006.
- **Suggested cap:** $0.04.

The outcome is partly predictable (B2, B3 and B5 fail); its purpose is to show A1–A6 pass on the full
pipeline, including the critic and strategist under structured outputs.

**Step 2, intelligence improvement (optional, separate approval and a new pre-registration).** Version the
generator prompt as `loop.v2`:
- state that tests trend extrapolation already predicts are uninformative;
- require at least one mechanism-discriminating implication per hypothesis;
- give per-metric momentum direction in the slice.

Optionally, extend the grammar with a non-segment-units template (a contract change, ADR). A fresh
generator sample costs about $0.003 per date, so the step stays under $0.03 worst case. The B criteria
apply unchanged under a new criteria hash for the new prompt version.

## 7. Future multi-model direction (not implemented)

- **Route by role, measured per role.** The generator is the intelligence-critical role (hypotheses and
  implications). The critic and strategist are cheaper and more template-bound. The planned allocation
  (Haiku 100%, 80/20 and 70/30 with Sonnet) is best realised as *Sonnet for the generator on a share of
  dates*, routed by task complexity and judged on the same pre-registered metrics. These are targets, not
  quotas.
- **Prerequisite.** Fix informativeness first (Step 2). Otherwise model comparisons measure a ceiling the
  prompt and grammar impose rather than model capability.
- **Qwen (cloud).** The existing `QwenProvider` has JSON mode but no schema enforcement. Every limit is
  enforced locally, so the per-array rule matters even more. It needs loop-role support in the router and
  its own training-cutoff tag for leakage labelling.
- **Local open-weight Qwen.** An OpenAI-compatible local endpoint behind `LLMProvider`. It has no
  per-token spend, but its hardware cost should be accounted; the same harness, criteria and leakage rules
  apply.
- **Mixed workforce.** Per-role, per-model metrics are already reported (§4 of the plan). Promotion of any
  routing policy follows the existing evaluation rule and founder approval.
