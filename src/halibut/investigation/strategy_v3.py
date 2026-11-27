"""Strategist contract ``strategy.v3`` (Architecture V2, milestone 3): the model proposes strategies and mechanisms;
deterministic tools calculate and verify every quantity.

It is selected with ``run_investigation(..., contract="loop.v2", strategist="v3")``. The ``loop.v2`` generator and
critic are unchanged, and ``loop.v1``, ``loop.v2`` and the frozen Step 2 benchmark are untouched.

**Rules:**
- **Quantity catalogue (engine).** Every number the strategist may use is calculated before the call and has a stable
  id with provenance:
  - ``q:obs:…``: an observed evidence metric;
  - ``q:diff:…``: an L4−P4 change in pp;
  - ``q:gap:…``: a pairwise L4 gap in pp;
  - ``q:compl:…``: 100 − segment mix;
  - ``q:base:…``: an official baseline forecast from a registered model.
- **No digits in free text.** A number appears in text only as a reference ``{q:…}`` to a catalogue id, which the
  engine renders. Assumed magnitudes go only in the numeric fields ``magnitude_low_pp`` and ``magnitude_high_pp`` with
  a structured assumption (justification, basis, uncertainty). Cost numbers go only in the numeric cost fields.
- **Calculation requests.** The strategist may request a missing calculation (difference, ratio or sum of catalogue
  or metric quantities) instead of inventing a value. The engine computes supported requests; unsupported ones become
  typed ``calculation_unavailable`` records. The strategist never receives an invented number.
- **Alignment.**
  - **Objective = intended effect** (same quantity and direction).
  - **Actions** use registered levers.
  - **Success criteria:** at least one measures the objective in the intended direction.
  - **Actor responses** each carry a justification.
- **Forecastable subclaims.** Each option states subclaims in the measurement grammar. They are compiled into
  registered tests and registered in the prediction ledger, which resolves them automatically.
- **Scenarios.** The engine computes the baseline (no action: the official baseline forecast) and alternative scenarios
  (no competitor response, and each response) as **simulated** quantities, conditional and uncalibrated.
- **Labels.** Observed, calculated, model (baseline), assumed and simulated quantities are always labelled.
- **Every enforced schema limit is stated to the model** (``describe_limits``).
- **Validity is not effectiveness.** A structurally valid option is a well-formed proposal, not evidence that the
  strategy works. Experiment eligibility rules are unchanged.
"""

from __future__ import annotations

import copy
import json
import re

from ..agents.loop_agents import SYSTEMS as V1_SYSTEMS
from ..epistemic import schemas as sch
from . import loop_v2 as v2

CONTRACT = "strategy.v3"
SCHEMA_VERSION = "strategy_options.v3"
REF = re.compile(r"\{(q:[A-Za-z0-9_:.\-]+)\}")
DIGIT = re.compile(r"\d")
OPS = ("difference", "ratio", "sum")
ASSUMPTION_BASES = ("analogy", "expert_judgement", "evidence_ratio", "unknown")
UNCERTAINTY = ("low", "medium", "high")


# ------------------------------------------------------------------------------------------------ the catalogue
def catalogue(metrics: dict, baselines: dict, entities, focal=None, target=None) -> dict:
    """{id: {kind, value, unit, definition, inputs}}; values are in percent (pp for changes and gaps)."""
    cat = {}
    for mid, m in metrics.items():
        qid = "q:obs:" + mid.split(":", 1)[1]
        cat[qid] = {"kind": "observed", "value": 100 * m["value"], "unit": "percent", "definition": f"evidence {mid}",
                    "inputs": [mid]}
    for mid, m in metrics.items():
        parts = mid.split(":")
        if len(parts) >= 3 and parts[2] == "L4":
            p4 = mid.replace(":L4", ":P4", 1)
            if p4 in metrics:
                rest = ":".join([parts[1]] + parts[3:])
                cat[f"q:diff:{rest}"] = {"kind": "calculated", "value": 100 * (m["value"] - metrics[p4]["value"]), "unit": "pp",
                                         "definition": f"{mid} - {p4}", "inputs": [mid, p4]}
            if parts[1] == "segment_mix":
                cat[f"q:compl:segment_mix:L4:{parts[3]}"] = {"kind": "calculated", "value": 100 * (1 - m["value"]),
                                                             "unit": "percent", "definition": f"100 - {mid}", "inputs": [mid]}
    pairs = [(a, b) for a in (focal, target) if a for b in entities if b != a]
    for a, b in pairs:
        ma, mb = metrics.get(f"metric:segment_share:L4:{a}"), metrics.get(f"metric:segment_share:L4:{b}")
        if ma and mb:
            cat[f"q:gap:segment_share:L4:{a}:{b}"] = {"kind": "calculated", "value": 100 * (ma["value"] - mb["value"]),
                                                      "unit": "pp", "definition": f"segment_share L4 {a} - {b}",
                                                      "inputs": [ma["id"], mb["id"]]}
    for (tmpl, ent, *rest), b in baselines.items():
        if b.get("point") is None:
            continue
        cat[f"q:base:{tmpl}:{ent}"] = {"kind": "model", "value": 100 * b["point"], "unit": "percent",
                                       "definition": f"official baseline {b.get('model')} ({b.get('status')}) for the next two "
                                                     "quarters", "inputs": [b.get("model_version_id")],
                                       "interval80": [100 * x for x in b["interval80"]]}
    return cat


LEGEND = {"q:obs:<template>:<window>:<entity>": "observed evidence metric, percent",
          "q:diff:<template>:<entity>": "calculated: L4 minus P4, percentage points",
          "q:gap:segment_share:L4:<a>:<b>": "calculated: segment share of a minus b in L4, percentage points",
          "q:compl:segment_mix:L4:<entity>": "calculated: 100 minus segment mix, percent (non-segment part of the brand)",
          "q:base:<template>:<entity>": "model: official baseline forecast for the next two quarters, percent [point, 80% low, 80% high]"}


def catalogue_view(cat: dict) -> dict:
    """What the model sees: a legend once, then [id, value] rows (ids encode kind and definition; nothing is dropped)."""
    rows = []
    for k, v in sorted(cat.items()):
        rows.append([k, round(v["value"], 2)] + ([[round(x, 2) for x in v["interval80"]]] if "interval80" in v else []))
    return {"legend": LEGEND, "quantities": rows}


# ------------------------------------------------------------------------------------------------ the schema
def schema(refs, entities, metric_ids, levers, modes, cat_ids, extended: bool) -> dict:
    base = v2.strategist_schema(refs, entities, metric_ids, levers, modes, extended)
    s = copy.deepcopy(base)
    opt = s["properties"]["options"]["items"]
    props = opt["properties"]
    props.pop("numeric_claims")
    opt["required"].remove("numeric_claims")
    ents = sorted(set(entities))
    tmpl = sch.TEMPLATES + (v2.EXTENDED_TEMPLATES if extended else ())
    # quantity ids are NOT an enum (it would repeat the whole catalogue in the schema): references are checked locally
    # against the catalogue by validate(), so an unknown id is rejected all the same
    qid = sch._s(80)
    props["intended_effect"]["properties"]["assumption"] = sch._obj({
        "justification": sch._s(300), "basis": sch._enum(ASSUMPTION_BASES), "uncertainty": sch._enum(UNCERTAINTY)})
    props["intended_effect"]["required"].append("assumption")
    props["objective"] = sch._obj({"template": sch._enum(tmpl), "entity": sch._enum(ents + ["market"]),
                                   "entity_b": sch._enum(ents + [""]), "direction": sch._enum(("increase", "decrease"))})
    props["actions"] = sch._arr(sch._obj({"lever": sch._enum(levers), "description": sch._s(240)}), 4, 1)
    props["rationale"] = sch._s(600)
    props["quantity_refs"] = sch._arr(qid, 8, 1)
    props["assumptions"] = sch._arr(sch._obj({"statement": sch._s(240), "kind": sch._enum(sch.ASSUMPTION_KINDS),
                                              "justification": sch._s(240), "uncertainty": sch._enum(UNCERTAINTY)}), 5)
    resp = props["competitor_responses"]["items"]
    resp["properties"]["justification"] = sch._s(240)
    resp["required"].append("justification")
    props["subclaims"] = sch._arr(sch._obj({"template": sch._enum(tmpl), "entity": sch._enum(ents + ["market"]),
                                            "entity_b": sch._enum(ents + [""]), "comparator": sch._enum(v2.COMPARATORS_V2),
                                            "reference_metric": {"$ref": "#/$defs/metric_id_or_empty"},
                                            "rationale": sch._s(240)}), 3, 1)
    props["calculation_requests"] = sch._arr(sch._obj({"operation": sch._enum(OPS), "inputs": sch._arr(qid, 2, 2),
                                                       "purpose": sch._s(200)}), 3)
    props["limitations"] = sch._arr(sch._s(200), 3)
    opt["required"] += ["objective", "actions", "rationale", "quantity_refs", "subclaims", "calculation_requests",
                        "limitations"]
    if "metric_id_or_empty" not in s["$defs"]:
        s["$defs"]["metric_id_or_empty"] = sch._enum(list(metric_ids) + [""])
    return s


def describe_limits(schema_: dict) -> str:
    """Every enforced limit (maxLength, maxItems, minItems) in words: the API does not show them to the model."""
    out = []

    def walk(x, path):
        if not isinstance(x, dict):
            return
        if "maxLength" in x:
            out.append(f"{path}: at most {x['maxLength']} characters")
        if x.get("type") == "array":
            lim = []
            if "minItems" in x:
                lim.append(f"at least {x['minItems']}")
            if "maxItems" in x:
                lim.append(f"at most {x['maxItems']}")
            if lim:
                out.append(f"{path}: {' and '.join(lim)} items")
            walk(x.get("items"), path + "[]")
        for k, v in (x.get("properties") or {}).items():
            walk(v, f"{path}.{k}" if path else k)
    walk(v2.inline(schema_), "")
    return "LIMITS (enforced; a reply that exceeds one is rejected): " + "; ".join(out) + "."


SYSTEM = V1_SYSTEMS["strategist"].split(" Every number in text")[0] + (
    " NUMBERS ARE COMPUTED BY HALIBUT, NOT BY YOU. Never write a digit in any text field. To use a number, cite a "
    "quantity from the catalogue in braces, for example {q:obs:segment_share:L4:<entity>}; Halibut renders its value. "
    "Assumed effect sizes go ONLY in magnitude_low_pp and magnitude_high_pp, with an assumption (justification, basis, "
    "uncertainty). If a number you need is not in the catalogue, request it in calculation_requests (difference, ratio "
    "or sum of catalogue quantities) instead of estimating it. ALIGNMENT: the objective must be the intended effect (same "
    "metric, entity, entity_b and direction); at least one success criterion must measure it in that direction; each "
    "action uses one listed lever; justify each competitor response. Give up to three FORECASTABLE subclaims in the "
    "measurement grammar that would show whether the option's logic holds. List limitations. Options are proposals for "
    "human review; a valid option is not evidence that a strategy works. Reply with a JSON object matching the schema.")


def system_prompt(schema_: dict) -> str:
    return SYSTEM + " " + describe_limits(schema_)


# ------------------------------------------------------------------------------------------------ validation
_TEXT_KEYS = {"title", "basis", "statement", "justification", "description", "rationale", "purpose"}


def texts(option: dict) -> list[tuple[str, str]]:
    out = []

    def walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(x, list):
            for v in x:
                walk(v, path + "[]")
        elif isinstance(x, str) and (path.rsplit(".", 1)[-1].rstrip("[]") in _TEXT_KEYS or path.startswith(("risks", "limitations"))):
            out.append((path, x))
    walk(option, "")
    return out


def validate(option: dict, cat: dict, entities) -> list[str]:
    """Contract rules beyond the schema (numbers, references, alignment, assumptions). Empty list = compliant."""
    p = []
    for path, t in texts(option):
        for r in REF.findall(t):
            if r not in cat:
                p.append(f"{path}: reference {{{r}}} is not in the quantity catalogue")
        if DIGIT.search(REF.sub("", t)):
            p.append(f"{path}: a digit in free text; cite a catalogue quantity or use a numeric field")
    for r in option.get("quantity_refs", []):
        if r not in cat:
            p.append(f"quantity_refs: {r} is not in the catalogue")
    e, obj = option["intended_effect"], option["objective"]
    if (obj["template"], obj["entity"], obj.get("entity_b") or "", obj["direction"]) != \
            (e["template"], e["entity"], e.get("entity_b") or "", e["direction"]):
        p.append("objective and intended effect differ (metric, entity, entity_b or direction)")
    p += v2.check_pairwise(e, entities) + v2.check_objective_agreement(option)
    a = e.get("assumption") or {}
    if not (a.get("justification") or "").strip():
        p.append("intended_effect.assumption needs a justification")
    for i, x in enumerate(option.get("assumptions", [])):
        if not (x.get("justification") or "").strip():
            p.append(f"assumptions[{i}] needs a justification")
    for i, x in enumerate(option.get("competitor_responses", [])):
        if not (x.get("justification") or "").strip():
            p.append(f"competitor_responses[{i}] needs a justification")
    levers = {x["lever"] for x in option.get("actions", [])}
    if not levers <= set(option["levers"]):
        p.append(f"actions use levers {sorted(levers - set(option['levers']))} that are not the option's levers")
    return p


def run_calculations(option: dict, cat: dict) -> list[dict]:
    """Engine-side calculation requests: computed if supported (never invented), else typed calculation_unavailable."""
    out = []
    for r in option.get("calculation_requests", []):
        ins = r["inputs"]
        vals = [cat.get(i) for i in ins]
        if None in vals:
            out.append({**r, "status": "calculation_unavailable", "reason": "an input is not in the catalogue"})
            continue
        a, b = vals[0]["value"], vals[1]["value"]
        if r["operation"] == "ratio":
            if b == 0:
                out.append({**r, "status": "calculation_unavailable", "reason": "division by zero"})
                continue
            v, unit = a / b, "ratio"
        else:
            v, unit = (a - b if r["operation"] == "difference" else a + b), vals[0]["unit"]
        qid = f"q:req:{r['operation']}:{ins[0]}:{ins[1]}"
        out.append({**r, "status": "computed", "id": qid, "value": v, "unit": unit, "kind": "calculated"})
    return out


def render(option: dict, cat: dict) -> dict:
    """A copy of the option's free text with every {q:...} replaced by its engine value and kind."""
    def sub(t):
        def one(m):
            q = cat[m.group(1)]
            return f"{q['value']:.2f}{'%' if q['unit'] == 'percent' else ' pp' if q['unit'] == 'pp' else ''} [{q['kind']}]"
        return REF.sub(one, t)
    return {path: sub(t) for path, t in texts(option) if REF.search(t)}


def scenarios(base: dict, sims: list[dict]) -> list[dict]:
    """The baseline (no action) and the alternative scenarios, each labelled with its kind."""
    out = []
    if base.get("point") is not None:
        out.append({"scenario": "baseline_no_action", "kind": "model", "point": base["point"], "interval80": base["interval80"],
                    "model": base.get("model"), "status": base.get("status")})
    for s in sims:
        out.append({"scenario": s["scenario"], "kind": "simulated", "point": s["outcome_point"], "range": s["outcome_range"],
                    "assumptions": s["assumptions"], "calibration": "uncalibrated"})
    return out


# ------------------------------------------------------------------------------------------------ fixture tooling
def translate_v2_option(o: dict, cat: dict) -> dict:
    """OFFLINE ANALYSIS ONLY (not used at run time): could each number a v2 option declared have been expressed under
    v3? Observed and calculated values map to a catalogue id when the value reproduces at the declared precision;
    assumed values map to the magnitude fields; anything else is unreferencable (an unsupported number)."""
    rows = []
    for c in o.get("numeric_claims", []):
        tok = v2.ANY_NUMBER.findall(c["text"])
        val = float(tok[-1]) if tok else c["value"]
        d = len(str(tok[-1]).split(".")[1]) if tok and "." in tok[-1] else c.get("decimals", 2)
        if c["kind"] == "assumed":
            ok = c["value"] in (o["intended_effect"]["magnitude_low_pp"], o["intended_effect"]["magnitude_high_pp"])
            rows.append({"text": c["text"], "route": "magnitude_field" if ok else "unsupported_assumption"})
            continue
        hits = [k for k, q in cat.items() if round(q["value"], d) == round(val, d)]
        rows.append({"text": c["text"], "route": "catalogue" if hits else "unreferencable", "ids": hits[:3]})
    return {"ref": o.get("ref"), "numbers": rows,
            "fully_referencable": all(r["route"] in ("catalogue", "magnitude_field") for r in rows)}
