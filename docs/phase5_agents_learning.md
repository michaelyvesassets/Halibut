# Phase 5: closed learning loop, agent layer, cost accounting (M29–M33)

> **Labels.** Observed facts are cited from sources. Derived metrics have
> recomputable definitions. Hypotheses and candidate explanations are
> proposed, not proven. **Mock agent output is not intelligence**: it
> exercises the pipeline, validators and accounting. Real LLM quality is
> unmeasured until Qwen is approved.

## 1. Component boundaries (modules within one application)

| Component | Modules | Reads | Writes |
|---|---|---|---|
| Intelligence agents | `halibut.agents` (Research, Hypothesis, Critic; LLM provider; accounting), `halibut.intel.agents` (deterministic reference agents) | as-of evidence bundle, lessons | proposals only (via the engine) |
| World model | `halibut.data.store`, entities and relationships, `intel.retrieval` | — | providers only |
| Simulation engine | `halibut.engine` and related modules (unchanged) | configs | results |
| Strategy system | `intel.agents` (modes, levers), `intel.forecast` (prediction policies), scenario drafter | signals, track record | hypotheses, predictions, drafts (proposed) |
| Learning / evaluation | `halibut.learning` (memory, lineage, `predictions` scorer, `evaluate` rules, promotions), `intel.replay` | scores, outcomes | outcomes, lessons, evaluations; promotions **only with human approval** |
| Validation / safety | `halibut.validation`, `intel.audit` | everything an agent emits | accept or reject, with reasons |
| Shared evidence and learning memory | SQLite (migrations 1–4) | — | — |

## 2. Automatic prediction evaluation (M29)

**Prediction spec v2.** Each prediction has:
- a recomputable quantity: a share, or a difference of two shares, over
  explicit target quarters;
- a point, an 80% interval, an SD, and a threshold with a direction and
  probability;
- the method, model version, as-of date and evidence IDs.

The **engine** builds the numbers from metrics. Agents choose only what to
claim and what to cite.

**`score_due(T)`** scores every open prediction whose target quarters are
published by date T: no hindsight, and nothing scored twice. Each score
records:
- error, absolute error and 80% interval coverage;
- interval score (Gneiting–Raftery, α = 0.2);
- whether the direction held, and the Brier score.

Scores go to `prediction_scores` and to an `outcome` knowledge item whose
`available_at` is the outcome data's publication date. A refuted hypothesis
becomes `rejected`; a supported one becomes `tested`, never `validated`.

**Failure explanations** are deterministic *candidates*: volatility
underestimated, denominator shift, seasonality not modelled, trend reversal.
They are stored as proposed hypotheses tagged `explanation`, with the note
"candidate explanation, not a demonstrated cause".

**Lessons respect as-of dates.** `retrieve(as_of=…)` only returns knowledge
whose supporting data was available by that date.

## 3. Historical replay (M30)

`halibut intel replay MARKET --agents reference|mock [--faults]`.

There are 10 half-yearly forecast dates, 2021-11-23 to 2026-05-23. At each
date the replay:
1. scores every earlier prediction whose outcome had been published by then;
2. analyses the market as of that date.

The analysis runs evidence audit → research (agents) → hypotheses → critique
→ validation → v2 predictions, plus shadow policy forecasts for every
competitor (level, momentum and last-quarter, with naive or empirical
intervals). Finally, three policy comparisons go through the standing rule
(`paired-dev-holdout-v1`: 6 development dates, 4 held-out) and become lessons.

**Limitation.** DfT history comes from one retrieval. Availability before
2026 is estimated as quarter end + 120 days, and earlier revisions are
invisible.

## 4. Demonstration results (UK cars, BYD vs Volkswagen)

### Learning cycle

Evidence → research → hypothesis → critique → prediction → outcome →
lesson → subsequent investigation.

| | Reference (deterministic) | Mock LLM, faults on | Mock LLM, repeated |
|---|---|---|---|
| Hypotheses proposed / rejected by validation | 61 / 0 | 70 / **19** (fabricated metric ×10, unsupported number ×9) | same |
| Predictions scored / supported / refuted | 61 / 41 / **20** | 51 / 30 / 21 | same |
| Direction held | 82% | 78% | 78% |
| MAE / 80% coverage | 1.23 pp / 79% | 1.45 pp / 71% | same |
| LLM calls / estimated tokens (in + out) | 0 | 30 / 70.7k + 8.2k | 30 / same |
| Cache hit rate | – | 40% | **67%** |
| Duplicate tool calls caught | – | 10 | 10 |
| Runtime | 44 s | 50 s | 56 s |
| Cost (mock) | $0 | $0 | $0 |

The token counts are estimates (characters ÷ 4), because mock output is
terse.

**Track record by hypothesis mode** (reference plus one mock replay,
repeated replays de-duplicated):

| Mode | n | Direction held | 80% coverage |
|---|---|---|---|
| continuation | 53 | 92% | 77% |
| enforcement | 38 | 89% | 71% |
| **annihilation (displacement)** | **20** | **30%** | 80% |

The "focal gains while target loses in the segment" pattern did **not**
persist in 14 of 20 cases.

**Lessons (all rejected under the rule):**

| Candidate vs baseline | Baseline | Candidate | Metric |
|---|---|---|---|
| momentum vs level persistence | 0.61 pp | 0.72 pp | MAE |
| last quarter vs level persistence | 0.61 pp | 0.71 pp | MAE |
| empirical vs naive 80% intervals | 2.94 | 2.95 | interval score |

Level persistence stays the active method. No promotion was proposed,
because nothing was eligible.

**Subsequent investigation** (as of 2026-11-23, offline, 3 s). It retrieved
11 distinct lessons, outcomes and questions. Repeats from same-data replays
are counted once and labelled as not independent; pre-fix lessons are shown
as superseded. Effects on the run:
- Confidence comes from the mode track record (`confidence_basis: empirical`):
  continuation and enforcement are *medium*, at 89–92%, while the
  annihilation hypothesis stays *low*, at 30%, with n shown.
- Predictions use level persistence, the policy every comparison failed to
  beat.

### Evidence audit (M32)

All checks pass:
- comparison windows are L4 = 2025 Q3 – 2026 Q2 and P4 = 2024 Q3 – 2025 Q2,
  adjacent 4-quarter windows, not calendar years;
- 2024 and 2026 are each only half covered, and no calendar-year totals are
  computed;
- denominators are identical across brands in each window;
- the universe is DfT first registrations, which **include imported used
  vehicles**;
- no competitor quarter is missing.

**Brand displacement vs general share change** (exact shift-share
decomposition):

| | Share change | Segment-mix | Within-segment | Interaction | Units P4 → L4 |
|---|---|---|---|---|---|
| BYD | +1.95 pp | +0.23 | **+1.43** | +0.29 | 25,282 → 69,449 |
| Volkswagen | −0.61 pp | **−0.34** | −0.16 | −0.11 | 184,113 → 183,726 |

- Volkswagen's decline is mostly segment mix.
- Within battery-electric, Volkswagen's units fell 6.4% while the segment
  grew 21%.
- Within-segment losers are **BMW (−2.02 pp), Tesla (−1.91), Volkswagen
  (−1.78)**; gainers are **BYD (+3.41), Ford (+2.65), Kia (+0.90)**.
- Switching is not observed. BYD's gain is not specifically Volkswagen's
  loss, and the reports now say so.

## 5. Cost accounting and efficiency (M31)

`agent_calls` records the provider, model, tokens (estimated or actual),
estimated and actual cost, latency, cache hits, prompt hash, tool calls,
duplicate tool calls and outcome for every call. Replay summaries add:
- useful validated results (hypotheses that pass validation *and* are
  supported by outcomes);
- cost per useful validated result.

**Efficiency controls:**
- prompt-level cache in the store;
- Research runs only when there are gaps, and network tools are only
  *proposed*;
- one Hypothesis call per run;
- one batched Critic call, only over validated hypotheses;
- duplicate tool calls are skipped and counted;
- validated or rejected lessons are reused instead of repeating experiments.

**Projected Qwen cost for one 10-date replay (unverified).** This assumes the
same token volume (≈71k in, 8k out). Real prompts and outputs will be larger,
so treat these as lower bounds. The prices are third-party figures that
conflict with each other.

| Price tier | Input / output price | Estimated cost | Per useful result (30) |
|---|---|---|---|
| plus-tier | $0.4 / $1.6 per M tokens | ≈ $0.04 | ≈ $0.0014 |
| max-tier | $2 / $6 per M tokens | ≈ $0.19 | ≈ $0.006 |

## 6. Reference vs agent workflow: what can and cannot be concluded

- **The deterministic reference workflow is currently the stronger
  baseline**: more supported hypotheses (41 against 30), a better hit rate,
  better calibration, and no cost.
- The mock workflow shows the safeguards work. 19 of 70 agent hypotheses
  (27%) were rejected for fabricated citations or unsupported numbers before
  entering memory.
- Whether a real LLM adds value is **unknown**. It must beat the reference on
  held-out outcomes per dollar under the same rule before any promotion.

## 7. Remaining work

- Connect Qwen (approval needed) and run the same replay. The comparison is
  fixed in advance: supported hypotheses, hit rate, interval coverage, MAE,
  cost per useful result, all against the reference.
- Time-stamp status changes, so a lesson's validation date, not only its data
  availability, is replayable.
- Normalise run-specific IDs in Critic prompts, to raise cache hits on
  repeated runs.
- Run multi-market replays so lessons can generalise across markets.

## 8. Qwen benchmark readiness (no paid calls made)

**Verified 2026-11-23 against official Alibaba Cloud Model Studio documentation:**

| Item | Verified value | Source |
|---|---|---|
| Model | `qwen3.7-plus` (snapshot `qwen3.7-plus-2026-05-26`), Singapore / International | [model page](https://www.alibabacloud.com/help/en/model-studio/qwen3-7-plus) |
| Price (original, ≤ 256K input) | $0.40 in / $1.60 out per 1M tokens. Promotions may apply in the console; caps use the original price | model page |
| Endpoint | `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions` (workspace-specific, recommended over legacy `dashscope-intl`); API keys are **region-bound** | [OpenAI compatibility](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope) |
| Thinking | **On by default** for qwen3.7-plus and billed as output, so Halibut sends `enable_thinking: false` and flags any reasoning tokens | [deep thinking](https://www.alibabacloud.com/help/en/model-studio/deep-thinking) |
| JSON mode | `response_format: {"type": "json_object"}`; the prompt must contain "JSON" (enforced) | [structured output](https://www.alibabacloud.com/help/en/model-studio/json-mode) |

**Fail-closed spending** (`halibut.agents.budget.SpendGuard`, `llm_spend_ledger`):
- every attempt, including retries, reserves its worst case: characters ÷ 3 × 1.1 + 64 input tokens plus the full `max_output_tokens`, priced at the original list price;
- the check and insert happen in one `BEGIN IMMEDIATE` transaction, against the $5 total across all processes and the $1 per replay, counting outstanding reservations;
- failed attempts stay charged at their reservation, and missing usage is charged at the worst case;
- missing prices or caps cause a refusal;
- `halibut agents spend` shows the ledger.

**Planned first benchmark** (`halibut intel benchmark … --dry-run`, prompts measured on a throwaway copy):

| Calls | Prompt size | Worst case | Worst case, every call retried | Per-replay cap |
|---|---|---|---|---|
| 30 (10 research, 10 hypothesis, 10 critic) | ≈ 309k characters | **$0.096** | $0.19 | $1 |

**Status timestamping.** `knowledge_status_history` records when each status change became knowable in data time. `retrieve(as_of=…)` returns the status as of that date, so a replay cannot see a lesson validated or rejected later. Rows that existed before migration 5 are back-filled from their current status and marked `backfilled_current_status`.

**Approximations stay labelled.** Outcomes record `outcome_publication_date_basis`, which is "approximate (estimated publication lag)" for DfT history found in the first retrieval. Benchmark reports carry the same note.

**Secrets:**
- the key and workspace ID are read only from `DASHSCOPE_API_KEY` and `DASHSCOPE_WORKSPACE_ID`;
- the key goes only into the outgoing `Authorization` header;
- configs that contain a key are rejected;
- tests assert the key never appears in the store, ledger or error messages;
- `.env` files are git-ignored.
