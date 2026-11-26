# Investigation result contract (`halibut.investigation_result.v1`)

Status: **implemented in R2** (`src/halibut/investigation/artifact.py`). The JSON Schema is published at
[schemas/investigation_result.v1.schema.json](schemas/investigation_result.v1.schema.json); a test
asserts the published file equals the code's schema. Related:
[R2_IMPLEMENTATION.md](R2_IMPLEMENTATION.md), [ADR-016](ARCHITECTURE_DECISIONS.md#adr-016).

Audience: anyone building a separate frontend (for example a Next.js dashboard) that displays real
Halibut results without access to Halibut's database.

## 1. One document per investigation

Each run of `halibut investigation run` produces one self-contained JSON document:

- it is stored append-only in `investigations.artifact_json`;
- it is written to `results/investigations/<id>.json`;
- `halibut.command.api.get_investigation` serves it.

A frontend needs nothing else to render an investigation.

| Top-level field | Meaning |
|---|---|
| `schema` | Always `halibut.investigation_result.v1`. A breaking change gets a new id (v2), never an in-place change |
| `investigation_id` | `<input_id>.<n>`: the n-th run with these inputs |
| `input_id` | Hash of the inputs: market spec, as-of, evaluation date, routing, prompt version, registry rule, tenant. Identical inputs share it |
| `content_hash` | sha256 of the substantive content (objective, evidence, world model, stages, protocol, links, item failures). **Identical inputs give an identical hash**; operational metadata is excluded |
| `created_at`, `tenant` | Run time (UTC); tenant (`public` unless a client tenant) |
| `status`, `stop_reason` | See §3 |
| `objective` | Market, mode, focal/target, decision, as-of, evaluation date, horizon, jurisdiction, success metric, budget |
| `labels` | Human-readable meaning of each output type (show these in the UI) |
| `evidence` | Coverage contract and coverage; panel source, universe, periods and snapshot; all window metrics as `derived_metric` items |
| `world_model` | Slice id, market definition, entities with identifiers, deterministic anomaly scan |
| `stages` | The item arrays of §2, in loop order |
| `experiment_protocol` | One `experiment_protocol` item or `null` |
| `links` | Every relation between items: `{from, relation, to}` |
| `failures` | `items` (every rejection, drop or unavailable item, with class and problems), `counts` by class, `calls` (failed LLM calls) and `completion` (`state`: `clean`, `partial_rejection` or `not_completed`; `rejected_arrays`; `unavailable_items`, the options not simulated and models not executed; `internal_error`; the downstream rule, ADR-020/021) |
| `agents` | Routing (role → model), every call (outcome, tokens, cost, cache hit, latency), totals |
| `approvals` | Approval records created by this run (e.g. the experiment request), with status |
| `provenance` | Engine, prompt, schema, registry-rule and test-spec versions; market spec sha256; panel snapshot |

## 2. The item envelope and stages

Every item in `stages.*`, `evidence.metrics` and `experiment_protocol` has the same shape:

```json
{"id": "c:…", "type": "hypothesis", "status": "refuted", "label": "short display text",
 "produced_by": "agent:loop_hypothesis_generator:v1", "as_of": "2025-05-23T23:59:59Z",
 "data": { "...type-specific..." }, "links": [{"relation": "cites", "to": "metric:segment_share:L4:tesla"}],
 "provenance": {"market_id": "…", "slice_id": "…", "prompt_version": "loop.v1", "model": "…"}}
```

| Stage | Item `type` | `status` values | Key `data` fields | Typical links |
|---|---|---|---|---|
| `evidence.metrics` | `derived_metric` | recorded | value, definition (template, entity, periods, source), inputs_count | — |
| `explanations` | `explanation` | proposed | — | `cites` → metric ids |
| `hypotheses` | `hypothesis` | proposed, survived, refuted, inconclusive, untested | origin, **novelty** (original or restatement), mechanism, conditions, assumptions, alternative_explanations, observable_implications, uncertainty, refutation_criteria, novelty_claim, model_request, status_detail (reason, strength) | `cites` → metric ids |
| `critiques` | `critique` | proposed | severity, challenged_assumption | `critiques` → hypothesis |
| `tests` | `test` | registered, not_testable | spec (template, entity, comparator, reference value, target periods, trend_check), reason, spec_hash | `tests` → hypothesis |
| `test_results` | `test_result` | passed, failed, pending | realized, reference_value, comparator, available_at, trend_consistent | `result_of` → test |
| `model_specs` | `model_version` | validated, unvalidated, rejected | family, target, assumptions, validation (rule, reproducible, backtest or problems) | `for_test` → test |
| `assumptions` | `assumption` | proposed | name, value, unit, basis, or statement and kind | `scopes` → model version or option |
| `forecasts` | `forecast` (**official**) | open, scored | model (family, version id), point, interval80, resolution, score | `from_model`, `informs` → test |
| `estimates` | `estimate` (**not official**) | proposed | value, interval, label, model_version, score | `from_model`, `informs` |
| `simulations` | `simulated_outcome` | recorded | scenario, outcome_range, baseline, assumed effect and offsets, assumptions, baseline_status | `simulates` → option |
| `strategic_options` | `strategic_proposal` | proposed | mode, levers, intended_effect, linked_hypotheses, supporting_evidence, assumptions, competitor_responses, cost (`known: false` when unknown), risks, uncertainty, success_criteria, compliance, `executable: false`, **simulation** (`status`: `simulated` or `not_simulated`, with `failure_class` and `reason` when not simulated; ADR-021) | `relies_on` → hypotheses, `simulated_by` → simulations |
| `experiment_protocol` | `experiment_protocol` | draft_not_executable | design, primary_metric, power, guardrails, stop_rules, measurement, operator, pre_registration_hash, **eligibility** (`status`: `exploratory_not_eligible` or `eligible_pending_human_review`, `eligible_for_execution`, `reasons`; ADR-021) | `tests` → option, hypotheses |
| `information_gaps` (**optional**; `loop.v2` only) | `information_gap` | open | missing_measurement, why_it_matters, distinguishes (refs and claims), potential_sources, expected_research_value, research_status, autonomous_research | `about` → hypotheses |
| `lessons` | `lesson` / `question` | proposed | knowledge_id, status_detail / needs | `derived_from` or `about` → hypothesis, `evaluated_by` → results |

**Display rules the frontend must respect:**
- an `estimate` must never be shown as a forecast;
- a `simulated_outcome` must be shown as conditional on its scenario and assumptions;
- a `strategic_proposal` must be shown as a proposal awaiting human, legal and compliance review;
- `survived` must be shown with its `status_detail.strength` and the label "not causal proof";
- a `strategic_proposal` with `simulation.status = not_simulated` has no simulated outcome: show its reason, never a number;
- an `experiment_protocol` whose eligibility is `exploratory_not_eligible` must be shown as exploratory, with its reasons; a draft is never evidence that a strategy is valid;
- `provenance.agents = recorded` means saved replies of a real model replayed offline (genuine AI output, not new); `mock` and `reference` are scripted.

## 3. Investigation statuses

| `status` | Meaning |
|---|---|
| `completed` | The loop ran to the end. Some stages may still be empty, with reasons in `failures` (e.g. no option passed validation). `failures.completion.state` says whether it was `clean` or had a `partial_rejection`; dashboards must show the difference |
| `stopped` | A stopping rule fired: the Hypothesis Generator's reply was unusable (truncated, refused, invalid JSON, schema violation) or the budget was exhausted. `stop_reason` says which. Items already produced are kept. **`stop_reason` starting `internal_error:`** means an unexpected engine defect (failure class `internal.error`, `completion.internal_error: true`): dashboards must show it as an engine failure, never as a result (ADR-021) |
| `insufficient_evidence` | There is no transaction panel, or fewer than 8 quarters as of the date. No agent was called |
| `invalid_market` | The market fails the coverage contract ([ADR-014](ARCHITECTURE_DECISIONS.md#adr-014)). No agent was called |

## 4. Read-only API requirements (for a frontend)

R2 implements the functions; an HTTP layer is **not** built (deferred by founder decision 8). Any
HTTP layer must map one to one:

| HTTP (to be built later) | Function (`halibut.command.api`) | Notes |
|---|---|---|
| `GET /v1/investigations` | `list_investigations(store, tenant)` | Paginate by `created_at`; filter by market and status |
| `GET /v1/investigations/{id}` | `get_investigation(store, id, tenant)` | Use `content_hash` as the ETag; the document is immutable |
| `GET /v1/investigations/{id}/items?type=hypothesis` | `get_items(store, id, type, tenant)` | Types from §2 |
| `GET /v1/approvals?status=requested` | `list_approvals(store, status)` | Read-only. Decisions stay in the CLI (`halibut approvals decide`), by a named person |
| `GET /v1/schema/investigation_result.v1` | `schema()` | The published JSON Schema |

Requirements:
1. **No database access and no writes.** Documents and approval records are append-only.
2. **Tenancy:** every call is scoped to the caller's tenant (own plus public). Client tenants must not
   be served until tenancy is enforced on all tables ([ADR-013](ARCHITECTURE_DECISIONS.md#adr-013)).
3. **Versioning:** the frontend checks `schema` and rejects unknown versions. New optional fields may
   appear in `data` without a version change; envelope changes need a new version.
4. **Authentication** (when an HTTP layer exists): read access per tenant. Approving anything from a
   dashboard needs a new ADR, because approvals must stay attributable to a named person.

## 5. loop.v2 additions (ADR-022; additive, absent from loop.v1 documents)

| Where | New field |
|---|---|
| `stages.information_gaps` | optional stage; items of type `information_gap` |
| hypothesis `data` | `explains` (metric id), `rivals` (refs), `discriminating_implications` ({implication id: rival refs}) |
| test `data` | `discriminates_from` (rival refs that predict the opposite outcome) |
| critique `data` | `dimension` (6 values), `evidence` (metric ids) |
| strategic proposal `data.intended_effect` | `entity_b` (comparator company for pairwise targets) |
| strategic proposal `data` | `numeric_claims`: each number in the text with value, unit, decimals, kind (`observed`, `calculated`, `assumed`) and source metric ids |
| `provenance.prompt_version` | `loop.v2:base` or `loop.v2:extended` (`loop.v1` unchanged) |
| `provenance.schemas` | `hypothesis_mechanism.v2`, `critic_structured.v2`, `strategy_options.v2` |
