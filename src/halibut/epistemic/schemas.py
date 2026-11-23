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
