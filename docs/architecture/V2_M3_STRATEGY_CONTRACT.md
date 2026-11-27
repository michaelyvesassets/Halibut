# Architecture V2, milestone 3: strategist contract `strategy.v3`

Status: **implemented and tested offline (2026-11-27); awaiting founder review.** No paid calls; `loop.v1`, `loop.v2`,
the frozen Step 2 benchmark and the sealed holdout are unchanged.

**Principle: models propose strategies and mechanisms; deterministic tools calculate and verify every quantity.** A
structurally valid option is a well-formed proposal. It is not evidence that a strategy works.

## 1. Implementation (`src/halibut/investigation/strategy_v3.py`; selected with `contract="loop.v2", strategist="v3"`)

| Requirement | Implementation |
|---|---|
| Engine quantities with stable ids | the catalogue, computed before the call: `q:obs:` (evidence metrics), `q:diff:` (L4−P4 in pp), `q:gap:` (focal or target pairwise gaps in pp), `q:compl:` (100 − segment mix), `q:base:` (official baseline point and 80% interval from a registered model). Each quantity carries its kind, unit, definition and inputs |
| Explicit numerical provenance | text may cite `{q:…}`; the engine renders "3.60% [observed]". The option payload keeps every cited quantity with its provenance |
| No unsupported numbers | **no digit in any free-text field** (title, basis, rationale, statements, justifications, descriptions, risks, limitations, cost basis). Assumed effects go only in `magnitude_low_pp`/`magnitude_high_pp`; costs only in the numeric cost fields. Unknown references are rejected |
| Structured assumptions | the effect assumption has a justification, a basis (analogy, expert judgement, evidence ratio, unknown) and an uncertainty; every assumption and every competitor response carries a justification |
| Limits communicated | `describe_limits(schema)` states every `maxLength`, `maxItems` and `minItems` in the system prompt (the API strips them) |
| Kinds kept distinct | observed, calculated, model (baseline), assumed (magnitudes) and simulated (scenarios) are each labelled |
| Alignment | the objective equals the intended effect (quantity and direction); the success criteria agree; actions use the option's levers |
| Actor responses | competitor responses with likelihood, effect on the option, and a justification; they drive the simulated scenarios |
| Forecastable subclaims | 1–3 per option in the measurement grammar, compiled into registered tests and **registered in the prediction ledger** (`source: strategy_subclaim`), so they resolve automatically |
| Baseline and alternative scenarios | the engine computes the baseline (no action: official baseline) plus no-response and per-response scenarios (simulated, uncalibrated) |
| Calculation requests | difference, ratio or sum of catalogue quantities; the engine computes supported ones and records the rest as typed `calculation_unavailable`. Nothing is invented |
| Measurable outcomes, limitations, eligibility | success criteria and subclaims; a `limitations` list; **the experiment eligibility rules and approvals are unchanged** |

## 2. Offline validation (fixtures; no claim about live model behaviour)

**Replay of the 22 real Haiku v2 options rejected in Steps 2a and 2b**
(`tests/fixtures/strategist_v2_rejected_r2.json`, verbatim, with their evidence):

| Result | Value |
|---|---|
| The v2 failure (a `numeric_claims` text over the invisible 24-character limit) | **cannot occur in v3**: the field no longer exists, and every limit is stated |
| Declared numbers | 53: **28 observed or calculated, all computable from the catalogue (0 unreferencable)**; 18 assumed (go to the magnitude fields); 7 assumed values written as a midpoint or range (would need the low/high fields) |
| Options fully expressible in v3 as written | 15 / 22 |
| A v2-format option submitted as v3 | **rejected** (digits in free text): v3 does not loosen integrity |

Failure modes retained by tests:
- **fabricated numbers:** O1's "baseline point 7.27%" is rejected as a digit in text;
- **invalid references:** an unknown `{q:…}` is rejected;
- **unsupported assumptions:** a missing justification is rejected;
- **contradictory objectives:** O3 (decrease the gap, against a criterion that it increases) is rejected, as is an
  objective that differs from the intended effect;
- **levers:** an action using a lever outside the option's levers is rejected;
- **calculations:** a request with a missing input is `calculation_unavailable`, with no value.

Evaluation dimensions (offline, mock strategist on the fixture market; mock output is not intelligence):

| Dimension | Offline result |
|---|---|
| Contract compliance | mock options pass v3 validation; v2-format text fails |
| Numerical integrity | every number in text is an engine quantity; the rest are numeric fields |
| Strategic coherence | objective, effect and success alignment enforced; actor responses justified |
| Evidence grounding | `quantity_refs` exist in the catalogue; linked hypotheses exist |
| Testability | every accepted option has 1–3 compiled subclaims registered in the ledger |
| Decision relevance | scenarios (baseline and alternatives) present; eligibility reported (exploratory unless every check passes) |

**Estimated cost** (real UK evidence, 2026-02-23, extended measurements):

| | Prompt + schema characters |
|---|---|
| v2 strategist | 21,287 |
| v3 strategist | 32,110 (+51%) |

- The extra is the quantity catalogue; it was compacted to `[id, value]` rows with a legend, and the quantity-id enum
  was dropped from the schema. That is about 2,700 more input tokens, roughly $0.0003 per call at Haiku prices.
- On output, v3 removes `numeric_claims` and adds actions, justifications and subclaims, so it should be roughly
  neutral (to be measured live).
- The main expected saving is **fewer wasted calls**: every v2 strategist call in Step 2 was rejected (37% of v2
  spend).

**Tests:** 8 new tests (`tests/test_strategy_v3.py`); the full suite passes.

**Backward compatibility:** v1 and v2 strategists are unchanged (tested); the Step 2 criteria hash is pinned; v3 is
rejected with `loop.v1`.

## 3. Remaining limitations

- **Live compliance is unknown.** Whether Haiku follows "no digits, cite quantities" can only be measured in a paid
  development run.
- **Strategic effectiveness is not assessed.** Simulated scenarios are uncalibrated assumptions. Subclaims test the
  option's logic, not its causal effect.
- **The catalogue is limited** to evidence metrics, differences, focal and target gaps, complements and baselines.
  Other quantities need a calculation request.
- **Assumed magnitudes remain judgement.** They are labelled and justified, but not validated.
- **Game-theoretic and robust-decision analysis is not implemented** (premature until response links are scored).

## 4. Recommended milestone 4

**Critic validation** (roadmap item 4), in two parts:
1. After the founder audits the purified benchmark (`registry/critic_audit/AUDIT_PACKAGE_uk_bev_2026-02-23.md`): a
   versioned critic contract with visible limits, deterministic verification of the critic's cited numbers, and
   severity guidance, scored with `score_v2` on the audited items.
2. **One separately approved, small development run** (development dates only; about $0.02) of `loop.v2` +
   `strategy.v3` + the new critic. It measures live contract compliance and actual tokens.

Then freeze both contracts and use the sealed holdout. The baseline-aware generation experiment
([FUTURE_EXPERIMENT_BASELINE_AWARE_GENERATION.md](FUTURE_EXPERIMENT_BASELINE_AWARE_GENERATION.md)) follows as its own
versioned experiment.
