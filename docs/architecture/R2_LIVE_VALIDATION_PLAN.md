# R2 live intelligence validation: experiment plan (pre-registered)

Status: **prepared and tested offline (2026-11-24); not run**. Running it needs the founder's execution
approval. Code: `src/halibut/investigation/live_validation.py` (runner) and
`src/halibut/investigation/evaluation.py` (metrics).
Related: [R2_IMPLEMENTATION.md](R2_IMPLEMENTATION.md),
[INVESTIGATION_RESULT_CONTRACT.md](INVESTIGATION_RESULT_CONTRACT.md),
[ADR-019](ARCHITECTURE_DECISIONS.md#adr-019).

## 1. Question

On identical evidence, does Claude Haiku 5.5, acting as the Hypothesis Generator, Critic and
Strategist, produce hypotheses that are **original, evidence-faithful, falsifiable, testable and
informative**, and how do those hypotheses fare against later outcomes? The comparison is with:
- the **deterministic reference** workforce (rule-based trend continuation: the reference library);
- the **mock** workforce (a scripted repertoire that exercises the pipeline).

The experiment also measures schema failures, truncations, tokens and cost per useful result.

**What it cannot establish:** two dates give about 10 tests per workforce. No claim of superiority
over the baselines is possible; the experiment establishes feasibility, quality signals and cost.

## 2. Design

| Item | Value |
|---|---|
| Market | `configs/intel/markets/uk_cars_bev_v1.toml` (UK first registrations, battery-electric segment) |
| Investigation dates (as-of) | 2025-11-23 and 2026-05-23 |
| Evaluation date | 2026-11-23 (both two-quarter test windows are published by then) |
| Workforces | `reference` (deterministic), `mock` (scripted), `anthropic` (Claude Haiku 5.5, thinking off, structured outputs) |
| Identical evidence | Each date's panel fingerprint must match across all three workforces (criterion A5) |
| Routing | `configs/llm/routing.toml`: Haiku for all three roles; output caps 12,000 / 3,000 / 5,000 tokens, sized to the schemas' length limits so replies cannot be truncated within schema bounds |
| Live calls | 1 structured-output smoke call, then 3 per date: **7 calls**. At most 13 attempts with one retry per call on transient errors |
| Hard cap | **$0.06** for everything, smoke included. It is enforced by the fail-closed spend guard on one experiment scope, by the granted approval, and by the provider config's second key |

## 3. Cost (dry-run plan from measured prompt sizes)

| Per date | Prompt + schema (chars) | Output cap (tokens) | Worst case |
|---|---|---|---|
| Hypothesis Generator | ~19,450 (measured) | 12,000 | $0.0070 |
| Critic | 46,000 (upper bound) | 3,000 | $0.0038 |
| Strategist | ~46,450 (upper bound) | 5,000 | $0.0048 |

- **Total, both dates plus smoke: $0.0313 worst case.** Retrying every call would reach $0.0625, so
  the guard would refuse any attempt that crosses $0.06 and the run would stop and report.
- Expected actual cost is well below the worst case. It assumes 2 characters per token at the full
  output cap; the first live benchmark spent 57% of its worst case.
- Haiku 5.5 rates: $0.10 / MTok input and $0.50 / MTok output (official pricing, verified 2026-11-23).

## 4. Metrics (computed from the exported documents; `src/halibut/investigation/evaluation.py`, v1)

| Metric | Definition |
|---|---|
| Evidence fidelity | accepted ÷ (accepted + rejected at schema or validation stage); fabricated ids and unsupported numbers are counted by class |
| Originality | accepted hypotheses whose testable implications are not all in the reference library; overlap of implication signatures with the reference and the mock (Jaccard) |
| Falsifiability | refutation criteria **and** ≥ 1 registered test **and** every alternative explanation points at an implication that compiled |
| Testability | registered ÷ all implications; hypotheses with ≥ 1 registered test |
| Informativeness | registered tests that trend extrapolation did *not* already predict |
| Outcomes | test results (passed, failed, pending); hypothesis statuses (survived with strength, refuted, inconclusive, untested); official forecast MAE and coverage |
| Useful validated result | an *original* hypothesis that survived all its tests, including ≥ 1 informative test |
| Operations | calls, truncations, refusals, top-level schema failures, input and output tokens, latency, cost, cost per useful result |

## 5. Success criteria (pre-registered in `CRITERIA`)

**A. Pipeline integrity: all must pass for "pipeline ready".**
- A1: the structured-output smoke call returns `{"ok": true}`.
- A2: both Claude investigations complete.
- A3: no Claude call is truncated, refused or rejected as a whole.
- A4: every exported document validates against the published schema.
- A5: identical evidence across workforces at each date.
- A6: total spend ≤ $0.06.

**B. Quality bar for Claude: reported pass or fail.**
- B1: evidence fidelity ≥ 0.80.
- B2: ≥ 2 original hypotheses at each date.
- B3: ≥ 80% of accepted hypotheses falsifiable.
- B4: ≥ 60% of implications testable.
- B5: ≥ 40% of registered tests informative.

**C. Outcomes: reported, not pass or fail.** Survival and refutation counts, useful validated
results, forecast accuracy, and cost per useful result, each against the reference and mock.

**Decision.** If A passes, the result informs whether to propose R3; the founder decides. If A fails,
the failures are diagnosed offline, as with the first benchmark, before any further paid call.

## 6. Leakage (historical and training data)

| Risk | Status in this experiment |
|---|---|
| **In-context future data** | Controlled. The slice holds only data available at the date (as-of retrieval). Tests are frozen at the date. The strategist never sees outcomes (tested) |
| **Revised data** | Not controlled. Inputs are the latest published vintage, not what was known at each date (R4) |
| **Estimated publication dates** | DfT history uses an estimated lag (quarter end + 121 days); only 2026-Q2 has a recorded publication date (2026-09-23). The 2026-05-23 window mixes estimated and recorded dates |
| **Training data, 2025-11-23** | **Potentially contaminated.** Its test window (2025-Q3, 2025-Q4) was published 2026-01-28 and 2026-04-30 (estimated), before Haiku 5.5's documented training cutoff (June 2026; models overview, verified 2026-11-24). The model may know these outcomes |
| **Training data, 2026-05-23** | **Outcome data after the cutoff.** 2026-Q1 was published 2026-07-29 (estimated) and 2026-Q2 on 2026-09-23 (recorded), both after June 2026. But both quarters *ended* before or at the cutoff, so news and commentary about them may be in the training data |
| **No cleaner date exists** | 2026-Q2 is the latest published quarter, so no later date has a resolvable window |

Interpretation rule: a Claude hypothesis that passes a test on a pre-cutoff window may reflect
remembered outcomes rather than reasoning. Such results are labelled *potentially contaminated* in the
report and must not support any promotion.

## 7. Authorization (single founder)

Halibut has one human operator, so the usual "requester ≠ approver" rule cannot be met. The
**sole-operator policy** (`configs/governance/approvals.toml`, ADR-019) allows the configured
operator to self-approve a `paid_run` only under all of these conditions:
- the amount is at most $0.10;
- a written attestation of at least 40 characters is given;
- the decision records `self_approved_under_policy` and the policy version, in the append-only
  decision record and an `approval.granted` event.

It never applies to experiments, external actions, promotions or new sources, and never to agents or
the engine.

The provider config remains a second, independent key (`approved = true` only for the duration of
the run).

## 8. Outputs

Everything goes to `results/r2_validation/<experiment id>/`:
- `investigations/` holds six result documents (reference, mock and anthropic × two dates), in the
  published dashboard format;
- `results/r2_validation/<id>/comparison.json` and `results/r2_validation/<id>/comparison.md` hold the metrics, criteria, leakage and cost;
- `results/r2_validation/<id>/manifest.json` lists the files.

The run also emits `validation.started`, `validation.completed` and `approval.consumed` events, and
spend is visible in `halibut agents spend`.
