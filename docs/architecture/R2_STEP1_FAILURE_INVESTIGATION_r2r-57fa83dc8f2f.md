# R2 Step 1 failure investigation (experiment `r2r-57fa83dc8f2f`)

Status: **offline investigation complete (2026-11-24); fixes implemented and tested locally; not pushed; awaiting
founder review.**
- No paid calls were made.
- No Step 2, R3, Sonnet or Qwen work.
- No lesson promotions and no real-world interventions.

The original records are unchanged: `results/r2_validation/step1-20261124T025850Z/` and the store.

Related:
- [R2_STEP1_REVALIDATION_PLAN.md](R2_STEP1_REVALIDATION_PLAN.md);
- [ADR-021](ARCHITECTURE_DECISIONS.md#adr-021);
- [R2_POSTMORTEM_r2v-b16fc9281536.md](R2_POSTMORTEM_r2v-b16fc9281536.md).

## 1. Summary

The run crashed with `IndexError: list index out of range` in `models/registry.py` (`execute`, `ser[-1][0]`). The
crash happened while the engine was processing strategist option **O3** for 2025-11-23.

**Root cause: an engineering defect in Halibut, not an agent error.**
1. The live strategist proposed O3 with `intended_effect = {template: segment_share_gap, entity: volkswagen,
   direction: decrease}`. The reply was **schema-valid**: the loop.v1 strategist schema gives an intended effect only
   one entity, so it has no field for the second entity of a gap. Its success criterion did name it (volkswagen–mg).
2. `_option` built the target `segment_share_gap(volkswagen, None)`. That series has **0 quarters**; volkswagen–mg
   has 44.
3. The registry correctly rejected every model family (`target series has 0 quarters`). `official_baseline` then fell
   back to an **unguarded** `execute`. That call computed the mean of an empty slice (NumPy's "Mean of empty slice"
   warning, NaN) and indexed the empty series.
4. There was no isolation per option or per investigation. The exception aborted the runner before it wrote any
   document, report or execution record. The 2026-05-23 investigation never ran.

Classification against the hypotheses the founder listed:

| Candidate | Verdict | Evidence |
|---|---|---|
| Unsupported target | **Yes (primary)** | A gap needs two entities; the strategist contract allows one |
| Missing observations | Consequence | The empty series follows from the missing second entity |
| Wrong metric mapping | Contributing | `_option` mapped the effect to a target with `entity_b=None` without checking the template |
| Invalid model specification | No | `check_spec` correctly rejected it; the fallback ignored the rejection |
| Registry defect | **Yes (contributing)** | `execute` had no guard against empty, short or non-finite series |
| Strategist error | **No** | The output met its contract. The contract was incomplete |

All of this was reproduced on the real data, offline. The recorded reply reproduces the exact production traceback on
the pre-fix code (§6).

## 2. Recovered live intelligence and expenditure

### 2.1 Calls in `r2r-57fa83dc8f2f`

| Call | Request id | Tokens in / out | Cost | Latency | Stop reason |
|---|---|---|---|---|---|
| smoke | req_011ChM1DpHKu6VfkNT4W722v | – | $0.0000269 | – | end_turn |
| generator 2025-11-23 | (cache hit, recorded reply of r2v-b16fc9281536) | 10,026 / 3,596 | $0 | – | end_turn |
| critic 2025-11-23 | req_011ChM1EKtZ43v9Xjk9CUzww | 2,513 / 1,589 | $0.0010458 | 10.9 s | end_turn |
| strategist 2025-11-23 | req_011ChM1FALcUmUUeDgpiG1Fn | 10,046 / 2,537 | $0.0022731 | 10.8 s | end_turn |
| 2026-05-23 (all roles) | never called (crash) | – | $0 | – | – |

**Expenditure:**

| Scope | Cost |
|---|---|
| Step 1 | **$0.0033458** (3 ledger entries, all settled) |
| Original R2 live validation (`r2v-b16fc9281536`) | $0.0053219 |
| **R2 total to date** | **$0.0086677** |
| All-time ledger | $0.0294266 over 30 entries |

The approval `ap:48d414eddb780140` was bound to the run and then revoked by the script's trap. The tracked config is
unchanged.

### 2.2 Where the records are preserved

- `tests/fixtures/haiku_r2r_57fa83dc8f2f_critic_strategist_replies.json`: both replies verbatim, with cache key,
  prompt fingerprint, request id, tokens, cost and stop reason. This is the durable copy in git.
- `results/r2_validation/step1-20261124T025850Z/recovered/`:
  - `critic_strategist_replies.json`;
  - `execution_records_recovered.json`: approval and decisions, ledger, `agent_calls`, the 32 events in the window,
    and the event-chain check.
  - The runner never wrote these files itself because it crashed first.
- `results/r2_validation/step1-20261124T025850Z/offline_reconstruction/`: the reconstructed documents and summary
  (§7).

## 3. Independent assessment of the critic

The critic produced 12 critiques, 3 per hypothesis, using only the accepted hypotheses and evidence. Overall: **good
methodological criticism, weak on checking claims against the evidence.**

**Strengths.**
- Every critique targets a real weakness.
- It found the postmortem's main finding without being told. Its H1 critique says the test "cannot fail if BEV volume
  rises with total volume, so a confirming result does not discriminate H1 from a BEV-growth explanation". That is
  the trend-continuation (uninformative test) problem.
- It flagged:
  - H2's unfalsifiability;
  - noise at a single-quarter threshold (power);
  - H3's zero-sum assumption and its under-specified conjunctive refutation;
  - unmeasured confounders (model launches, fleet timing, reclassification of fuel categories).
- Severities are sensible.

**Weaknesses.** It questions assumptions it could have checked against the evidence it was given.

### 3.1 Detection of the postmortem's known issues

Human judgement, replacing the keyword screen. The keyword screen gave a false negative on Tesla and a false positive
on the VW–MG gap.

| Issue (2025-11-23) | Detected? | Evidence |
|---|---|---|
| Tesla "non-BEV volume" contradicts Tesla's 100% BEV mix | **Partial** | H4 [high]: "the assumption that Tesla's non-BEV registrations are non-trivial is unverified and, if false, … the mechanism then collapses". It found the weak point but did not use `metric:segment_mix:L4:tesla = 100%`, which refutes it |
| Ford "product-led step-up" lies outside the evidence | **Partial** | H2 [high] offers fleet-deal timing as a rival explanation. It does not say that model-level data are absent |
| VW–MG gap compared with MG's level (mis-encoded test) | **No** | H3 critiques address substitution and the conjunction, not the mismatch of reference quantity (the compiler now rejects such tests) |
| 2026-05-23 issues (petrol, launches, MG–Kia gap) | n/a | The 2026 critic was never called |

**Implication:** the `critic_detection` keyword screen is not a reliable measure. It stays labelled "keyword screen
only: a human must confirm", and the human reading above is the one to use.

## 4. Independent assessment of the strategist

The strategist proposed 4 options, all with unknown costs and a stated basis, assumed magnitudes labelled ASSUMED,
measurable success criteria and plausible competitor responses.

| Option | Assessment |
|---|---|
| O1 price/financing push, segment_share BYD +0.5–1.5 pp | **Rejected (correctly)** by number validation: "baseline point 7.27%" matches no evidence or baseline value (BYD's segment share is 3.60%; the VW baseline is 7.76%). A fabricated number |
| O2 range/availability, segment_mix BYD +2–6 pp | Coherent with H1 (BYD's BEV mix fell from 100% to 62.63%). Objective alignment is doubtful, because raising BEV *mix* is not the same as gaining share. The baseline is unvalidated persistence (11 quarters), with an 80% range of 38.8–94.5%, so the effect is undetectable. **Selected for the draft protocol** in the reconstruction |
| O3 service/charging bundle on the VW–MG gap | **Internally contradictory:** the intended effect is to *decrease* the VW–MG gap, but its success criterion is the gap *increasing* versus L4. The actor is unclear (focal is BYD). It admits "H3 is not testable". Kept, not simulated (`option.effect_target_unsupported`) |
| O4 disclosed advertising, share BYD +0.1–0.5 pp | Its numbers 1.24% and 0.20% are correct (BYD total share L4 and P4). "Baseline point 0.12 share" has no source. It passed only because the absolute number tolerance (0.06 pp) loosely matches 0.197%. Simulated: 3.01–4.37% |

**Overall:** the strategist is structurally sound and lawful, but it is not yet quantitatively careful:
- 1 fabricated number was caught;
- 1 unsourced number passed;
- 1 option contradicts its own success criterion.

Lever originality is moderate, mostly standard levers linked to the hypotheses.

## 5. System-wide resilience review (every AI → deterministic boundary)

| Boundary | Before | Now |
|---|---|---|
| Empty target series | IndexError (crash) | `ModelUnavailable(model.target_unavailable)` |
| Insufficient history (< 8 quarters to execute) | NaN pooled windows | `model.insufficient_history` |
| Unsupported metric or spec (family cannot forecast) | ValueError deep in fitting | `model.unknown_family`, checked first |
| NaN or infinity in observations, assumptions or outputs | propagated silently | `numeric.nonfinite` |
| NaN or Infinity in agent JSON (accepted by `json.loads`) | passed the "number" check | schema violation, class `numeric.nonfinite` |
| No baseline for an option | unguarded fallback, crash | `official_baseline` → status `unavailable`, point `None` |
| Gap effect without a second entity, or an effect on "market" | crash or meaningless target | `option.effect_target_unsupported`; the option is kept, not simulated |
| Simulation inputs or outputs | unchecked | finite-checked; `min_effect_pp([])` raises instead of `min()` on empty |
| Protocol selection | could pick an unsimulated option | only simulated options |
| Failure taxonomy for options | every strategist schema rejection was mislabelled `format.evidence_not_id_list` (a pre-existing defect found here) | classified correctly |
| Unexpected exception inside the loop | aborted the process; no records | stored as `stopped` / `internal_error: …`, event, re-raised (`InvestigationInternalError`) |
| Runner (validation / revalidation) | crash lost every output | per-origin isolation; stops further *paid* origins; documents and records written before reporting; `pipeline_ready=false`; CLI exit 6 |

Boundaries already safe:
- invalid comparisons and periods, which the compiler marks not testable with a reason;
- unregistered levers and prohibited tactics;
- magnitude order and size;
- cost consistency;
- hindsight.

**Known weaknesses left unchanged.** Each needs a founder decision, because each changes what agent output is
accepted:
1. Add `entity_b` to the strategist's intended effect. This changes the strategist schema, so it belongs in a new
   prompt and schema version; doing it now would break identity with the recorded replies and the frozen criteria.
2. Reject options whose intended-effect direction contradicts their own success criterion (O3).
3. Use a relative, unit-aware number tolerance in `check_numbers` (the O4 "0.12").
4. Replace the keyword-based critic detection with a structured critic output that cites the contradicting metric.

## 6. Tests

**352 passed** (296 before, plus 56 new). Ruff is not installed in this environment.

`tests/test_r2_resilience.py` (53 tests):
- **Regression from the real replies:**
  - the fixture matches the live record;
  - the strategist reply is valid under its own schema;
  - the recorded critic and strategist replies run through the full pipeline. O3 is isolated, the other options are
    simulated and a protocol is drafted.
- **Registry:** empty, short and NaN series; unknown family; non-finite simulation inputs and outputs.
- **Adversarial strategist items:** NaN, infinity, a "market" effect and a gap effect. Each is isolated, and valid
  work survives.
- **Engine error visibility:** a planted `ZeroDivisionError` gives a stored `stopped` document, the `internal.error`
  class, the event, and the re-raise.
- **Property tests (seeded, reproducible):**
  - 24 runs where every role answers with a random instance of *its own* schema;
  - 16 runs where options are repaired only enough to pass validation, so any intended effect reaches execution.

`tests/test_r2_step1_revalidation.py` (3 new):
- reuse sends only the smoke test and gives identical content hashes;
- reuse never covers a changed request;
- an internal error is visible, stops spending and keeps the records.

**Shown to catch the defect.** Against the pre-fix code (temporarily stashed):
- the recorded-reply pipeline test reproduces the production traceback (`registry.py:160 IndexError`);
- the validation-aware property test fails in 9 of 16 seeds with the same IndexError.

### 6.1 Why the 296 tests missed it

1. The mock and reference strategists only ever propose effects on `segment_share` or `share` for the focal entity, so
   no test reached a gap effect or a target with no observations.
2. Tests checked schema validity and the validation paths, but never the full space of schema-valid outputs. The
   schema allowed a gap with one entity, and nothing exercised it.
3. `official_baseline`'s fallback was always reached with a valid target, so its missing guard was never executed on
   bad input.
4. There was no test of process-level isolation: nothing checked that an exception mid-investigation leaves records.
5. Purely random fuzzing would not have caught it either. Validation rejects nearly all random options, which is good,
   but it hides the execution boundary. Only validation-aware generation exercises it, which is why that test was
   added.

## 7. Offline end-to-end reconstruction (all saved replies, both experiments)

`scripts/r2_offline_reconstruction.py` runs both dates on a throwaway copy of the store, using:
- the r2v generator replies (both dates);
- the r2r critic and strategist replies (2025-11-23).

The store's sha256 was verified unchanged.

| Date | Status | Completion | Content |
|---|---|---|---|
| 2025-11-23 | completed, validates | partial_rejection | 4 hypotheses, 12 critiques, 4 tests (3 results), 3 forecasts, 4 estimates, 3 options (O1 rejected; O3 not simulated), 5 simulations, protocol for O2, 4 lessons. Rejections: explanations array (5 > 4), O1 (unsupported number) |
| 2026-05-23 | completed, validates | partial_rejection | 3 hypotheses, 4 tests (3 results), 3 forecasts, 3 estimates, 3 lessons. The critic and strategist were never paid for, so in this reconstruction those calls are refused and recorded as `call.error` (never invented) |

No internal errors occurred.

The documents label `agents` as `mock` because the reconstruction routes through the offline router. The router
description and the summary say what was actually used.

## 8. The cheapest scientifically valid revalidation

**Reuse.** The 2025-11-23 critic and strategist requests are byte-identical to the paid ones:
- same prompt fingerprint (`9cab9f15752a4471`, `d42ac1d59f148730`);
- same cache key, covering model, settings, system prompt, evidence bundle and output schema.

They do not need to be bought again. The fixes change only what happens *after* the strategist reply. The preflight
verifies this, and the call-time check repeats it.

| Call | Live? | Worst case |
|---|---|---|
| smoke (criterion A1) | live | $0.00003 |
| generator ×2 | cache (r2v) | $0 |
| critic, strategist 2025-11-23 | **reused** (r2r-57fa83dc8f2f) | $0 |
| critic 2026-05-23 | live | about $0.003 |
| strategist 2026-05-23 | live | about $0.0056 |
| **Total** | 3 live calls | **$0.0087** ($0.0173 with retries); expected about $0.004 |

**Preparation and safeguards:**
- `scripts/r2_step1_revalidation.sh` now passes `--reuse-from r2r-57fa83dc8f2f`, with cap **$0.02** (was $0.04);
- the safeguards are unchanged: single-use approval, cumulative cap, traps, identity preflight, frozen criteria hash
  `c9fcd277…`;
- the offline CLI preflight on a copy of the store reports IDENTICAL for both dates, 3 live calls and the 2 reused
  calls with their request ids.

**Scientific status:**
- still a *mixed cached/live engineering revalidation*. 2025-11-23 becomes fully recorded (no new live sample) and
  2026-05-23 gets fresh critic and strategist replies;
- it tests engineering completion and gives the first critic and strategist sample for 2026-05-23;
- it is not an independent intelligence experiment (see the Step 2 proposal).

**Needs:**
- a new founder approval (ADR-019, under $0.10), because the previous approval was consumed and revoked;
- the run is performed by the founder in their own terminal.
