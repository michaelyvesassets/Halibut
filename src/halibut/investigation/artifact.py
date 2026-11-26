"""Investigation result contract ``halibut.investigation_result.v1`` (dashboard-ready, read-only).

One JSON document per investigation, from objective to proposed lessons. Every item has the same
envelope, so a frontend can render any stage generically:

    {"id", "type", "status", "label", "produced_by", "as_of", "data", "links": [{"relation", "to"}], "provenance"}

``type`` is a claim type (explanation, hypothesis, forecast, estimate, simulated_outcome, critique,
strategic_proposal, assumption, lesson, derived_metric) or an object type (test, test_result,
model_version, experiment_protocol, question). ``links`` point to ids of items in this document (or
evidence ids). The frontend needs no database access: the document is self-contained, and
``halibut.command.api`` serves stored documents read-only. The full schema is ``SCHEMA`` below; it is also
published as ``docs/architecture/schemas/investigation_result.v1.schema.json``.

``content_hash`` covers the objective, the stages, the links and the item-level failures: identical inputs
give an identical hash. It excludes operational metadata (ids, timestamps, LLM call/cache accounting,
approval request ids).
"""

from __future__ import annotations

import hashlib
import json

SCHEMA_ID = "halibut.investigation_result.v1"
ITEM_TYPES = ("derived_metric", "explanation", "hypothesis", "critique", "assumption", "test", "test_result", "model_version",
              "forecast", "estimate", "simulated_outcome", "strategic_proposal", "experiment_protocol", "lesson", "question",
              "information_gap")  # information_gap: loop.v2 only
STATUSES = ("completed", "stopped", "insufficient_evidence", "invalid_market")
OPTIONAL_STAGES = ("information_gaps",)  # loop.v2 only (absent from v1 documents, so their hashes are unchanged)
STAGES = ("explanations", "hypotheses", "critiques", "tests", "test_results", "model_specs", "assumptions", "forecasts",
          "estimates", "simulations", "strategic_options", "lessons")

_ITEM = {"type": "object", "additionalProperties": False,
         "required": ["id", "type", "status", "label", "produced_by", "as_of", "data", "links", "provenance"],
         "properties": {"id": {"type": "string"}, "type": {"type": "string", "enum": list(ITEM_TYPES)},
                        "status": {"type": "string"}, "label": {"type": "string"}, "produced_by": {"type": "string"},
                        "as_of": {"type": "string"}, "data": {"type": "object"},
                        "links": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                             "required": ["relation", "to"],
                                                             "properties": {"relation": {"type": "string"},
                                                                            "to": {"type": "string"}}}},
                        "provenance": {"type": "object"}}}
SCHEMA = {
    "$id": SCHEMA_ID, "type": "object", "additionalProperties": False,
    "required": ["schema", "investigation_id", "input_id", "content_hash", "created_at", "tenant", "status", "stop_reason",
                 "objective", "labels", "evidence", "world_model", "stages", "experiment_protocol", "links", "failures",
                 "agents", "approvals", "provenance"],
    "properties": {
        "schema": {"type": "string", "enum": [SCHEMA_ID]}, "investigation_id": {"type": "string"},
        "input_id": {"type": "string"}, "content_hash": {"type": "string"}, "created_at": {"type": "string"},
        "tenant": {"type": "string"}, "status": {"type": "string", "enum": list(STATUSES)},
        "stop_reason": {},  # string or null (the local validator has no union types; see json_schema_document)
        "objective": {"type": "object", "required": ["id", "market_id", "mode", "decision", "as_of", "evaluate_as_of",
                                                     "horizon_quarters", "jurisdiction", "success_metric"]},
        "labels": {"type": "object"},
        "evidence": {"type": "object", "required": ["coverage", "panel", "metrics"]},
        "world_model": {"type": "object", "required": ["slice_id", "market", "entities", "anomalies"]},
        "stages": {"type": "object", "additionalProperties": False, "required": list(STAGES),
                   "properties": {k: {"type": "array", "items": _ITEM} for k in STAGES + OPTIONAL_STAGES}},
        "experiment_protocol": {},
        "links": {"type": "array", "items": {"type": "object", "required": ["from", "relation", "to"]}},
        "failures": {"type": "object", "required": ["items", "calls"]},
        "agents": {"type": "object", "required": ["routing", "calls", "costs"]},
        "approvals": {"type": "array"},
        "provenance": {"type": "object", "required": ["engine_version", "prompt_version", "schemas", "registry_rule",
                                                      "test_spec_version", "market_spec_sha256", "panel"]}}}


def item(id_, type_, status, label, produced_by, as_of, data, links=(), provenance=None) -> dict:
    return {"id": id_, "type": type_, "status": status, "label": label[:300], "produced_by": produced_by, "as_of": as_of,
            "data": data, "links": [{"relation": r, "to": t} for r, t in links], "provenance": provenance or {}}


def content_hash(doc: dict) -> str:
    proto = doc["experiment_protocol"]
    if proto:  # the approval request id is operational metadata (a new request per run)
        proto = {**proto, "provenance": {k: v for k, v in proto["provenance"].items() if k != "approval_request"}}
    core = {"objective": {k: v for k, v in doc["objective"].items() if k != "id"}, "status": doc["status"],
            "stages": doc["stages"], "experiment_protocol": proto, "links": doc["links"],
            "failures_items": doc["failures"]["items"], "evidence": doc["evidence"], "world_model": doc["world_model"]}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def validate(doc: dict) -> list[str]:
    from ..epistemic.schemas import validate as v  # noqa: PLC0415
    problems = v(doc, SCHEMA)
    ids = {it["id"] for st in doc.get("stages", {}).values() for it in st}
    if doc.get("experiment_protocol"):
        ids.add(doc["experiment_protocol"]["id"])
    for st in doc.get("stages", {}).values():
        for it in st:
            for ln in it["links"]:
                to = ln["to"]
                if to.startswith(("c:", "t:", "tr:", "mv:", "xp:")) and to not in ids:
                    problems.append(f"item {it['id']} links to {to}, which is not in this document")
    return problems


def json_schema_document() -> dict:
    """The schema as a standalone JSON Schema document (draft 2020-12 subset)."""
    s = json.loads(json.dumps(SCHEMA))
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["title"] = "Halibut investigation result v1"
    s["properties"]["stop_reason"] = {"type": ["string", "null"]}
    s["properties"]["experiment_protocol"] = {"oneOf": [{"type": "null"}, _ITEM]}
    return s
