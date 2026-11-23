"""Agent sets: deterministic reference vs LLM-backed (Research -> Hypothesis -> Critic).

Selective invocation (cost control):
- Research is invoked only when evidence gaps exist; it may run OFFLINE store
  tools only. Network/provider actions are returned as proposals pending
  approval. Identical tool calls within a run are executed once (duplicates counted).
- Hypothesis: one call per run; identical evidence bundles hit the cache.
- Critic: one batched call for all validated hypotheses; skipped if none. No debates.

Output contracts (R1). Prompt version "v2" (default) sends a strict JSON schema with every call, built
from the bundle (ids, subjects, tools and hypothesis ids are enums), enforced at generation by
providers that support structured outputs and ALWAYS re-validated here. Invalid items are rejected
with reasons and recorded; nothing is repaired. Prompt version "v1" reproduces the exact prompts of
the first live benchmark (no schema), for offline reconstruction from cached replies only.
"""

from __future__ import annotations

import hashlib
import json

from ..epistemic import schemas as sch
from ..epistemic.failures import classify_item, classify_problems
from ..intel.agents import ReferenceCritic, ReferenceHypothesisAgent, ReferenceScenarioDrafter
from ..validation import ALLOWED_LEVERS
from .llm import CachedProvider, record_call, run_costs

OFFLINE_TOOLS = ("store.track_record", "store.decomposition", "store.denominator_check", "store.lessons")
PROPOSABLE_TOOLS = ("provider.gdelt_sample_expand", "provider.overpass_refresh", "analyst.wikidata_override",
                    "provider.new_source_search")

# v1: the prompts of the first live benchmark (2026-11-23), kept verbatim for offline reconstruction.
RESEARCH_SYSTEM = ("You are Halibut's Research Agent. Given evidence gaps, choose tools to close them. Offline tools: "
                   f"{', '.join(OFFLINE_TOOLS)}. You may only PROPOSE: {', '.join(PROPOSABLE_TOOLS)}. "
                   'Return JSON {"actions": [{"tool", "args", "reason"}]}. Never invent data.')
HYPOTHESIS_SYSTEM = ("You are Halibut's Hypothesis Agent. Propose evidence-backed, testable competitive hypotheses. "
                     "Cite ONLY metric ids present in the bundle. Every number you write must equal a bundle value "
                     "(percent or percentage points, 2 decimals). Kinds: momentum, displacement, exposure. Levers must be "
                     "legitimate (price, product_range, product_quality, distribution, availability, service, "
                     'disclosed_advertising, financing_terms). Return JSON {"hypotheses": [{"kind", "subject", "claim", '
                     '"evidence", "confidence", "mechanism_candidates", "levers"}]}.')
CRITIC_SYSTEM = ("You are Halibut's Critic Agent. Independently challenge each hypothesis: evidence quality, denominators, "
                 "segment-mix versus within-segment effects, unobserved switching, definitions. Do not soften or merge "
                 'disagreements. Return JSON {"critiques": [{"hypothesis_id", "statement", "severity"}]}.')

# v2: explicit shapes and limits; the schema sent with the call is authoritative.
RESEARCH_SYSTEM_V2 = ("You are Halibut's Research Agent. Given evidence gaps, choose at most 6 tool actions that would close "
                      f"them. Offline tools (run now): {', '.join(OFFLINE_TOOLS)}. Tools you may only PROPOSE for approval: "
                      f"{', '.join(PROPOSABLE_TOOLS)}. Reply with a JSON object "
                      '{"actions": [{"tool": "<tool name>", "target": "<competitor id or empty string>", '
                      '"reason": "<one sentence, at most 30 words>"}]}. Use each tool at most once per target. '
                      "Never invent data.")
HYPOTHESIS_SYSTEM_V2 = ("You are Halibut's Hypothesis Agent. Propose evidence-backed, testable competitive hypotheses. Reply "
                        'with a JSON object {"hypotheses": [...]} with at most 8 items. Each item has: "kind" (momentum, '
                        'displacement or exposure); "subject" (exactly one competitor id from bundle.market.names, '
                        'copied without any prefix); "claim" (at most 80 words; every number must equal a bundle value, '
                        'in percent or percentage points with 2 decimals); "evidence" (a JSON array of 1 to 8 metric '
                        "id strings copied exactly from bundle.metrics[].id: ids only, never values, never an object); "
                        '"confidence" (low, medium or high); "mechanism_candidates" (at most 3 short phrases); "levers" '
                        "(at most 4, only: " + ", ".join(ALLOWED_LEVERS) + "). Numbers belong in the claim, not in evidence.")
CRITIC_SYSTEM_V2 = ("You are Halibut's Critic Agent. Independently challenge each hypothesis: evidence quality, denominators, "
                    "segment-mix versus within-segment effects, unobserved switching, definitions. Do not soften or merge "
                    'disagreements. Reply with a JSON object {"critiques": [{"hypothesis_id": "<copied exactly>", '
                    '"severity": "low|medium|high", "statement": "<at most 80 words>"}]}: at most 3 critiques per hypothesis.')
PROMPTS = {"v1": {"research": RESEARCH_SYSTEM, "hypothesis": HYPOTHESIS_SYSTEM, "critic": CRITIC_SYSTEM},
           "v2": {"research": RESEARCH_SYSTEM_V2, "hypothesis": HYPOTHESIS_SYSTEM_V2, "critic": CRITIC_SYSTEM_V2}}


class ReferenceAgentSet:
    critic_id = ReferenceCritic.id
    name = "reference"

    def __init__(self):
        self.h, self.c, self.d = ReferenceHypothesisAgent(), ReferenceCritic(), ReferenceScenarioDrafter()

    def propose(self, ctx):
        return self.h.propose(ctx), {}

    def critique(self, hyps, ctx):
        return {h["id"]: self.c.critique(h, ctx) for h in hyps}

    def draft(self, h, ctx):
        return self.d.draft(h, ctx)

    def costs(self):
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0, "cache_hit_rate": None,
                "duplicate_tool_calls": 0, "latency_seconds": 0.0, "research": None}


def evidence_bundle(ctx) -> dict:
    m, tx, audit = ctx["market"], ctx["signals"]["transactions"], ctx.get("audit", {})
    metrics = [{"id": x["id"], "name": x["name"], "subject": x["subject"], "scope": x["scope"],
                "value_pct": None if x["value"] is None else round(100 * x["value"], 4)} for x in tx.get("metrics", [])]
    comps = {cid: {k: (round(100 * v, 4) if isinstance(v, float) and k != "registrations_L4" else v)
                   for k, v in e.items() if k in ("share_P4", "share_L4", "segment_share_P4", "segment_share_L4",
                                                    "segment_mix_L4", "registrations_L4")}
             for cid, e in tx.get("competitors", {}).items() if e.get("available")}
    dec = {c: {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items() if k != "segment_units"}
           for c, d in audit.get("decomposition", {}).items()}
    return {"market": {"id": m.id, "mode": m.mode, "focal": m.focal, "target": m.target, "segment": tx.get("segment"),
                       "as_of": m.as_of, "names": {c.id: c.name for c in m.competitors},
                       "market_segment_share_pct": {"P4": _pct(tx.get("segment_share_P4")), "L4": _pct(tx.get("segment_share_L4"))}},
            "windows": {"L4": tx.get("L4"), "P4": tx.get("P4")}, "metrics": metrics, "competitors": comps,
            "audit": {"checks": [{k: c[k] for k in ("check", "pass")} for c in audit.get("checks", [])],
                      "decomposition": dec, "displacement": audit.get("displacement", {})},
            "track_record": ctx.get("track_record", {}),
            "lessons": [{"id": k["knowledge_id"], "status": k["status"], "statement": k["statement"][:300]}
                        for k in ctx.get("lessons", [])][:10]}


def _pct(x):
    return None if x is None else round(100 * x, 4)


def find_gaps(ctx) -> list[dict]:
    gaps = []
    s = ctx["signals"]
    if not s.get("media", {}).get("available") or s["media"].get("total_matched", 0) < 20:
        gaps.append({"gap": "media_evidence_thin", "detail": s.get("media", {}).get("total_matched", 0)})
    if not s.get("footprint", {}).get("available"):
        gaps.append({"gap": "footprint_unavailable_as_of"})
    if ctx.get("unresolved"):
        gaps.append({"gap": "unresolved_entities", "detail": ctx["unresolved"]})
    if not ctx.get("track_record"):
        gaps.append({"gap": "no_track_record"})
    if ctx.get("audit", {}).get("available") and not ctx["audit"].get("all_passed"):
        gaps.append({"gap": "audit_failures"})
    gaps.append({"gap": "segment_mix_vs_within_unclear"})
    return gaps


class LLMAgentSet:
    name = "llm"
    research_id, hypothesis_id, critic_id = "agent:llm_research:v1", "agent:llm_hypotheses:v1", "agent:llm_critic:v1"

    def __init__(self, provider, store, run_id, faults: bool = False, prompt_version: str = "v2"):
        if prompt_version not in PROMPTS:
            raise ValueError(f"unknown prompt version {prompt_version!r}")
        self.provider = CachedProvider(provider, store)
        self.store, self.run_id, self.faults = store, run_id, faults
        self.prompt_version, self.prompts, self.strict = prompt_version, PROMPTS[prompt_version], prompt_version != "v1"
        self.drafter = ReferenceScenarioDrafter()
        self.research_result = None
        self.call_rejections = []  # top-level schema failures of whole replies

    def _check(self, r, schema, key, role):
        """Split a reply into valid and rejected items under ``schema`` (strict mode only). Returns
        (items, rejected [(i, item, problems)], outcome)."""
        if r.data is None:
            return [], [], "ok"  # record_call types truncation/refusal/invalid JSON itself
        if not self.strict:
            items = r.data.get(key, []) if isinstance(r.data, dict) else []
            return (items if isinstance(items, list) else []), [], "ok"
        valid, rejected, top = sch.split_items(r.data, schema, key)
        if top:
            self.call_rejections.append({"role": role, "problems": top, "reply": r.data})
            return [], [], "schema_invalid: " + top[0][:150]
        return valid, rejected, "ok"

    def _call(self, role, agent, system, payload, schema=None):
        user = "BUNDLE:" + json.dumps(payload, sort_keys=True, default=str)
        ph = hashlib.sha256((system + user).encode()).hexdigest()[:16]
        try:
            r = self.provider.complete(system, user, role=role, run_id=self.run_id,
                                       schema=schema if self.strict else None)
        except Exception as exc:
            record_call(self.store, self.run_id, agent, role, None, ph, outcome=f"error: {exc}"[:200],
                        provider=self.provider.name, model=self.provider.model)
            raise
        return r, ph

    def research(self, ctx, bundle) -> dict:
        gaps = find_gaps(ctx)
        if not gaps:
            return {"invoked": False}
        schema = sch.research_schema(OFFLINE_TOOLS, PROPOSABLE_TOOLS)
        r, ph = self._call("research", self.research_id, self.prompts["research"], {"gaps": gaps, "market": bundle["market"]},
                           schema)
        actions, rejected, outcome = self._check(r, schema, "actions", "research")
        self.research_rejected = [{"subject": "research_action", "item": it, "problems": pr,
                                   "failure_class": classify_problems(pr)} for _, it, pr in rejected]
        seen, executed, pending, dup = set(), [], [], 0
        for a in actions:
            key = (a.get("tool"), a.get("target") if "target" in a else json.dumps(a.get("args", {}), sort_keys=True))
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            if a.get("tool") in OFFLINE_TOOLS:
                executed.append({**a, "result": self._tool(a["tool"], ctx)})
            elif a.get("tool") in PROPOSABLE_TOOLS:
                pending.append({**a, "status": "pending_approval"})
            else:
                pending.append({**a, "status": "rejected_unknown_tool"})
        record_call(self.store, self.run_id, self.research_id, "research", r, ph, tool_calls=[{"tool": t["tool"]} for t in executed + pending],
                    duplicates=dup, outcome=outcome)
        self.research_result = {"invoked": True, "gaps": gaps, "executed": executed, "pending": pending, "duplicates": dup}
        return self.research_result

    def _tool(self, tool, ctx):
        if tool == "store.track_record":
            return ctx.get("track_record") or "no scored predictions available as of this date"
        if tool == "store.decomposition":
            return {c: {k: round(v, 3) for k, v in d.items() if k.endswith("_pp")}
                    for c, d in ctx.get("audit", {}).get("decomposition", {}).items()}
        if tool == "store.denominator_check":
            return [c for c in ctx.get("audit", {}).get("checks", []) if c["check"] == "consistent_denominators"]
        return [k["knowledge_id"] for k in ctx.get("lessons", [])]

    def propose(self, ctx):
        bundle = evidence_bundle(ctx)
        res = self.research(ctx, bundle)
        if res.get("executed"):
            bundle["research"] = [{"tool": a["tool"], "result": a["result"]} for a in res["executed"]]
        if self.faults:
            bundle["mock_faults"] = True
        subjects = sorted(bundle["market"]["names"])
        metric_ids = [m["id"] for m in bundle["metrics"]]
        schema = sch.hypothesis_schema(metric_ids, subjects, ALLOWED_LEVERS)
        r, ph = self._call("hypothesis", self.hypothesis_id, self.prompts["hypothesis"], bundle, schema)
        items, rejected, outcome = self._check(r, schema, "hypotheses", "hypothesis")
        record_call(self.store, self.run_id, self.hypothesis_id, "hypothesis", r, ph, outcome=outcome)
        schema_rejected = [{"id": "rejected:" + hashlib.sha256(json.dumps([self.run_id, i, it], sort_keys=True,
                                                                          default=str).encode()).hexdigest()[:16],
                            "claim": str(it.get("claim", ""))[:300] if isinstance(it, dict) else "",
                            "produced_by": self.hypothesis_id, "agent_item": it, "evidence": [],
                            "failure_class": classify_problems(pr, it, subjects=subjects),
                            "validation": {"passed": False, "problems": pr, "stage": "schema"}} for i, it, pr in rejected]
        hyps, dropped, contract = [], [], []
        for item in items:
            h = to_hypothesis(item, ctx, self.hypothesis_id) if isinstance(item, dict) else None
            if not self.strict:  # legacy replies: how would the R1 contract have judged this item? (diagnostic only)
                contract.append({"item": item, "problems": sch.validate(item, schema["properties"]["hypotheses"]["items"])})
            if h is None:
                # conversion needs a supported kind and subject: those causes rank first for a drop
                found = classify_item(item, subjects=subjects, kinds=sch.KINDS)
                cause = [c for c in found if c in ("format.subject_not_canonical", "unsupported.kind_or_subject")]
                classes = (cause + [c for c in found if c not in cause]) or ["unsupported.missing_evidence_signals"]
                dropped.append({"item": item, "failure_class": classes[0], "problems": [
                    f"not convertible into a testable prediction: {', '.join(classes)}"]})
                continue
            h["agent_item"] = item
            hyps.append(h)
        return hyps, {"dropped": dropped, "schema_rejected": schema_rejected, "contract_diagnostic": contract,
                      "call_rejections": list(self.call_rejections), "research_rejected": getattr(self, "research_rejected", []),
                      "prompt_version": self.prompt_version, "schema_version": sch.SCHEMA_VERSIONS["hypothesis"]}

    def critique(self, hyps, ctx):
        if not hyps:
            return {}
        payload = {"hypotheses": [{"id": h["id"], "mode": h["mode"], "claim": h["claim"], "subjects": h["subjects"],
                                   "evidence": h["evidence"][:12]} for h in hyps], **evidence_bundle(ctx)}
        schema = sch.critic_schema([h["id"] for h in hyps])
        r, ph = self._call("critic", self.critic_id, self.prompts["critic"], payload, schema)
        items, rejected, outcome = self._check(r, schema, "critiques", "critic")
        record_call(self.store, self.run_id, self.critic_id, "critic", r, ph, outcome=outcome)
        self.critic_rejected = [{"subject": "critique", "item": it, "problems": pr, "failure_class": classify_problems(pr)}
                                for _, it, pr in rejected]
        out = {}
        for c in items:
            out.setdefault(c.get("hypothesis_id"), []).append({"target": c.get("hypothesis_id"), "relation": "critiques",
                                                               "statement": c.get("statement", ""), "severity": c.get("severity"),
                                                               "produced_by": self.critic_id})
        return out

    def draft(self, h, ctx):
        return self.drafter.draft(h, ctx)

    def costs(self):
        c = run_costs(self.store, self.run_id)
        c["cache"] = {"hits": self.provider.hits, "misses": self.provider.misses}
        c["research"] = self.research_result
        return c


def to_hypothesis(item: dict, ctx: dict, agent_id: str) -> dict | None:
    """Convert an agent's JSON hypothesis into Halibut's schema. The PREDICTION is built from engine
    metrics (never from agent numbers); unsupported kinds are dropped."""
    from ..data.providers.base import stable_id  # noqa: PLC0415
    from ..intel.agents import _resolve_date  # noqa: PLC0415
    m, tx = ctx["market"], ctx["signals"]["transactions"]
    comps = tx.get("competitors", {})
    kind, subj = item.get("kind"), item.get("subject")
    resolve = _resolve_date(m.as_of)
    if kind == "momentum" and comps.get(subj, {}).get("available"):
        e = comps[subj]
        up = e["share_L4"] > e["share_P4"]
        pred = {"metric": f"registration_share:{subj}", "direction": "above" if up else "below", "threshold": e["share_P4"],
                "periods": "next 2 quarters", "resolvable_after": resolve}
        mode, subjects = ("enforcement" if up else "continuation"), [e["brand"]]
    elif kind == "displacement" and m.focal and m.target:
        f, t = comps.get(m.focal, {}), comps.get(m.target, {})
        if not (f.get("available") and t.get("available")):
            return None
        pattern = f["segment_share_L4"] > f["segment_share_P4"] and t["segment_share_L4"] < t["segment_share_P4"]
        pred = {"metric": f"segment_share_gap:{m.focal}-{m.target}", "direction": "narrowing" if pattern else "unchanged",
                "periods": "next 2 quarters", "resolvable_after": resolve}
        mode, subjects = "annihilation", [f["brand"], t["brand"]]
    elif kind == "exposure" and tx.get("segment_share_P4") is not None:
        pred = {"metric": "segment_share_of_market", "direction": "above", "threshold": tx["segment_share_P4"],
                "periods": "next 2 quarters", "resolvable_after": resolve}
        # label only (does not affect validation or the prediction): never double-prefix ("brand:brand:x")
        label = comps.get(subj, {}).get("brand") or (subj if str(subj).startswith("brand:") else f"brand:{subj}")
        mode, subjects = "continuation", [label]
    else:
        return None
    from ..intel.agents import empirical_confidence  # noqa: PLC0415
    confidence, basis = empirical_confidence(ctx.get("track_record", {}), mode, item.get("confidence", "low"))
    return {"id": "hyp:" + stable_id(ctx["run_id"], agent_id, item.get("claim", "")), "mode": mode,
            "claim": item.get("claim", ""), "subjects": subjects, "evidence": list(item.get("evidence", [])),
            "prediction": pred, "confidence": confidence, "confidence_basis": basis, "agent_confidence": item.get("confidence"), "rationale": f"LLM agent ({kind})",
            "levers": list(item.get("levers", [])), "mechanism_candidates": list(item.get("mechanism_candidates", [])),
            "uncertainties": ["First UK registrations include imported used vehicles (DfT definition).",
                              "Brand-to-brand switching is not observed."],
            "status": "proposed", "epistemic_status": "hypothesis", "produced_by": agent_id, "origin": "template_selection",
            "narrative_numbers_check": True, "lessons_considered": [k["knowledge_id"] for k in ctx.get("lessons", [])]}
