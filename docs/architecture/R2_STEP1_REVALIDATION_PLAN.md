# R2 Step 1: mixed cached/live engineering revalidation (plan)

Status: **approved by the founder on 2026-11-24 (cap $0.04); prepared, tested and rehearsed offline; not run.**
It runs only through `scripts/r2_step1_revalidation.sh`. Code: `src/halibut/investigation/revalidation.py`.
Related: [R2_POSTMORTEM_r2v-b16fc9281536.md](R2_POSTMORTEM_r2v-b16fc9281536.md),
[R2_LIVE_VALIDATION_PLAN.md](R2_LIVE_VALIDATION_PLAN.md), [ADR-020](ARCHITECTURE_DECISIONS.md#adr-020).

> **This is a MIXED CACHED/LIVE ENGINEERING REVALIDATION, not a fresh independent intelligence experiment.** The
> Hypothesis Generator is never called: its two replies are the ones recorded in r2v-b16fc9281536. Only the smoke test,
> 2 critic calls and 2 strategist calls are live.

## 1. Purpose

Show that the fixed pipeline completes end to end on **identical intelligence and identical evidence**:
- ADR-020 per-array rejection;
- like-with-like tests;
- canonical signatures and the prior-knowledge library;
- the direction check.

Then assess the critic and strategist, which never ran in the original experiment. The original experiment, its
outputs and its result (A2, A3 and B1–B5 failed) stay unchanged.

## 2. Identity (verified before any paid call)

For each date, the generator request is rebuilt on a throwaway copy of the store, with no network. The run is
refused unless all of these match the original run:

| Check | 2025-11-23 | 2026-05-23 |
|---|---|---|
| Cache hit with the recorded reply | yes | yes |
| Prompt fingerprint (sha256 of system + user, recorded in `agent_calls`) | 2d8c2ef354c29758 | 015d0f3e5cf2e53e |
| Original request id | req_011ChLwmSvd7AmDDiw1bcY5m | req_011ChLwo32W5FacEvPgAsxG7 |
| Model, prompt version (`loop.v1`), prompt size, schema size, evidence fingerprint | identical | identical |

The cache key itself hashes provider, model, role, system prompt, evidence bundle, request settings and the output
schema. A hit therefore also proves the schema is identical. The dry run on 2026-11-24 reported IDENTICAL for both
dates.

In the live run the generator role is **cache-only**: a miss raises instead of calling the provider. The critic and
strategist are **always live**: never answered from the cache, though their replies are recorded.

## 3. Calls, cost and safeguards

- **Calls:** 1 smoke + 2 critic + 2 strategist = **5 live calls**, 0 generator calls. At most 9 attempts with one
  retry per call on transient errors.
- **Cost:** worst case **$0.0173**; with every call retried, $0.0346. Cap **$0.04**. Expected about $0.006.
- **Safeguards, unchanged from the original experiment:**
  - single-use approval (`r2_revalidation:uk_cars_first_registrations`), bound to this run before the smoke test;
  - cumulative fail-closed cap (smoke, calls and retries);
  - the second key lives only in a temporary copy outside the repository;
  - EXIT, INT and TERM traps revoke the approval and delete the temporary key;
  - pipefail;
  - execution records and audit events.
- The critic and strategist prompts are byte-identical to `loop.v1` (hashes pinned by a test). **No postmortem
  finding is sent to any agent** (tested).

## 4. Costs reported separately

- historical generator cost, per date and in total (from the original ledger: $0.0028006 and $0.0024944);
- incremental live cost (smoke, critic, strategist), per date and in total;
- total equivalent investigation cost per date = historical generator cost + incremental live cost.

## 5. What is evaluated

- **Frozen criteria** A1–A6 and B1–B5 (hash `c9fcd277…`, unchanged), each with an explicit note on whether its
  interpretation is affected by the validator changes (§7).
- **Completion state** per date (ADR-020): `clean` or `partial_rejection`, with the rejected arrays listed. The
  explanations array is expected to be rejected (5 > 4) and is never passed to the critic or strategist.
- **Critic detection** of the postmortem's known issues. The critic is not told about them, and detection is screened
  by keyword afterwards; every matched critique is reported verbatim for human confirmation:

  | Issue | Date and target | Kind | Notes |
  |---|---|---|---|
  | Tesla "non-BEV volume" | 2025-11-23 H4 | contradicted assumption | |
  | "hybrid and petrol" | 2026-05-23 H1 | unsupported detail | |
  | "recent BEV launches" | 2026-05-23 H3 | unsupported detail | |
  | "product-led step-up" | 2025-11-23 H2 | detail outside the evidence | |
  | VW–MG gap against MG's level | 2025-11-23 H3 | mis-encoded comparison | |
  | MG–Kia gap against Kia's level | 2026-05-23 H3 | mis-encoded comparison | |
  | "fell from 0.44% to 2.47%" | 2026-05-23 E2 | direction error | **not applicable**: the explanation was rejected and is never shown to the critic |

- **Strategist assessment** per option:
  - originality (lever set against the deterministic reference templates; distinct lever sets);
  - evidence support (cited ids exist; how many concern the intended entity);
  - feasibility (known or unknown cost and its basis; assumed effect against the national minimum detectable
    effect; large-effect flag);
  - assumptions;
  - competitor responses;
  - measurable outcomes;
  - simulations and compliance flags;
  - rejected options with reasons.

## 6. Outputs

The script writes `results/r2_validation/step1-<UTC timestamp>/` (logs, spend before and after, final approval
record). Its `r2r-<id>/` subfolder holds:
- `investigations/mixed_cached_live_claude_<date>.json`, the dashboard-format documents, classed
  `mixed_cached_live`;
- the comparison report (JSON and Markdown), execution records and manifest.

The manifest references the original run's reference and mock documents by content hash; they are not modified.

## 7. Interpretation of the frozen criteria after the validator changes

| Criterion | Effect |
|---|---|
| A1 smoke, A4 documents, A5 evidence, A6 spend | unaffected (A5 is also proven by the identity preflight) |
| A2 completed | "completed" may now carry a partial rejection; the completion state is reported next to it |
| A3 no whole-reply rejection | an over-long array is now rejected as an array, so A3 can pass while the explanations array was rejected; array rejections are reported separately |
| B1 fidelity | stricter (direction check) |
| B2 originality | canonical signatures and the prior-knowledge library: fewer items count as original than under the original implementation |
| B3 falsifiability, B4 testability | mis-specified comparisons are now not testable |
| B5 informativeness | definition unchanged; computed over the correctly specified tests |

The postmortem's offline replay predicts B2, B3 and B5 fail on these replies. Step 1 tests engineering, not a new
intelligence sample.
