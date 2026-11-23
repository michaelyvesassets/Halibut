"""Strict JSON schemas for agent outputs, a local validator, and the API-safe subset.

Schemas are built per call FROM THE EVIDENCE BUNDLE: evidence ids, subjects, tools and hypothesis ids
are enums of the values actually present, so an id that is not in the bundle, a value glued to an id
("metric:x = 8.13"), a dict where a list of ids belongs, or a prefixed subject ("brand:byd") cannot
validate. Nothing is repaired: an invalid item is rejected with its reasons.

Two views of each schema:
- ``validate(instance, schema)``: the FULL schema (including maxItems, maxLength, minItems), enforced
  locally on every reply from every provider.
- ``api_schema(schema)``: the subset that Claude structured outputs (``output_config.format``)
  enforce at generation. Per the Claude API documentation, numeric/string-length constraints and
  complex array constraints are not supported, so they are removed there and enforced locally.
"""

from __future__ import annotations

SCHEMA_VERSIONS = {"research": "research.v2", "hypothesis": "hypothesis_selection.v2", "critic": "critic.v2", "smoke": "smoke.v1"}
KINDS = ("momentum", "displacement", "exposure")
SEVERITIES = ("low", "medium", "high")
CONFIDENCE = ("low", "medium", "high")
UNSUPPORTED_BY_API = ("maxItems", "minItems", "maxLength", "minLength", "minimum", "maximum", "multipleOf", "pattern")

LIMITS = {"research_actions": 6, "reason_chars": 200, "target_chars": 120, "hypotheses": 8, "claim_chars": 600,
          "evidence_ids": 8, "mechanisms": 3, "mechanism_chars": 160, "levers": 4, "critiques_per_hypothesis": 3,
          "critique_chars": 500}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _enum(values) -> dict:
    vals = sorted(set(values))
    return {"type": "string", "enum": vals} if vals else {"type": "string", "enum": ["__none__"]}


def research_schema(offline_tools, proposable_tools) -> dict:
    action = _obj({"tool": _enum(list(offline_tools) + list(proposable_tools)),
                   "target": {"type": "string", "maxLength": LIMITS["target_chars"]},
                   "reason": {"type": "string", "maxLength": LIMITS["reason_chars"]}})
    return _obj({"actions": {"type": "array", "items": action, "maxItems": LIMITS["research_actions"]}})


def hypothesis_schema(metric_ids, subjects, levers) -> dict:
    item = _obj({"kind": _enum(KINDS), "subject": _enum(subjects),
                 "claim": {"type": "string", "maxLength": LIMITS["claim_chars"]},
                 "evidence": {"type": "array", "items": _enum(metric_ids), "minItems": 1, "maxItems": LIMITS["evidence_ids"]},
                 "confidence": _enum(CONFIDENCE),
                 "mechanism_candidates": {"type": "array", "items": {"type": "string", "maxLength": LIMITS["mechanism_chars"]},
                                          "maxItems": LIMITS["mechanisms"]},
                 "levers": {"type": "array", "items": _enum(levers), "maxItems": LIMITS["levers"]}})
    return _obj({"hypotheses": {"type": "array", "items": item, "maxItems": LIMITS["hypotheses"]}})


def critic_schema(hypothesis_ids) -> dict:
    item = _obj({"hypothesis_id": _enum(hypothesis_ids), "severity": _enum(SEVERITIES),
                 "statement": {"type": "string", "maxLength": LIMITS["critique_chars"]}})
    return _obj({"critiques": {"type": "array", "items": item,
                               "maxItems": max(1, LIMITS["critiques_per_hypothesis"] * len(set(hypothesis_ids)))}})


SMOKE_SCHEMA = _obj({"ok": {"type": "boolean"}})


def api_schema(schema):
    """Copy of ``schema`` without the keywords structured outputs do not enforce (validated locally instead)."""
    if isinstance(schema, dict):
        return {k: api_schema(v) for k, v in schema.items() if k not in UNSUPPORTED_BY_API}
    if isinstance(schema, list):
        return [api_schema(v) for v in schema]
    return schema


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _tname(x) -> str:
    return {dict: "object", list: "array", str: "string", bool: "boolean", type(None): "null", int: "integer",
            float: "number"}.get(type(x), type(x).__name__)


def validate(x, schema: dict, path: str = "$") -> list[str]:
    """Validate against the subset of JSON Schema used by Halibut (type, properties, required,
    additionalProperties=false, enum, items, minItems, maxItems, minLength, maxLength). Returns problems."""
    t = schema.get("type")
    if t == "integer":
        ok = isinstance(x, int) and not isinstance(x, bool)
    elif t == "number":
        ok = isinstance(x, (int, float)) and not isinstance(x, bool)
    elif t:
        ok = isinstance(x, _TYPES[t])
    else:
        ok = True
    if not ok:
        return [f"{path}: expected {t}, got {_tname(x)}"]
    p = []
    if "enum" in schema and x not in schema["enum"]:
        shown = repr(x) if len(repr(x)) <= 120 else repr(x)[:117] + "..."
        p.append(f"{path}: {shown} is not an allowed value")
    if t == "string":
        if "maxLength" in schema and len(x) > schema["maxLength"]:
            p.append(f"{path}: longer than {schema['maxLength']} characters ({len(x)})")
        if "minLength" in schema and len(x) < schema["minLength"]:
            p.append(f"{path}: shorter than {schema['minLength']} characters")
    if t == "object":
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in x:
                p.append(f"{path}: missing required field {k!r}")
        if schema.get("additionalProperties") is False:
            for k in x:
                if k not in props:
                    p.append(f"{path}: unexpected field {k!r}")
        for k, sub in props.items():
            if k in x:
                p += validate(x[k], sub, f"{path}.{k}")
    if t == "array":
        if "maxItems" in schema and len(x) > schema["maxItems"]:
            p.append(f"{path}: more than {schema['maxItems']} items ({len(x)})")
        if "minItems" in schema and len(x) < schema["minItems"]:
            p.append(f"{path}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for i, v in enumerate(x):
                p += validate(v, schema["items"], f"{path}[{i}]")
    return p


def split_items(data, schema: dict, key: str):
    """Validate a reply ``{key: [items]}``: (valid_items, rejected [(index, item, problems)], top_level_problems).

    Top-level problems (not an object, missing key, too many items, unexpected fields) reject the
    whole reply; otherwise each item is accepted or rejected on its own (never repaired)."""
    if not isinstance(data, dict):
        return [], [], [f"$: expected object, got {_tname(data)}"]
    arr_schema = schema["properties"][key]
    top = validate({k: v for k, v in data.items() if k != key}, {**schema, "required": []})
    if key not in data:
        top.append(f"$: missing required field {key!r}")
        return [], [], top
    items = data[key]
    if not isinstance(items, list):
        return [], [], top + [f"$.{key}: expected array, got {_tname(items)}"]
    if "maxItems" in arr_schema and len(items) > arr_schema["maxItems"]:
        top.append(f"$.{key}: more than {arr_schema['maxItems']} items ({len(items)})")
    if top:
        return [], [], top
    valid, rejected = [], []
    for i, it in enumerate(items):
        prob = validate(it, arr_schema["items"], f"$.{key}[{i}]")
        (rejected.append((i, it, prob)) if prob else valid.append(it))
    return valid, rejected, []


# --------------------------------------------------------------------------------------------- R2 loop roles
SCHEMA_VERSIONS.update({"hypothesis_generator": "hypothesis_original.v1", "loop_critic": "critic_loop.v1",
                        "strategist": "strategy_options.v1"})
TEMPLATES = ("share", "segment_share", "segment_mix", "market_segment_share", "segment_share_gap")
COMPARATORS = ("increase_vs_L4", "decrease_vs_L4", "above_reference", "below_reference")
ASSUMPTION_KINDS = ("behavioural", "market_structure", "measurement", "competitive", "other")
RESPONSES = ("match_price", "expand_range", "increase_distribution", "increase_marketing", "hold", "unknown")
OFFSETS = ("none", "offsets_partly", "offsets_fully", "unknown")
HYP_REFS = tuple(f"H{i}" for i in range(1, 7))
IMP_REFS = tuple(f"I{i}" for i in range(1, 5))
OPT_REFS = tuple(f"O{i}" for i in range(1, 5))
CURRENCIES = ("GBP", "EUR", "USD", "NOK", "SEK", "DKK", "unknown")


def _s(n):
    return {"type": "string", "maxLength": n}


def _arr(item, mx, mn=None):
    a = {"type": "array", "items": item, "maxItems": mx}
    if mn:
        a["minItems"] = mn
    return a


def generator_schema(metric_ids, entities, families) -> dict:
    ents = sorted(set(entities))
    imp = _obj({"id": _enum(IMP_REFS), "template": _enum(TEMPLATES + ("other",)), "entity": _enum(ents + ["market"]),
                "entity_b": _enum(ents + [""]), "comparator": _enum(COMPARATORS), "reference_metric": _enum(list(metric_ids) + [""]),
                "description": _s(300), "would_refute_if": _s(300)})
    hyp = _obj({"ref": _enum(HYP_REFS), "statement": _s(500),
                "mechanism": _obj({"cause": _s(200), "pathway": _s(500), "effect_template": _enum(TEMPLATES + ("other",)),
                                   "direction": _enum(("increase", "decrease"))}),
                "conditions": _arr(_s(200), 4),
                "assumptions": _arr(_obj({"statement": _s(240), "kind": _enum(ASSUMPTION_KINDS)}), 5),
                "supporting_evidence": _arr(_enum(metric_ids), 8, 1),
                "alternative_explanations": _arr(_obj({"statement": _s(300), "discriminating_implication": _enum(IMP_REFS)}), 3, 1),
                "observable_implications": _arr(imp, 4, 1),
                "uncertainty": _obj({"level": _enum(CONFIDENCE), "sources": _arr(_s(160), 3)}),
                "refutation_criteria": _s(500), "novelty_claim": _s(300),
                "model_request": _obj({"family": _enum(list(families) + ["none"]), "rationale": _s(300),
                                       "numerical_assumptions": _arr(_obj({"name": _s(60), "value": {"type": "number"},
                                                                           "unit": _s(30), "basis": _s(240)}), 3)})})
    expl = _obj({"statement": _s(500), "evidence": _arr(_enum(metric_ids), 6, 1)})
    return _obj({"explanations": _arr(expl, 4), "hypotheses": _arr(hyp, 6)})


def loop_critic_schema(refs) -> dict:
    item = _obj({"hypothesis_ref": _enum(refs), "severity": _enum(SEVERITIES), "statement": _s(500),
                 "challenged_assumption": _s(240)})
    return _obj({"critiques": _arr(item, max(1, 3 * len(set(refs))))})


def strategist_schema(refs, entities, metric_ids, levers, modes) -> dict:
    ents = sorted(set(entities))
    crit = _obj({"template": _enum(TEMPLATES), "entity": _enum(ents + ["market"]), "entity_b": _enum(ents + [""]),
                 "comparator": _enum(COMPARATORS), "reference_metric": _enum(list(metric_ids) + [""]),
                 "horizon_quarters": {"type": "integer", "enum": [2]}})
    opt = _obj({"ref": _enum(OPT_REFS), "title": _s(160), "mode": _enum(modes), "levers": _arr(_enum(levers), 4, 1),
                "intended_effect": _obj({"template": _enum(TEMPLATES), "entity": _enum(ents + ["market"]),
                                         "direction": _enum(("increase", "decrease")),
                                         "magnitude_low_pp": {"type": "number"}, "magnitude_high_pp": {"type": "number"},
                                         "basis": _s(300)}),
                "linked_hypotheses": _arr(_enum(refs), 4, 1), "supporting_evidence": _arr(_enum(metric_ids), 6, 1),
                "assumptions": _arr(_obj({"statement": _s(240), "kind": _enum(ASSUMPTION_KINDS)}), 5),
                "competitor_responses": _arr(_obj({"competitor": _enum(ents), "response": _enum(RESPONSES),
                                                   "likelihood": _enum(CONFIDENCE), "effect_on_option": _enum(OFFSETS)}), 4),
                "cost": _obj({"known": {"type": "boolean"}, "low": {"type": "number"}, "high": {"type": "number"},
                              "currency": _enum(CURRENCIES), "basis": _s(300)}),
                "risks": _arr(_s(200), 4), "uncertainty": _enum(CONFIDENCE), "success_criteria": _arr(crit, 3, 1)})
    return _obj({"options": _arr(opt, 4)})


def split_multi(data, schema: dict, keys) -> tuple[dict, dict, list]:
    """Like ``split_items`` for replies with several item arrays: ({key: valid}, {key: rejected}, top_problems)."""
    if not isinstance(data, dict):
        return {}, {}, [f"$: expected object, got {_tname(data)}"]
    top = [f"$: unexpected field {k!r}" for k in data if k not in schema["properties"]]
    top += [f"$: missing required field {k!r}" for k in keys if k not in data]
    for k in keys:
        if k in data:
            if not isinstance(data[k], list):
                top.append(f"$.{k}: expected array, got {_tname(data[k])}")
            elif len(data[k]) > schema["properties"][k].get("maxItems", 10 ** 9):
                top.append(f"$.{k}: more than {schema['properties'][k]['maxItems']} items ({len(data[k])})")
    if top:
        return {}, {}, top
    valid, rejected = {}, {}
    for k in keys:
        valid[k], rejected[k] = [], []
        for i, it in enumerate(data[k]):
            prob = validate(it, schema["properties"][k]["items"], f"$.{k}[{i}]")
            (rejected[k].append((i, it, prob)) if prob else valid[k].append(it))
    return valid, rejected, []
