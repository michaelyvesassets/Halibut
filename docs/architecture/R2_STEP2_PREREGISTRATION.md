# R2 Step 2: implementation and pre-registered live validation plan

Status:
- **Implemented and tested offline (2026-11-26). Not run.**
- **Each live run needs the founder's approval** and runs only through `scripts/r2_step2_run.sh 2a|2b`.
- **Criteria hash `22bae60509f6a2f9…`** (`step2.CRITERIA_HASH`, pinned by `tests/test_r2_step2.py`). It must not
  change after any Step 2 call.

Related:
- [R2_STEP2_PROPOSAL.md](R2_STEP2_PROPOSAL.md);
- [ADR-022](ARCHITECTURE_DECISIONS.md#adr-022);
- [INVESTIGATION_RESULT_CONTRACT.md](INVESTIGATION_RESULT_CONTRACT.md).

Step 1 (`r2r-878aa70df76d`) closed the engineering milestone. It stays the **historical engineering benchmark**, and
its two dates (2025-11-23, 2026-05-23) are the development set: they are never used for comparisons.

## 1. What was implemented (offline)

The new reasoning contract is `loop.v2`, selected per investigation (`--contract loop.v2`). `loop.v1` stays the
default and is byte-identical. On real data, after all Step 2 changes:
- the Step 1 identity preflight is IDENTICAL;
- the paid replies are still reusable;
- the reconstructed v1 documents have unchanged content hashes.

| Requirement | Implementation |
|---|---|
| 1. Mechanism discovery | Each hypothesis names the movement it `explains` and its `rivals`. A test is **discriminating** when a rival hypothesis predicts the opposite direction for the same quantity; this is checked from the rivals' own implications. **Originality does not require contradicting the trend**: a hypothesis is original if its implications are not all in the reference library, or if one of them discriminates it from a rival. Nothing rewards unusual predictions as such |
| 2. Information gaps and measurements | `information_gaps` records: missing measurement, why it matters, which hypotheses it distinguishes, potential sources, expected research value. They are stored as open questions in learning memory, with `autonomous_research: disabled`. Extended measurements: `non_segment_share`, `units_growth` (entity or market), and pairwise `above_zero`/`below_zero` for share gaps ("A exceeds B") |
| 3. Critic intelligence | Structured critiques by dimension: factual_contradiction, unsupported_detail, numerical_accuracy, causal_identification, alternative_explanation, test_validity. Factual and numerical critiques must cite the metric. The critic now **sees the evidence**. A blinded benchmark of planted defects plus clean controls is scored structurally (target, dimension and cited metric; no keywords), with penalties for false alarms and excess criticism |
| 4. Strategic intelligence | The v2 strategist contract is active: `entity_b` for pairwise targets, objective/success agreement, and `numeric_claims` (observed, calculated or assumed, with unit and precision). **Every number in every free-text field** (title, basis, assumptions, risks, cost basis) must be declared, and observed and calculated values must reproduce exactly. Experiment eligibility safeguards are unchanged, and pairwise options are now simulable against the gap baseline |
| 5. Cost efficiency | The metric-id enum is defined once (`$defs`); unused definitions are dropped. Metric rows are compact `[id, value]`, and the grammar prose is dropped. Every metric id and value is kept (tested) |
| 6. Experimental design | §2 |
| 7. Multi-model readiness | §5 |

Context size on real UK data (2026-02-23; characters of prompt plus schema; **tokens are measured from API usage in
the live run**, criterion E1):

| Role | v1 (arm A) | v2 base (arm B) | v2 extended (arm C) |
|---|---|---|---|
| Generator | 19,437 | 15,934 (−18%) | 20,007 (84 metrics instead of 50) |
| Critic | 5,499 | about 12,500 (now includes the evidence) | about 16,800 |
| Strategist | 19,546 | 17,932 | 22,052 |

## 2. Experimental design (pre-registered)

**Arms**, all with the same model (routing default, Claude Haiku), identical evidence (same panel snapshot) and the
same origins:

| Arm | Contract | Measurements | Isolates |
|---|---|---|---|
| A_v1_base | loop.v1 | base | control (the Step 1 contract) |
| B_v2_base | loop.v2 | base | B − A: prompts and reasoning contract |
| C_v2_extended | loop.v2 | extended | C − B: expanded measurements |

Model changes are a **later, separate factor**: the same arms rerun with other models (§5).

**Runs.** Each is separately approved and single-use; each worst case is within the ADR-019 limit.

| Run | Investigations | Calls | Worst case (retries) | Expected |
|---|---|---|---|---|
| 2a: matched factor run | UK BEV at **2025-08-23** and **2026-02-23** (held out, never sent to any agent; resolved by 2026-11-23) × arms A, B, C | 19 | $0.0946 ($0.189) | about $0.04 |
| 2b: prospective, second market and critic benchmark | UK BEV at **2026-11-23** (prospective) × A, C; UK hybrid-electric (petrol) at 2026-02-23 × A, C; critic benchmark (2 sets) | 15 | $0.0693 ($0.139) | about $0.03 |

Every attempt, retries included, is capped fail-closed at the run's cap ($0.10). The total expected cost is about
$0.07.

**Leakage controls:**
- the retrospective origins' outcome windows predate the model's training cutoff (June 2026) and are labelled as
  possibly seen in training;
- the **prospective origin's tests (2026-Q3/Q4) are unpublished**. They are registered with their spec hashes and
  resolved later (`prospective_pending_tests`);
- the second market comes from the same source (DfT VEH0160) with a different segment. It is **not** an independent
  data source. Norway (SSB) data are by fuel only, not brand, so a Norwegian competitor panel needs new data.

**Reported separately, never merged into one score:**
- informative confirmations;
- informative refutations;
- validated useful discoveries;
- cost per validated useful discovery;
- prospective pending tests;
- critic benchmark scores.

## 3. Acceptance criteria (exact; computed on Claude output)

| Id | Criterion | Run |
|---|---|---|
| A1 | the smoke call with a `$defs`/`$ref` schema returns `{"ok": true}` | each |
| A2 | every live investigation is `completed` (no internal error) | each |
| A3 | no live call truncated, refused or rejected as a whole | each |
| A4 | every document validates against `investigation_result.v1` | each |
| A5 | all arms at the same market and origin saw the same panel fingerprint | each |
| A6 | live spend (smoke included) ≤ the approved cap | each |
| B1 | arm C: accepted / (accepted + rejected) hypotheses ≥ 0.80 | each |
| B2 | arm C: ≥ 2 original hypotheses in every investigation | each |
| B3 | arm C: ≥ 1 rival pair in every investigation | each |
| B4 | arm C: ≥ 25% of registered tests discriminating | each |
| B5 | arm C: ≥ 40% of registered tests informative (v2 definition) | each |
| B6 | arm C: ≥ 60% of implications compile into registered tests | each |
| B7 | arm C: every investigation has ≥ 1 accepted information gap distinguishing ≥ 2 hypotheses | each |
| B8 | informativeness (v2) of arm B − arm A ≥ +0.20 (pooled over matched origins; descriptive) | 2a |
| C1 | critic benchmark recall ≥ 0.60 (correct target, dimension and evidence) | 2b |
| C2 | critic precision ≥ 0.60 (medium/high critiques that detect a planted defect) | 2b |
| C3 | ≤ 25% of clean controls receive a high-severity critique | 2b |
| D1 | arm C: ≥ 50% of proposed options pass v2 validation | each |
| D2 | no drafted protocol is eligible unless every eligibility check passes | each |
| E1 | arm B generator input tokens (API usage) ≤ arm A at the same origin | 2a |

A criterion a run cannot evaluate is `null` (for example C* in 2a), never `false`. The B criteria test whether the
contract elicits the intended reasoning; they are not claims of predictive superiority. With 6 to 10 investigations,
every contrast is descriptive, not a significance test.

**Expected improvements (hypotheses, not results):**
- discriminating tests and informative refutations should appear (v1 Haiku produced 0 informative predictions);
- with the evidence in view, the critic should cite metrics for contradictions such as Tesla's 100% segment mix (v1
  reached only a partial detection);
- the strategist should stop passing undeclared or unsupported numbers (v1: O1's 7.27% and O4's 0.12).

**Remaining limitations:**
- national quarterly aggregates give few observations, and two-quarter windows are noisy;
- discrimination is checked on direction only, not magnitude;
- the critic benchmark has 5 defect types and 4 clean controls, built by templates; it measures detection of known
  defect types, not open-ended insight;
- the second market shares the source and most brands with the first;
- information gaps are recorded but not yet researched;
- the prospective results arrive only when DfT publishes 2026-Q3/Q4.

## 4. Dashboard fields (Workspace and Agents pages, eventually)

| Field | Where | Show |
|---|---|---|
| `stages.information_gaps[]` (type `information_gap`) | Workspace | missing measurement, value, hypotheses it distinguishes, research status |
| hypothesis `data.explains`, `data.rivals`, `data.discriminating_implications` | Workspace | rival mechanisms side by side |
| test `data.discriminates_from` | Workspace | "rivals disagree here" badge |
| critique `data.dimension`, `data.evidence` | Workspace | dimension chip; linked metric |
| option `data.intended_effect.entity_b`, `data.numeric_claims`, `data.simulation` | Workspace | each number with kind (observed, calculated, assumed) |
| protocol `data.eligibility` | Workspace | exploratory or eligible, with reasons |
| `provenance.prompt_version` (`loop.v2:base` or `loop.v2:extended`), `provenance.agents` (`recorded` = saved replies) | both | contract badge |
| evaluation `v2` block, critic benchmark scores, per-role tokens and cost | Agents | per-role and per-model performance |

## 5. Multi-model readiness

- Routing is per role (`configs/llm/routing.toml`), and the contract and the schemas are model-agnostic.
- The planned Haiku–Sonnet 80/20 and 70/30 mixes and the independent Qwen benchmark rerun the same arms and criteria
  with a different routing. The router fingerprint is part of every input id, so results never mix.
- Allocation is decided from **measured per-role performance** (generator informativeness and discrimination, critic
  benchmark recall/precision, strategist numeric integrity, cost per validated useful discovery), not token quotas.
- No model benchmark or workforce expansion is implemented or run here.
