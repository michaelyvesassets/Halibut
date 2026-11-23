"""Validation / Safety Engine.

Checks applied to every agent output before it is recorded or reported:
- evidence: every cited id exists and was available at the run's as-of date;
- metrics: every cited share metric is RECOMPUTED from its definition and must
  match (tolerance 1e-9); a metric without a definition is rejected;
- epistemics: hypotheses are 'proposed' and 'hypothesis'-class; they may not
  claim observed status; predictions must name a recomputable metric, a
  direction and a resolution date after the as-of date;
- strategy safety: levers must come from ALLOWED_LEVERS (legitimate
  competition: price, product, distribution, availability, service,
  disclosed advertising). Text mentioning prohibited tactics (fake reviews,
  impersonation, bots, astroturfing, sabotage, misinformation, unauthorised
  outreach, collusion) is rejected outright;
- action policy: agents are read-only; no outbound action types exist.
"""

from __future__ import annotations

import math
import re

ALLOWED_LEVERS = ("price", "product_range", "product_quality", "distribution", "availability", "service",
                  "disclosed_advertising", "financing_terms", "charging_or_support_infrastructure")
PROHIBITED = re.compile(r"fake review|astroturf|impersonat|sockpuppet|bot (army|network)|engagement bot|"
                        r"misinformation|disinformation|sabotag|smear|price.?fixing|collu|bribe|hack|dox|"
                        r"unsolicited outreach|spam", re.I)
ACTION_TYPES_ALLOWED = ("read_evidence", "read_knowledge", "propose_hypothesis", "propose_scenario",
                        "submit_critique", "submit_question", "propose_experiment")


def check_action(action_type: str) -> None:
    if action_type not in ACTION_TYPES_ALLOWED:
        raise PermissionError(f"action {action_type!r} is not permitted for agents (read-only, propose-only)")


def validate_hypothesis(h: dict, retriever, metrics: dict[str, dict], as_of: str) -> list[str]:
    from .intel.signals import compute_share  # noqa: PLC0415
    problems = []
    for key in ("id", "mode", "claim", "evidence", "prediction", "confidence", "status", "epistemic_status", "produced_by"):
        if key not in h:
            problems.append(f"missing field {key!r}")
    if problems:
        return problems
    if h["status"] != "proposed" or h["epistemic_status"] != "hypothesis":
        problems.append("hypotheses must be status 'proposed' and epistemic_status 'hypothesis'")
    if h["mode"] not in ("enforcement", "continuation", "annihilation", "diagnostic"):
        problems.append(f"unknown mode {h['mode']!r}")
    if not h["evidence"]:
        problems.append("no evidence cited")
    for ev in h["evidence"]:
        if ev.startswith("metric:"):
            m = metrics.get(ev)
            if m is None or "definition" not in m:
                problems.append(f"{ev}: metric not found or has no definition")
                continue
            if m["definition"]["kind"] == "share":
                v = compute_share(retriever, m["definition"])
                if v is None or m["value"] is None or not math.isclose(v, m["value"], rel_tol=1e-9, abs_tol=1e-12):
                    problems.append(f"{ev}: recomputed value {v} does not match reported {m['value']}")
            for inp in m.get("inputs", [])[:200]:
                if not retriever.exists(inp):
                    problems.append(f"{ev}: input {inp} not available at {as_of}")
                    break
        elif not retriever.exists(ev):
            problems.append(f"evidence {ev} does not exist or was not available at {as_of}")
    p = h["prediction"]
    if not isinstance(p, dict) or not {"metric", "direction", "resolvable_after"} <= set(p):
        problems.append("prediction needs metric, direction and resolvable_after")
    elif p["resolvable_after"] <= as_of[:10]:
        problems.append("prediction must resolve after the as-of date (no hindsight)")
    for lever in h.get("levers", []):
        if lever not in ALLOWED_LEVERS:
            problems.append(f"lever {lever!r} is not an allowed legitimate competitive lever")
    text = " ".join(str(h.get(k, "")) for k in ("claim", "mechanism_candidates", "levers", "rationale"))
    if PROHIBITED.search(text):
        problems.append("prohibited tactic mentioned: Halibut does not support deceptive or unlawful influence")
    return problems
