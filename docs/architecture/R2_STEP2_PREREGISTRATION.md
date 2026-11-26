# R2 Step 2: implementation and pre-registered live validation plan

Status:
- **Implemented and tested offline (2026-11-26). Not run.**
- **Each live run needs the founder's approval** and runs only through `scripts/r2_step2_run.sh 2a|2b`.
- **Criteria hash `223362694f7d254f…`** (`step2.CRITERIA_HASH`, pinned by `tests/test_r2_step2.py`). It covers the
  criteria, thresholds, arms, runs, the prospective protocol and the retry setting, and must not change after any Step 2
  call. It was revised on 2026-11-26 after the founder's review, before any Step 2 call.

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

| Run | Investigations | Calls | Worst case (no retries; all attempts) | Expected |
|---|---|---|---|---|
| 2a: matched factor run | UK BEV at **2025-08-23** and **2026-02-23** (held out, never sent to any agent; resolved by 2026-11-23) × arms A, B, C | 19 | $0.0946 | about $0.04 |
| 2b: prospective, second market and critic benchmark | UK BEV at the **registration date** (prospective; §3.1) × A, C; UK hybrid-electric (petrol) at 2026-02-23 × A, C; critic benchmark (2 sets) | 15 | $0.0693 | about $0.03 |

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

## 3. Evaluation method and acceptance criteria (exact; computed on Claude output)

**One assessment method for every arm** (`evaluation._common`). It uses only what v1 and v2 both produce:
- implications, mechanisms and cited evidence;
- tests and results.

**Competing pairs are detected from the hypotheses' own predictions, declared or not.** A pair counts only if it
passes every mechanism gate (`loop_v2.assess_pair`):
- both hypotheses explain the same observation;
- their mechanisms are meaningfully distinct (word overlap at most 0.5);
- one is not a pure negation of the other;
- the disputed quantity concerns the explained movement's entities, so the test is interpretable.

Near-duplicate mechanisms are dropped (`duplicate.mechanism`). **Opposing predictions alone never count.**

**Informative test:**
- trend extrapolation did not predict it, OR
- it is on a disputed quantity of a competing pair.

**Validated useful result:** an original hypothesis (library definition, the same for every arm) with no failed test
and at least one passed informative test. It is labelled **predictive support, not causal proof**.

v2-only fields are **contract compliance** (`evaluation.v2`, criteria K*). They are reported apart and never scored
against v1:
- declared rivals;
- information gaps;
- critique dimensions;
- numeric claims.

B6 measures the **combined** effect of the reasoning contract and the evidence presentation (prompts, schemas,
compact evidence, and a critic that sees the evidence). It is not the effect of prompt wording alone.

| Id | Criterion | Run |
|---|---|---|
| A1–A6 | engineering: smoke with a `$defs` schema; all completed; no truncation, refusal or top-level failure; documents validate; matched evidence; spend (smoke, failed-attempt reservations) ≤ cap | each |
| B1 | arm C: evidence fidelity ≥ 0.80 | each |
| B2 | arm C: ≥ 2 original hypotheses (common definition) per investigation | each |
| B3 | arm C: ≥ 1 gated competing pair per investigation | each |
| B4 | arm C: informativeness (common) ≥ 0.40 | each |
| B5 | arm C: testability ≥ 0.60 | each |
| B6 | informativeness (common) of B − A ≥ +0.20 (combined contract and evidence presentation; descriptive) | 2a |
| K1 | compliance, arm C: ≥ 1 information gap distinguishing ≥ 2 hypotheses per investigation | each |
| K2 | compliance, arm C: ≥ 50% of options pass v2 validation | each |
| C1–C4 | critic benchmark: recall ≥ 0.60; precision ≥ 0.60; ≤ 25% of clean controls with a high-severity critique; evidence-reference accuracy ≥ 0.80. Also reported: recall by defect type, precision by dimension, all critiques on clean controls | 2b |
| D1 | no protocol eligible unless every eligibility check passes | each |
| E1 | arm B generator input tokens (API usage) ≤ arm A at the same origin | 2a |
| P1 | every prospective test registered at the real call time, on DfT data retrieved ≤ 7 days earlier, with ≥ 1 `future` window | 2b |
| P2 | no period already published at registration scored as prospective | 2b |

A criterion a run cannot evaluate is `null`, never `false`. With 6 to 10 investigations, every contrast is
descriptive.

**Reported per arm and per role, from API usage:**
- input and output tokens, latency and cache hits;
- incremental and historical (reused) cost;
- cost per validated useful result.

No evidence is removed because it was not cited. Every metric stays in the slice; only redundant encoding was
removed.

### 3.1 Prospective protocol (frozen in the criteria hash)

- **Registration** happens at the real call time (`utc_now`), never backdated. Evidence is as of the registration
  date, and the origin is that date.
- **Publication check:** DfT data must have been retrieved at most 7 days before registration, or the run is refused.
  The current data were retrieved on 2026-11-23, so they are fresh until 2026-11-30.
- **Window classes:** each target period is labelled `already_published`, `unpublished_historical`,
  `partially_elapsed` (a nowcast) or `future`.
- **Future window:** every implication is also registered on the **first two quarters starting after registration**
  (engine horizon extension, identical for every arm, labelled as such). For a registration in late 2026, the
  nominal window is 2026-Q3 (unpublished historical) and 2026-Q4 (nowcast); the future window is 2027-Q1 and 2027-Q2.
- **Resolution:** once, on the first published vintage containing all target periods; later revisions are ignored.
- **No tuning:** nothing is rerun or re-scored after outcomes are seen.

### 3.2 Budget and authorization

- **Live calls are not retried** (`RETRIES = 0`). The worst case therefore includes every attempt: 2a is **$0.0946**
  for 19 calls, within the **hard cumulative cap of $0.10** (smoke and failed-attempt reservations included) and the
  ADR-019 sole-founder limit. A transient failure stops that investigation, recorded, and nothing is re-bought.
- **2b is prepared and frozen but separately authorized.** It needs its own approval, `R2_STEP2B_AUTHORIZED=yes`
  (script) and `--authorized-2b` (CLI).

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
