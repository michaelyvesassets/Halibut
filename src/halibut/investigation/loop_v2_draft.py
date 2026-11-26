"""DRAFT of the next strategist prompt and schema version (``loop.v2-draft``). PREPARED, NOT ACTIVE.

Nothing in the running loop imports this module: the loop still uses ``loop.v1`` prompts and schemas, so the requests
recorded for Step 1 cache reuse are unchanged (tested). Activating it needs founder approval, a new prompt version,
new pre-registered criteria and a fresh (non-reused) run.

Changes prepared (founder direction of 2026-11-26):
1. Pairwise targets: ``intended_effect.entity_b`` (the comparator company) is required for ``segment_share_gap`` and
   must be empty otherwise.
2. Objective and success-measure agreement: at least one success criterion must measure the intended effect's
   quantity (template, entity, entity_b), and its comparator must point in the intended direction.
3. Numerical claims: every number in the option's narrative must be declared in ``numeric_claims`` with its unit,
   rounding precision and kind:
   - ``observed``: one evidence metric, reproduced EXACTLY at the stated precision in the stated unit;
   - ``calculated``: a deterministic difference or ratio of two evidence metrics, recomputed and matched exactly;
   - ``assumed``: a scenario assumption. It cites no metric, and must equal one of the option's assumed effect
     magnitudes.
   Closeness to a real number is never enough: a value that does not reproduce exactly at its own precision, or a
   number that is not declared, is rejected. A unit-less number (for example "0.12 share") is a number and must be
   declared (loop.v1 does not check it).
4. Provenance: the kind field separates observed measurements, deterministically calculated values and hypothetical
   scenario assumptions.
"""

from __future__ import annotations

import copy
import re

from ..agents.loop_agents import SYSTEMS
from ..epistemic import schemas as sch

ACTIVE = False
PROMPT_VERSION_DRAFT = "loop.v2-draft"
UNITS = ("percent", "pp", "fraction", "count")
KINDS = ("observed", "calculated", "assumed")
OPERATIONS = ("value", "difference", "ratio", "none")
PAIRWISE_TEMPLATES = ("segment_share_gap",)
INCREASE, DECREASE = ("increase_vs_L4", "above_reference"), ("decrease_vs_L4", "below_reference")
ANY_NUMBER = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?")

STRATEGIST_SYSTEM_V2_DRAFT = SYSTEMS["strategist"] + (
    " PAIRWISE TARGETS: an intended effect on a gap between two companies must name both (entity and entity_b); "
    "otherwise entity_b is empty. AGREEMENT: at least one success criterion must measure exactly the intended effect "
    "(same metric, entity and entity_b) and move in the intended direction. NUMBERS: declare every number you write in "
    "numeric_claims with its unit (percent, pp, fraction or count), the number of decimals as written, and its kind: "
    "observed (copied from one evidence metric), calculated (difference or ratio of two evidence metrics) or assumed "
    "(a scenario assumption, equal to one of your assumed effect magnitudes). Never present an assumption as observed.")


def strategist_schema_v2(refs, entities, metric_ids, levers, modes) -> dict:
    s = copy.deepcopy(sch.strategist_schema(refs, entities, metric_ids, levers, modes))
    opt = s["properties"]["options"]["items"]
    ents = sorted(set(entities))
    opt["properties"]["intended_effect"]["properties"]["entity_b"] = sch._enum(ents + [""])
    opt["properties"]["intended_effect"]["required"].append("entity_b")
    claim = sch._obj({"text": sch._s(24), "value": {"type": "number"}, "unit": sch._enum(UNITS), "kind": sch._enum(KINDS),
                      "decimals": {"type": "integer", "enum": [0, 1, 2, 3, 4]}, "operation": sch._enum(OPERATIONS),
                      "metric_ids": sch._arr(sch._enum(metric_ids), 2)})
    opt["properties"]["numeric_claims"] = sch._arr(claim, 12)
    opt["required"].append("numeric_claims")
    return s


def check_pairwise(effect: dict, entities) -> list[str]:
    b = effect.get("entity_b", "")
    if effect["template"] in PAIRWISE_TEMPLATES:
        if not b:
            return [f"intended effect on {effect['template']} needs entity_b (the comparator company)"]
        if b == effect["entity"] or b not in entities:
            return [f"intended effect entity_b {b!r} must be another competitor"]
    elif b:
        return [f"entity_b is only used by pairwise templates {PAIRWISE_TEMPLATES}"]
    return []


def check_objective_agreement(option: dict) -> list[str]:
    e = option["intended_effect"]
    q = (e["template"], e["entity"], e.get("entity_b", "") or "")
    same = [c for c in option["success_criteria"] if (c["template"], c["entity"], c.get("entity_b", "") or "") == q]
    if not same:
        return ["no success criterion measures the intended effect (same metric, entity and entity_b)"]
    want = INCREASE if e["direction"] == "increase" else DECREASE
    if not any(c["comparator"] in want for c in same):
        return [f"every success criterion on the intended quantity contradicts the intended direction ({e['direction']})"]
    return []


def _in_unit(fraction: float, unit: str) -> float:
    return fraction * 100 if unit in ("percent", "pp") else fraction


def check_numeric_claims(option: dict, metric_values: dict) -> list[str]:
    """``metric_values``: evidence metric id -> value as a FRACTION (as stored). Exact reproduction at stated precision."""
    p, e = [], option["intended_effect"]
    claims = option.get("numeric_claims", [])
    declared = {c["text"] for c in claims}
    text = " ".join([option["title"], e["basis"]] + [a["statement"] for a in option["assumptions"]] + option["risks"])
    for tok in ANY_NUMBER.findall(text):
        if tok not in declared:
            p.append(f"number {tok} in the narrative is not declared in numeric_claims")
    for c in claims:
        written = c["text"].lstrip("+")
        dec = len(written.split(".")[1]) if "." in written else 0
        if dec != c["decimals"] or abs(float(written) - c["value"]) > 1e-12:
            p.append(f"numeric claim {c['text']!r}: value or decimals differ from the text as written")
            continue
        ids = c["metric_ids"]
        if c["kind"] == "assumed":
            if ids or c["unit"] != "pp" or c["value"] not in (e["magnitude_low_pp"], e["magnitude_high_pp"]):
                p.append(f"assumed claim {c['text']!r} must cite no metric and equal an assumed magnitude (pp)")
            continue
        if any(i not in metric_values for i in ids):
            p.append(f"claim {c['text']!r} cites a metric that is not in the evidence")
            continue
        if c["kind"] == "observed" and (c["operation"] != "value" or len(ids) != 1 or c["unit"] not in ("percent", "fraction")):
            p.append(f"observed claim {c['text']!r} must be the value of one metric in percent or fraction")
            continue
        if c["kind"] == "calculated" and (c["operation"] not in ("difference", "ratio") or len(ids) != 2):
            p.append(f"calculated claim {c['text']!r} needs a difference or ratio of two metrics")
            continue
        if c["kind"] == "observed":
            true = _in_unit(metric_values[ids[0]], c["unit"])
        elif c["operation"] == "difference":
            if c["unit"] != "pp":
                p.append(f"calculated difference {c['text']!r} must be in pp")
                continue
            true = (metric_values[ids[0]] - metric_values[ids[1]]) * 100
        else:
            if metric_values[ids[1]] == 0:
                p.append(f"calculated ratio {c['text']!r} divides by zero")
                continue
            true = metric_values[ids[0]] / metric_values[ids[1]] * (100 if c["unit"] == "percent" else 1)
        if round(true, c["decimals"]) != round(c["value"], c["decimals"]):
            p.append(f"claim {c['text']!r} does not reproduce: {c['kind']} value is {true:.{c['decimals'] + 2}f} {c['unit']}")
    return p


def validate_option_v2(option: dict, entities, metric_values: dict) -> list[str]:
    return (check_pairwise(option["intended_effect"], entities) + check_objective_agreement(option)
            + check_numeric_claims(option, metric_values))
