# R1: Epistemic foundation and contracts (implemented)

Status: **implemented, offline only** (2026-11-23). R1 made no paid API calls; the spend ledger
still holds its 24 entries ($0.020759).

This document describes what the code does now. The design it implements is in
[MASTER_ARCHITECTURE.md](MASTER_ARCHITECTURE.md#ei-epistemic-integrity-plane-cross-cutting) and
[ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md). The status of every capability is in
[CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md).

## 1. What was built

| Area | Files | Behaviour |
|---|---|---|
| Storage | `src/halibut/data/store.py` (migration 6) | Adds tables `claims`, `claim_links`, `claim_status_history`, `validation_records` and `events_log`, plus the column `agent_calls.response_excerpt`. Triggers make all five tables append-only. The migration runner now splits statements with `sqlite3.complete_statement`, so trigger bodies work |
| Claim ledger | `src/halibut/epistemic/claims.py` | Twelve claim types, each with its own validator. Claims are immutable and idempotent. Identical content in a run gets a `duplicates` link. Reads are by availability date (`as_of(t)`) and filtered by tenant (own + public). Status history is kept by availability date. `scorable()` is true only for forecasts |
| Event log | `src/halibut/epistemic/events.py` | Hash-chained (sha256 over content + previous hash). `verify()` reports the first tampered event. Each event records its layer, run, tenant, subject and provenance |
| Output contracts | `src/halibut/epistemic/schemas.py` | Schemas per role: `research.v2`, `hypothesis_selection.v2`, `critic.v2`, `smoke.v1`. They are built from the bundle (id, subject, tool and hypothesis-id enums). The full schema is validated locally; `api_schema()` is the subset Claude enforces at generation (no length or array-size keywords) |
| Failure taxonomy | `src/halibut/epistemic/failures.py` | Call classes (truncated, refusal, invalid_json, schema_top_level, error, not_reproducible) and item classes (format.\*, unsupported.\*, fabricated.\*, validation.\*, policy.\*, temporal.\*). Also the validation-record writer and run summaries |
| Providers | `src/halibut/agents/llm.py`, `src/halibut/agents/anthropic_provider.py` | `complete(..., schema=)`. Anthropic sends `output_config.format` (merged with `effort`). Qwen and the mock rely on local validation. The cache key includes the schema. Failed replies get a typed outcome and up to 4,000 characters of their text are stored. Every call emits an `llm.call` event |
| Agents | `src/halibut/agents/orchestrator.py`, `src/halibut/agents/mock.py` | Prompt versions `v1` (verbatim from the first live benchmark, no schema; reconstruction only) and `v2` (default: explicit shapes and limits, strict schema). Items are accepted or rejected one by one, never repaired. Drops are returned with their raw item and cause. The exposure subject label is fixed (`brand:brand:x`) |
| Workflow | `src/halibut/intel/workflow.py` | Records every schema rejection, validation rejection and drop in `validation_records`. Writes claims: one `hypothesis` per accepted hypothesis (with refutation criteria), `forecast` claims from the engine policy (never from an agent) linked `tests` → hypothesis, `critique`, and `simulated_outcome` for synthetic previews (conditional, uncalibrated) |
| Scoring | `src/halibut/learning/predictions.py` | Candidate error explanations become `explanation` claims. Their legacy knowledge rows remain kind `hypothesis` for compatibility; see §5 |
| Learning memory | `src/halibut/learning/memory.py` | Status changes, promotions and rollbacks emit events. Promotion still requires a named person |
| Replay and benchmark | `src/halibut/intel/replay.py`, `src/halibut/intel/benchmark.py` | An `agent_factory` hook. Identical predictions are removed before evaluation (raw and unique counts reported). Each origin reports its drop and rejection classes. Benchmarks report `overlap_with_reference` and the conditional-critic rule. `reconstruct()` is the offline replay of a past run |
| Coverage contract | `src/halibut/world/coverage.py`, `configs/data/provider_coverage.toml`, `src/halibut/intel/market.py` | An industry-neutral market declaration (`[measurement]`, competitor `identifiers`), checked against a provider coverage registry, plus an as-of evidence coverage report. Legacy `dft_make` and `osm_brand` keys still work |
| CLI | `src/halibut/intel/cli.py` | `halibut intel coverage MARKET [--as-of D]`; `halibut intel benchmark MARKET --reconstruct REPLAY_ID` |

## 2. Claim types as implemented

| Type | Who may produce it | Enforced rules (v1) |
|---|---|---|
| observation, derived_metric | Not agents | References an `obs:`, `rel:`, `doc:` or `ent:` id; derived metrics need a definition and inputs |
| estimate | Anyone | Value, uncertainty and method. **Agent estimates must be `candidate = true`**: AI estimates kept as labelled candidates, evaluated separately (founder decision 5) |
| assumption | Anyone | Scoped to a model or scenario |
| explanation | Anyone | Cites evidence; no forecast fields |
| hypothesis | Anyone (agents: proposed only) | Evidence, observable implications (metric and direction), refutation criteria, origin (`original`, `template_selection` or `reference_rule`); no point forecast |
| forecast | **Never agents** | Registered model id and version; point inside an 80% interval; resolution rule resolving after the as-of date |
| simulated_outcome | Engine | Scenario, assumptions, `conditional = true`, calibration status; **no resolution rule**, so it can never be scored |
| critique | Anyone | Target claim; severity low, medium, high or `unrated` |
| strategic_proposal | Anyone (agents: proposed only) | Registered lawful levers only; no prohibited tactics; `requires_approval = true`; compliance review status. No code produces these yet (L7 is R2+) |
| experiment_result, lesson | — | Protocol and measurement provenance; evaluations. No code produces these yet |

Rules for all types:
- cited evidence must be available at the claim's as-of date when a checker is given;
- a claim cannot be available before it was made;
- agents create only `proposed` claims;
- `validated` requires an evaluation and a non-agent decision.

## 3. Acceptance: reconstruction of the saved Haiku responses

The command:

```
halibut intel benchmark configs/intel/markets/uk_cars_bev_v1.toml --reconstruct rpf5673fab06
```

It runs on a throwaway copy of the store with the original `v1` prompts. Cached replies are
served from the prompt cache. Calls whose replies were never cached return the outcome recorded
at the time. Result, 2026-11-23:

| | Diagnosed (`docs/benchmark_haiku_v1_diagnostic.md`) | Reconstructed (R1) |
|---|---|---|
| Cached Hypothesis replies served | 10 | 10 |
| Recorded failed calls served | 13 | 13 (all `call.truncated`: 10 research, 3 critic) |
| Valid / rejected / dropped hypotheses | 10 / 17 / 9 | 10 / 17 / 9 |
| Rejections by class | 13 dict evidence, 4 value appended, 0 fabricated | `format.evidence_not_id_list` 13, `format.evidence_value_appended` 4 |
| Drops by class | 8 subject format, 1 unsupported | `format.subject_not_canonical` 8, `unsupported.kind_or_subject` 1 |
| Scored predictions raw → unique | 10 → 8 | 10 → 8 (2 duplicates removed) |
| Unique: supported / refuted / hit / MAE / coverage | 5 / 3 / 0.75 / 1.99 pp / 0.75 | 5 / 3 / 0.75 / 1.989 pp / 0.75 |
| The same 36 items under the R1 contract | — | 4 would pass, 32 rejected (evidence not an id list 16, subject not canonical 9, value appended 7); **none repaired** |

No requests were sent, and the spend ledger was unchanged before and after.

The reference replay is also unchanged by deduplication:
- 61 hypotheses, 41 supported, 20 refuted, hit rate 0.820, MAE 1.230 pp, coverage 0.787;
- it wrote 373 forecast, 61 hypothesis, 31 explanation, 10 critique and 1 simulated-outcome
  claims, with zero ledger rejections;
- its 915-event chain verifies.

## 4. Tests

There are 26 new tests: `tests/test_epistemic_claims.py`, `tests/test_epistemic_events.py`,
`tests/test_schemas_and_failures.py` and `tests/test_r1_pipeline.py`. Fixtures:
- `tests/fixtures/haiku_rpf5673fab06_hypothesis_items.json`: the 36 real Haiku items;
- `tests/fixtures/market_non_vehicle.toml`: a fictional non-vehicle market.

Coverage:
- every claim type's rules, and agents being unable to issue forecasts;
- temporal visibility and status as of a date;
- tenant isolation;
- immutability, idempotence and duplicate links;
- hash-chain tamper detection;
- schema enums and limits, the API-safe subset, and top-level rejection;
- typed call outcomes;
- classification of the real Haiku items;
- claims and failures written by a replay;
- strict-mode rejection without repair;
- storage of failed-reply text;
- reconstruction of a recorded run without sending anything;
- the industry-neutral coverage contract.

All 259 tests pass: 233 existing and 26 new.

## 5. Known limits of R1 (honest list)

1. **Structured outputs are untested live.** The request shape is tested through the real SDK
   over a mock transport. Whether Haiku 5.5 accepts these exact schemas and stops producing the
   F2/F3 forms will be known only after an approved paid run.
2. **Truncation is mitigated, not proven fixed.** Compact schemas and the limits stated in the
   prompts should shorten replies. Limits the API does not enforce (item counts, lengths) are
   enforced locally by *rejection*, so an over-long reply is rejected, not shortened.
3. **Hypotheses are still template selection** (`origin = template_selection`). Original
   hypothesis generation is R2.
4. **Explanations stored as `hypothesis` knowledge.** The scorer's candidate explanations still
   have legacy knowledge rows of kind `hypothesis`; the typed ledger records them correctly as
   `explanation` claims. The legacy rows stay for compatibility with the learning memory.
5. **Scoring still reads the learning memory** (`knowledge` predictions). Forecast claims mirror
   those predictions one to one, but the ledger is not yet the scorer's input.
6. **Tenancy covers claims and validation records only.** Other tables have no tenant column.
   Client data must not be ingested until tenancy covers them (founder decision 7).
7. **Reconstruction of successful critic calls is impossible.** Critic prompts embed run-specific
   hypothesis ids, so their cached replies cannot be found again. They are reported as
   `call.not_reproducible`. In the Haiku run all 3 critic calls were truncated, so this did not
   arise.
8. **No evidence other than transactions at historical origins.** The coverage report shows
   footprint and media evidence only from the 2026 retrievals. Historical replays use
   transactions only.
