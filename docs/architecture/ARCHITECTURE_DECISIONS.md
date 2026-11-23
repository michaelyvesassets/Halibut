# Architecture Governance and Decision Records (Master Architecture v1.0)

Related documents:
- [MASTER_ARCHITECTURE.md](MASTER_ARCHITECTURE.md)
- [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md#5-decisions-requiring-founder-approval)

## 1. Versioning

- **Architecture version** uses `MAJOR.MINOR`:
  - MAJOR changes layer boundaries, the claim taxonomy or the trust rules;
  - MINOR adds components, interfaces or maturity steps within existing boundaries.

  This document set is **v1.0 (proposed)**. It becomes v1.0 *accepted* when the founder
  approves ADR-001 to ADR-013 or amends them.
- **Contract versions** cover JSON schemas, protocols, claim types and evaluation rules. Each
  has its own semantic version (for example `hypothesis.v1`, `paired-dev-holdout-v1`, which
  exists as `RULE_VERSION` in `learning/evaluate.py`). Every stored record keeps the version
  it was written with.
- **Code versions** stay as today: `version.py` (engine version) and lineage in
  `learning/lineage.py`.

## 2. Decision records (ADRs)

**Template.** Each ADR has:
- id and title;
- status: Proposed, Accepted, Superseded by ADR-x, or Rejected;
- date;
- context;
- decision;
- consequences;
- alternatives considered;
- founder approval (name and date, when accepted).

ADRs are append-only. A change creates a new ADR that supersedes the old one.

**When an ADR is required:**
- a new layer, plane or cross-layer dependency;
- a change to the claim taxonomy or trust rules;
- new infrastructure (database, queue, hosted service, console);
- a new paid provider or data source class;
- a change to evaluation or promotion rules;
- anything touching external actions.

## 3. Module ownership

At MVP the owner is the founder plus the implementing engineer or agent. The mapping fixes
*where* code for each layer lives, so boundaries can be checked.

| Layer | Package (current → target) | Contract owner |
|---|---|---|
| L1 | `halibut.agents.llm`, `halibut.agents.budget`, `halibut.agents.anthropic_provider`, `halibut.batch`, `halibut.data.http` → `halibut.infra.*` | Infrastructure |
| L2 | `halibut.data.catalogue`, `halibut.data.ssb`, `halibut.data.providers.*`, `halibut.data.quality`, `halibut.data.events` → `halibut.acquisition.*` | Acquisition |
| L3 | `halibut.data.store`, `halibut.intel.retrieval`, `halibut.intel.signals`, `halibut.intel.market` → `halibut.world.*` | World model |
| L4 | `halibut.agents.orchestrator`, `halibut.agents.mock`, `halibut.intel.agents` → `halibut.workforce.*` | Workforce |
| L5 | `halibut.engine` and the simulation modules, `halibut.empirical.*`, `halibut.calibration`, `halibut.sensitivity`, `halibut.suite`, `halibut.intel.forecast` → `halibut.models.*` | Modelling |
| L6 | `halibut.learning.*`, `halibut.intel.replay`, `halibut.intel.benchmark` → `halibut.learning.*` | Learning |
| L7 | (new) → `halibut.strategy.*` | Strategy |
| L8 | `halibut.cli`, `*/cli.py`, `halibut.intel.report`, `halibut.export` → `halibut.command.*`, `halibut.control.*` | Governance |
| EI | `halibut.validation`, `halibut.intel.audit` → `halibut.epistemic.*` | Epistemic integrity |

Moving modules to the target packages is **not** part of v1.0. It happens per milestone,
behind compatibility imports, and only when a milestone already touches the module.

## 4. Contracts and compatibility

- Cross-layer calls go through protocols (`halibut.interfaces` today) or registered schemas.
  Importing another layer's internals is a review failure. An import-lint test (R1) enforces
  the dependency rules in
  [MASTER_ARCHITECTURE.md §6.4](MASTER_ARCHITECTURE.md#64-dependency-rules-acyclic).
- **Database migrations are append-only and numbered** (the existing `MIGRATIONS` list).
  Columns are added, never repurposed. Destructive changes need an ADR and a data migration
  plan.
- **Stored records are readable forever.** Readers handle every past schema version, or a
  migration upgrades them with an audit entry.
- Deprecation takes at least one milestone and is announced in the ADR log.
- **Reproducibility contract.** For any recorded run, the code version, configuration hash,
  data snapshots and vintages, model versions, prompt and schema versions, and seeds are
  enough to re-execute it. The LLM cache makes LLM steps replayable.

## 5. Rules for major changes

1. Write an ADR (Proposed), with the impact on contracts, data and costs.
2. Show offline evidence: tests, and a benchmark on fixed evidence where quality is affected.
3. Get founder approval for: new infrastructure, paid services, trust or evaluation rule
   changes, external actions.
4. Land it behind a compatibility layer; keep the previous version runnable until the next
   milestone.
5. Record the outcome in the ADR (Accepted, plus the commit).

---

## 6. ADR log

### ADR-001
**Eight logical layers plus the EI plane, in one deployable application and one database at MVP.**
- **Status:** Proposed.
- **Context:** principle I; a small team; current single-process CLI.
- **Decision:** layers are logical boundaries (packages, protocols, tables), not services. One
  process, one SQLite database (Postgres per ADR-011), local blobs.
- **Consequences:** low operating cost; boundaries enforced by tests and import rules, not by
  networks.
- **Alternatives considered:** microservices per layer, rejected as premature.

### ADR-002
**Typed claims: explanations, hypotheses, forecasts and simulated outcomes are distinct types with distinct validation and evaluation.**
- **Status:** Proposed.
- **Context:** principle A; benchmark finding F1.
- **Decision:** the claim taxonomy in
  [MASTER_ARCHITECTURE.md §4](MASTER_ARCHITECTURE.md#4-the-claim-taxonomy-principles-a-and-c),
  enforced by EI.
- **Consequences:** reports and evaluations can no longer mix types.
- **Alternatives considered:** a single "insight" type, rejected because it caused F1-style
  confusion.

### ADR-003
**The World Knowledge Base is relational and bitemporal; no graph database at MVP.**
- **Status:** Proposed.
- **Context:** most data is vintaged time series; the graph is small relative to observations.
- **Decision:** entities, relationships, assertions and hierarchies live in relational tables
  with recursive queries. Add a graph index only if measured query needs justify it.
- **Consequences:** one storage technology; as-of semantics stay uniform.
- **Alternatives considered:** a graph database now, rejected as premature and weak for
  vintaged series.

### ADR-004
**Agents propose hypotheses and model specs; registered models compute forecast numbers.**
- **Status:** Proposed.
- **Context:**
  - principle B;
  - the benchmark showed that "agents choose predefined predictions" cannot show original
    insight (F1);
  - but LLM-written numbers would be irreproducible and unvalidated.
- **Decision:**
  - agents write mechanisms, implications and model specs;
  - deterministic compilers and L5 registered models produce resolvable tests and forecasts;
  - agents never write forecast numbers directly.
- **Consequences:** originality moves to *what is modelled and tested*. Forecasts stay
  reproducible.
- **Alternatives considered:**
  - LLM-written forecasts, rejected as irreproducible and leakage-prone;
  - keeping selection only, rejected (F1).

### ADR-005
**One LLM gateway with provider adapters; Anthropic first, Claude Haiku 5.5 as the default for every role; routing by task class.**
- **Status:** Accepted for the provider and model (existing practice since 2026-11-23);
  Proposed for routing.
- **Decision:**
  - providers behind `LLMProvider`;
  - Qwen kept as an alternative;
  - a routing-table version changes only by promotion.
- **Consequences:** model upgrades are evaluated, not assumed.

### ADR-006
**Fail-closed spending and per-run approval.**
- **Status:** Accepted (existing practice: `agents/budget.py`, approval flags,
  `--max-usd`).
- **Decision:**
  - every attempt is reserved before it is sent;
  - caps are application-wide;
  - every paid run needs explicit founder approval;
  - committed configs stay `approved = false`.

### ADR-007
**Lawful strategy policy for the three strategic modes.**
- **Status:** Proposed. It extends existing practice: `ALLOWED_LEVERS`, `PROHIBITED`.
- **Decision:**
  - modes are analytical objectives;
  - options use registered lawful levers only;
  - per-jurisdiction compliance checklists;
  - human legal review for consequential options;
  - Halibut never executes external actions.

  See [STRATEGIC_OPERATING_LOOP.md §3](STRATEGIC_OPERATING_LOOP.md#3-lawful-strategy-policy-for-the-three-modes).

### ADR-008
**Temporal integrity, including revisions and model-knowledge leakage.**
- **Status:** Proposed. It extends existing as-of practice.
- **Decision:**
  - as-of reads use the latest vintage available at t;
  - availability bases are reported;
  - LLM claims carry the training-cutoff tag;
  - promotion uses post-cutoff evidence.

### ADR-009
**Structured outputs are mandatory for agent tasks; malformed output is rejected, never repaired.**
- **Status:** Proposed.
- **Context:** benchmark F2, F3 and F4.
- **Decision:**
  - registered JSON schemas with enums drawn from slices;
  - provider-side enforcement where available;
  - EI validation always;
  - failed-reply text stored.
- **Alternatives considered:** lenient post-hoc parsing, rejected because it weakens
  validation.

### ADR-010
**CLI and generated reports are the command surface through R6; a read-only console after R2 only with founder approval.**
- **Status:** Proposed.
- **Context:** earlier instructions not to build a dashboard; cost.
- **Decision:** the control plane (approvals, budgets, audit) is built first, as data and CLI.
  The console visualises it later.

### ADR-011
**SQLite until a trigger fires, then Postgres with the same schema.**
- **Status:** Proposed.
- **Triggers (any one):**
  - more than one concurrent writer process in normal operation;
  - more than 50 M observations;
  - multi-user access;
  - client data that requires row-level security;
  - hosted deployment.

### ADR-012
**The first live benchmark diagnosis is recorded as a known limitation and architectural input.**
- **Status:** Accepted (record).
- **Context:** `docs/benchmark_haiku_v1_diagnostic.md`.
- **Decision:**
  - the findings F1–F7 drive ADR-002, ADR-004 and ADR-009 and roadmap milestone R1;
  - its numbers are not quoted as evidence of LLM skill or the lack of it;
  - the proposed fixes stay deferred until R1 is approved.

### ADR-013
**Tenancy and confidentiality from the start.**
- **Status:** Proposed.
- **Decision:**
  - every private record carries a tenant;
  - public knowledge is shared;
  - context assembly filters by tenant;
  - isolation tests are required before any client data is ingested;
  - row-level security arrives at Postgres.
- **Alternatives considered:** adding tenancy later, rejected because retrofitting isolation
  is error-prone.
