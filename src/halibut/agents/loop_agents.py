"""Agent roles of the R2 intelligence loop: Hypothesis Generator (+ Investigator), Critic, Strategist.

Each role gets the world slice (never the store), a strict schema built from that slice, and one call.
The orchestrator (``halibut.investigation.loop``) decides whether a role is invoked at all.

ORIGINALITY. The Generator writes mechanisms, conditions, assumptions, alternative explanations,
uncertainty and refutation criteria in free text. Only the OBSERVABLE IMPLICATIONS use the closed
measurement grammar (so they can be compiled into pre-registered tests); an implication that needs
data this evidence does not contain uses template "other" and is recorded as untested. The Generator
may request a model family and numerical assumptions; it never writes forecasts.
"""

from __future__ import annotations

import hashlib
import json

from ..epistemic.failures import classify_outcome
from .llm import record_call

PROMPT_VERSION = "loop.v1"
AGENT_IDS = {"hypothesis_generator": "agent:loop_hypothesis_generator:v1", "loop_critic": "agent:loop_critic:v1",
             "strategist": "agent:loop_strategist:v1"}

GENERATOR_SYSTEM = (
    "You are Halibut's Investigator and Hypothesis Generator for ONE market, as of the slice's date. First write up to 4 "
    "EXPLANATIONS of observed anomalies (interpret the past only; cite metric ids). Then write up to 6 ORIGINAL, falsifiable "
    "HYPOTHESES about the mechanisms driving the market. Each hypothesis needs: a mechanism (cause, pathway, effect, "
    "direction); scope conditions; explicit assumptions; supporting evidence (metric ids copied exactly from slice.metrics); "
    "at least one ALTERNATIVE explanation and the implication that would discriminate between them; 1 to 4 OBSERVABLE "
    "IMPLICATIONS for the next two quarters in the measurement grammar (template, entity, optional entity_b, comparator, "
    "optional reference metric id) with what would refute it; uncertainty; refutation criteria; why it is new (novelty_claim); "
    "and optionally a model family and numerical assumptions with their basis. Do not restate simple trend continuation: "
    "propose mechanisms that could be wrong. If an implication needs data not in the slice, use template 'other' and describe "
    "it. Every number in text must equal a slice value (percent or percentage points, 2 decimals). Never write forecasts. "
    "Reply with a JSON object matching the schema.")
CRITIC_SYSTEM = (
    "You are Halibut's Critic. Independently challenge each hypothesis: confounders, denominators and universe definitions, "
    "segment-mix versus within-segment effects, unobserved switching, whether its tests could actually fail, and the weakest "
    "assumption. Do not soften or merge disagreements. At most 3 critiques per hypothesis, each at most 80 words. Reply with a "
    "JSON object matching the schema.")
STRATEGIST_SYSTEM = (
    "You are Halibut's Strategy Analyst. Propose 2 to 4 COMPETING lawful strategy options for the objective, each linked to the "
    "hypotheses it relies on. For each: intended market effect (metric, entity, direction, an ASSUMED magnitude range in "
    "percentage points with its basis), supporting evidence ids, assumptions, expected competitor responses, cost (set "
    "known=false with low=high=0 and say why if you do not know it; never invent precise costs), risks, uncertainty and "
    "measurable success criteria in the measurement grammar. Use only the listed levers. No deceptive, covert, "
    "anti-competitive or unauthorised actions; options are proposals for human review, never instructions. Every number in "
    "text must equal a slice value or the option's own assumed magnitudes. Reply with a JSON object matching the schema.")
SYSTEMS = {"hypothesis_generator": GENERATOR_SYSTEM, "loop_critic": CRITIC_SYSTEM, "strategist": STRATEGIST_SYSTEM}


def call_role(router, store, role: str, payload: dict, schema: dict, run_id: str) -> dict:
    """One routed, cached, accounted call. Returns {'data', 'response', 'outcome', 'failure_class', 'prompt_hash'}."""
    system = SYSTEMS[role]
    user = "BUNDLE:" + json.dumps(payload, sort_keys=True, default=str)
    ph = hashlib.sha256((system + user).encode()).hexdigest()[:16]
    provider, max_out = router.provider(role)
    agent = AGENT_IDS[role]
    try:
        r = provider.complete(system, user, role=role, max_output_tokens=max_out, run_id=run_id, schema=schema)
    except Exception as exc:  # budget, approval or transport failure: recorded, then the orchestrator stops
        record_call(store, run_id, agent, role, None, ph, outcome=f"error: {exc}"[:200], provider=provider.name,
                    model=provider.model)
        return {"data": None, "response": None, "outcome": f"error: {exc}"[:200], "failure_class": "call.error",
                "prompt_hash": ph, "error": str(exc)[:300], "prompt_chars": len(system) + len(user),
                "schema_chars": len(json.dumps(schema)), "max_output_tokens": max_out, "stop_reason": None}
    record_call(store, run_id, agent, role, r, ph)
    outcome = "ok" if r.data is not None else store.conn.execute(
        "SELECT outcome FROM agent_calls WHERE run_id=? AND prompt_hash=? ORDER BY rowid DESC LIMIT 1", (run_id, ph)).fetchone()[0]
    return {"data": r.data, "response": r, "outcome": outcome, "failure_class": classify_outcome(outcome), "prompt_hash": ph,
            "prompt_chars": len(system) + len(user), "schema_chars": len(json.dumps(schema)), "max_output_tokens": max_out,
            "stop_reason": getattr(r, "stop_reason", None)}
