"""Epistemic Integrity plane (Master Architecture v1.0, R1).

Deterministic, LLM-free truth discipline shared by every layer:

- ``claims``    typed claim ledger: observations, explanations, hypotheses, forecasts, estimates,
                simulated outcomes, critiques, strategic proposals ...; one validator per type;
                append-only; as-of and tenant-aware reads.
- ``events``    append-only, hash-chained event/audit log with provenance.
- ``schemas``   strict JSON schemas for agent outputs (enums drawn from the evidence bundle), a local
                validator, and the API-safe subset sent to providers that enforce schemas.
- ``failures``  failure taxonomy and classifier for agent calls and agent output items.

Nothing here repairs malformed output: it accepts or rejects, with reasons.
"""
