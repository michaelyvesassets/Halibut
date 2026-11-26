"""Reasoning contract ``loop.v2`` (R2 Step 2): mechanism discovery, information gaps, structured critique and
numerically accountable strategy. Selected per investigation with ``run_investigation(..., contract="loop.v2")``;
``loop.v1`` stays the default and is byte-identical, so recorded v1 requests (Step 1 cache reuse) are unchanged.

What changes against v1 (founder direction of 2026-11-26):

Generator
- Each hypothesis names the observed movement it ``explains`` (a metric id) and its ``rivals``: other hypotheses in
  the same reply that explain the same movement by a different mechanism.
- A test is DISCRIMINATING when a rival hypothesis predicts the opposite direction for the same quantity (same
  template, entity and entity_b). This is checked mechanically from the rivals' own implications, never from
  claims about what a rival "would" predict.
- Originality does NOT require contradicting the trend. A hypothesis is original if its testable implications are
  not all in the reference library, or if at least one of them discriminates it from a rival. Predicting trend
  continuation is fine when the hypothesis also says something a rival does not. Nothing rewards unusual
  predictions as such.
- ``information_gaps``: the missing measurement, why it matters, which hypotheses it would distinguish, potential
  sources and the expected research value. They are recorded for future research agents. No research is run, and
  no external source is contacted.
- Measurements (``extended``): non_segment_share and units_growth, plus pairwise ``above_zero`` / ``below_zero``
  comparators for share gaps (the correct encoding of "A exceeds B").

Critic
- Critiques are structured by DIMENSION:
  - factual_contradiction;
  - unsupported_detail;
  - numerical_accuracy;
  - causal_identification;
  - alternative_explanation;
  - test_validity.
- Factual and numerical critiques must cite the evidence metric that shows the problem.
- The critic now sees the evidence metrics, so it can check claims; v1 showed it only the market definition.
- It is told not to raise immaterial issues. The blinded planted-defect benchmark
  (``critic_benchmark.py``) penalises false alarms.

Strategist
- Comparator company (``entity_b``) for pairwise targets.
- Objective and success-measure agreement.
- ``numeric_claims`` with unit, precision and kind:
  - ``observed``: an evidence metric, reproduced exactly at the stated precision;
  - ``calculated``: a difference or ratio of two metrics, recomputed exactly;
  - ``assumed``: a scenario assumption, equal to an assumed magnitude.
- Every number in EVERY free-text field must be declared. Closeness to a real value is never enough.

Context efficiency
- Metric-id enums are defined once (``$defs``) instead of being repeated in every array.
- Metric rows are compact ``[id, value_pct]`` pairs, because the id already encodes template, window and entity.
- The grammar prose is dropped, because the schema carries the enums.
- No evidence is removed: every metric id and value is still present (tested).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from ..agents.loop_agents import SYSTEMS as V1_SYSTEMS
from ..epistemic import schemas as sch
from ..validation import check_directions, check_numbers
from ..world.panel import EXTENDED_TEMPLATES

CONTRACT = "loop.v2"
V1 = "loop.v1"
CONTRACTS = (V1, CONTRACT)
MEASUREMENTS = ("base", "extended")
SCHEMA_VERSIONS = {"hypothesis_generator": "hypothesis_mechanism.v2", "loop_critic": "critic_structured.v2",
                   "strategist": "strategy_options.v2"}
UNITS = ("percent", "pp", "fraction", "count")
KINDS = ("observed", "calculated", "assumed")
OPERATIONS = ("value", "difference", "ratio", "none")
PAIRWISE_TEMPLATES = ("segment_share_gap",)
COMPARATORS_V2 = sch.COMPARATORS + ("above_zero", "below_zero")
DIMENSIONS = ("factual_contradiction", "unsupported_detail", "numerical_accuracy", "causal_identification",
              "alternative_explanation", "test_validity")
EVIDENCE_REQUIRED = ("factual_contradiction", "numerical_accuracy")
GAP_REFS = tuple(f"G{i}" for i in range(1, 5))
VALUE = ("low", "medium", "high")
INCREASE, DECREASE = ("increase_vs_L4", "above_reference"), ("decrease_vs_L4", "below_reference")
DIRECTION = {"increase_vs_L4": "up", "above_reference": "up", "decrease_vs_L4": "down", "below_reference": "down",
             "above_zero": "pos", "below_zero": "neg"}
OPPOSITE = {("up", "down"), ("down", "up"), ("pos", "neg"), ("neg", "pos")}
ANY_NUMBER = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?")
TEMPLATE_DEFINITIONS = {
    "share": "entity units / market units", "segment_share": "entity segment units / market segment units",
    "segment_mix": "entity segment units / entity units", "market_segment_share": "market segment units / market units",
    "segment_share_gap": "segment_share(entity) - segment_share(entity_b); compare with zero via above_zero/below_zero",
    "non_segment_share": "entity non-segment units / market non-segment units",
    "units_growth": "units over the window / units over the same quarters one year earlier - 1 (entity or 'market')"}

GENERATOR_SYSTEM = (
    "You are Halibut's Investigator and Hypothesis Generator for ONE market, as of the slice's date. Separate what was "
    "OBSERVED from what EXPLAINS it. First write up to 4 EXPLANATIONS of observed movements (the past only; cite metric "
    "ids). Then write up to 6 HYPOTHESES about MECHANISMS. For the main observed movements, give COMPETING mechanisms as "
    "separate hypotheses that name each other in 'rivals' and name the movement they explain ('explains', a metric id). "
    "Each hypothesis needs: a mechanism (cause, pathway, effect, direction); scope conditions; explicit assumptions; "
    "supporting evidence (metric ids copied exactly); at least one alternative explanation and the implication that "
    "discriminates it; 1 to 4 OBSERVABLE IMPLICATIONS for the next two quarters in the measurement grammar; uncertainty; "
    "refutation criteria; novelty_claim; and optionally a model family with numerical assumptions and their basis. "
    "DISCRIMINATION: rivals should predict DIFFERENT outcomes for at least one shared quantity (same template, entity and "
    "entity_b); that is what makes a test informative. A mechanism may predict trend continuation, as long as it also "
    "says something its rival does not; never invent unusual predictions to look original. To claim that one company "
    "exceeds another, use segment_share_gap with comparator above_zero or below_zero. INFORMATION GAPS: list up to 4 "
    "missing measurements that would distinguish your hypotheses (what is missing, why it matters, which hypotheses it "
    "distinguishes, potential sources, expected research value); never pretend missing data were observed. If an "
    "implication needs data not in the slice, use template 'other' and describe it. Every number in text must equal a "
    "slice value (percent or percentage points, 2 decimals). Never write forecasts. Reply with a JSON object matching the "
    "schema.")
CRITIC_SYSTEM = (
    "You are Halibut's Critic. You see the hypotheses and the evidence metrics. Raise only MATERIAL problems, each under "
    "one dimension: factual_contradiction (a claim or assumption contradicted by an evidence metric: cite it), "
    "unsupported_detail (a fact the evidence does not contain, such as launches, prices or fuel splits), "
    "numerical_accuracy (a number that does not match the evidence: cite the metric), causal_identification (the tests "
    "cannot separate the mechanism from correlation or trend), alternative_explanation (a rival mechanism that fits the "
    "same evidence), test_validity (a test that cannot fail, compares unlike quantities, or is underpowered). A "
    "well-specified hypothesis may get no critique at all: do not invent problems. At most 3 critiques per hypothesis, "
    "each at most 80 words. Reply with a JSON object matching the schema.")
STRATEGIST_SYSTEM = V1_SYSTEMS["strategist"] + (
    " PAIRWISE TARGETS: an intended effect on a gap between two companies must name both (entity and entity_b); "
    "otherwise entity_b is empty. AGREEMENT: at least one success criterion must measure exactly the intended effect "
    "(same metric, entity and entity_b) and move in the intended direction. NUMBERS: declare EVERY number you write in "
    "any text field in numeric_claims with its unit (percent, pp, fraction or count), the number of decimals as written, "
    "and its kind: observed (copied from one evidence metric), calculated (difference or ratio of two evidence metrics) "
    "or assumed (a scenario assumption, equal to one of your assumed effect magnitudes). Never present an assumption as "
    "observed.")
SYSTEMS = {"hypothesis_generator": GENERATOR_SYSTEM, "loop_critic": CRITIC_SYSTEM, "strategist": STRATEGIST_SYSTEM}


def systems(contract: str) -> dict:
    return SYSTEMS if contract == CONTRACT else V1_SYSTEMS


# ------------------------------------------------------------------------------------------------- schemas
def _mid():
    return {"$ref": "#/$defs/metric_id"}


def _mid_or_empty():
    return {"$ref": "#/$defs/metric_id_or_empty"}


def _with_defs(schema: dict, metric_ids) -> dict:
    defs = {"metric_id": sch._enum(list(metric_ids)), "metric_id_or_empty": sch._enum(list(metric_ids) + [""])}
    used = json.dumps(schema)
    return {**schema, "$defs": {k: v for k, v in defs.items() if f"#/$defs/{k}" in used}}  # only definitions in use


def inline(schema: dict) -> dict:
    """The schema with every ``$ref`` replaced by its definition (for the local validator)."""
    defs = schema.get("$defs", {})

    def walk(x):
        if isinstance(x, dict):
            if "$ref" in x:
                return walk(defs[x["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in x.items() if k != "$defs"}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(schema)


def generator_schema(metric_ids, entities, families, extended: bool) -> dict:
    ents = sorted(set(entities))
    tmpl = sch.TEMPLATES + (EXTENDED_TEMPLATES if extended else ())
    imp = sch._obj({"id": sch._enum(sch.IMP_REFS), "template": sch._enum(tmpl + ("other",)),
                    "entity": sch._enum(ents + ["market"]), "entity_b": sch._enum(ents + [""]),
                    "comparator": sch._enum(COMPARATORS_V2),
                    "reference_metric": _mid_or_empty(),
                    "description": sch._s(300), "would_refute_if": sch._s(300)})
    hyp = sch._obj({"ref": sch._enum(sch.HYP_REFS), "statement": sch._s(500), "explains": _mid(),
                    "rivals": sch._arr(sch._enum(sch.HYP_REFS), 3),
                    "mechanism": sch._obj({"cause": sch._s(200), "pathway": sch._s(500),
                                           "effect_template": sch._enum(tmpl + ("other",)),
                                           "direction": sch._enum(("increase", "decrease"))}),
                    "conditions": sch._arr(sch._s(200), 4),
                    "assumptions": sch._arr(sch._obj({"statement": sch._s(240), "kind": sch._enum(sch.ASSUMPTION_KINDS)}), 5),
                    "supporting_evidence": sch._arr(_mid(), 8, 1),
                    "alternative_explanations": sch._arr(sch._obj({"statement": sch._s(300),
                                                                   "discriminating_implication": sch._enum(sch.IMP_REFS)}), 3, 1),
                    "observable_implications": sch._arr(imp, 4, 1),
                    "uncertainty": sch._obj({"level": sch._enum(sch.CONFIDENCE), "sources": sch._arr(sch._s(160), 3)}),
                    "refutation_criteria": sch._s(500), "novelty_claim": sch._s(300),
                    "model_request": sch._obj({"family": sch._enum(list(families) + ["none"]), "rationale": sch._s(300),
                                               "numerical_assumptions": sch._arr(sch._obj({
                                                   "name": sch._s(60), "value": {"type": "number"}, "unit": sch._s(30),
                                                   "basis": sch._s(240)}), 3)})})
    expl = sch._obj({"statement": sch._s(500), "evidence": sch._arr(_mid(), 6, 1)})
    gap = sch._obj({"id": sch._enum(GAP_REFS), "missing_measurement": sch._s(200), "why_it_matters": sch._s(300),
                    "distinguishes": sch._arr(sch._enum(sch.HYP_REFS), 3, 1), "potential_sources": sch._arr(sch._s(120), 3, 1),
                    "expected_research_value": sch._enum(VALUE)})
    return _with_defs(sch._obj({"explanations": sch._arr(expl, 4), "hypotheses": sch._arr(hyp, 6),
                                "information_gaps": sch._arr(gap, 4)}), metric_ids)


def critic_schema(refs, metric_ids) -> dict:
    item = sch._obj({"hypothesis_ref": sch._enum(refs), "dimension": sch._enum(DIMENSIONS),
                     "severity": sch._enum(sch.SEVERITIES), "statement": sch._s(500), "evidence": sch._arr(_mid(), 3),
                     "challenged_assumption": sch._s(240)})
    return _with_defs(sch._obj({"critiques": sch._arr(item, max(1, 3 * len(set(refs))))}), metric_ids)


def strategist_schema(refs, entities, metric_ids, levers, modes, extended: bool) -> dict:
    s = copy.deepcopy(sch.strategist_schema(refs, entities, metric_ids, levers, modes))
    opt = s["properties"]["options"]["items"]
    ents = sorted(set(entities))
    tmpl = sch.TEMPLATES + (EXTENDED_TEMPLATES if extended else ())
    opt["properties"]["intended_effect"]["properties"]["template"] = sch._enum(tmpl)
    opt["properties"]["intended_effect"]["properties"]["entity_b"] = sch._enum(ents + [""])
    opt["properties"]["intended_effect"]["required"].append("entity_b")
    crit = opt["properties"]["success_criteria"]["items"]["properties"]
    crit["template"], crit["comparator"] = sch._enum(tmpl), sch._enum(COMPARATORS_V2)
    crit["reference_metric"] = _mid_or_empty()
    opt["properties"]["supporting_evidence"] = sch._arr(_mid(), 6, 1)
    claim = sch._obj({"text": sch._s(24), "value": {"type": "number"}, "unit": sch._enum(UNITS), "kind": sch._enum(KINDS),
                      "decimals": {"type": "integer", "enum": [0, 1, 2, 3, 4]}, "operation": sch._enum(OPERATIONS),
                      "metric_ids": sch._arr(_mid(), 2)})
    opt["properties"]["numeric_claims"] = sch._arr(claim, 12)
    opt["required"].append("numeric_claims")
    return _with_defs(s, metric_ids)


# ------------------------------------------------------------------------------------------------- context
def compact_slice(slice_: dict, metrics: dict) -> dict:
    """The v2 slice: the same evidence, fewer tokens. Rows become [id, value_pct]; definitions are stated once."""
    s = {k: v for k, v in slice_.items() if k not in ("metrics", "measurement_grammar", "slice_id")}
    s["metrics"] = [[r["id"], r["value_pct"]] for r in slice_["metrics"]]
    used = sorted({m["template"] for m in metrics.values()} | {"segment_share_gap"})
    s["metric_id_format"] = "metric:<template>:<window L4|P4>:<entity> (market_segment_share has no entity); values in %"
    s["templates"] = {t: TEMPLATE_DEFINITIONS[t] for t in used if t in TEMPLATE_DEFINITIONS}
    s["untestable_implications"] = "use template 'other' with a description; recorded as untested"
    s["slice_id"] = "slice:" + hashlib.sha256(json.dumps(s, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return s


# ------------------------------------------------------------------------------------------------- generator checks
def quantity(imp: dict) -> tuple:
    return (imp["template"], imp["entity"], imp.get("entity_b") or "")


_STOP = {"the", "and", "for", "with", "its", "their", "from", "that", "this", "than", "into", "more", "less", "are", "is",
         "of", "to", "in", "on", "by", "as", "an", "a", "or", "not", "but", "which", "while", "rather", "share", "segment"}
DISTINCT_MAX_SIMILARITY = 0.5   # mechanism word overlap (Jaccard) above this: not meaningfully distinct
DUPLICATE_MIN_SIMILARITY = 0.8  # above this with the same explained movement: a duplicate mechanism


def _words(h: dict) -> set:
    m = h.get("mechanism") or {}
    return {w for w in re.findall(r"[a-z][a-z\-]+", f"{m.get('cause', '')} {m.get('pathway', '')}".lower()) if w not in _STOP}


def mechanism_similarity(h: dict, r: dict) -> float:
    a, b = _words(h), _words(r)
    return len(a & b) / len(a | b) if a | b else 1.0


def _entities_of(mid: str) -> set:
    parts = (mid or "").split(":")
    return {parts[3]} if len(parts) > 3 else {"market"}


def assess_pair(h: dict, r: dict) -> dict:
    """Quality gates for a rival pair. Opposing predictions ALONE never count: the pair must explain the same
    observation, have meaningfully distinct mechanisms, not be a pure negation of each other, and disagree on a
    quantity about the explained movement's entities (an interpretable test)."""
    reasons = []
    same = h.get("explains") == r.get("explains") or bool(set(h.get("supporting_evidence", [])) & set(r.get("supporting_evidence", [])))
    if not same:
        reasons.append("the rivals do not explain the same observation")
    sim = mechanism_similarity(h, r)
    if sim > DISTINCT_MAX_SIMILARITY:
        reasons.append(f"mechanisms are not meaningfully distinct (word overlap {sim:.2f})")
    qh, qr = {}, {}
    for src, dst in ((h, qh), (r, qr)):
        for i in src["observable_implications"]:
            if i["template"] != "other":
                dst.setdefault(quantity(i), set()).add(DIRECTION.get(i["comparator"]))
    shared = set(qh) & set(qr)
    disputed = [q for q in shared if any((x, y) in OPPOSITE for x in qh[q] for y in qr[q])]
    if not disputed:
        reasons.append("no shared quantity on which they predict opposite outcomes")
    elif set(qh) == set(qr) == set(disputed):
        reasons.append("artificial disagreement: one hypothesis is a pure negation of the other")
    focus = _entities_of(h.get("explains")) | _entities_of(r.get("explains"))
    interpretable = [q for q in disputed if {q[1], q[2]} & focus]
    if disputed and not interpretable:
        reasons.append("the disputed quantity is unrelated to the explained movement (not interpretable)")
    return {"rival": r["ref"], "counted": not reasons, "reasons": reasons, "mechanism_similarity": round(sim, 2),
            "disputed_quantities": [list(q) for q in interpretable]}


def rival_assessments(accepted: list[dict]) -> dict:
    """{ref: [assess_pair(...)]} for every declared rival (either direction) that is an accepted hypothesis."""
    by = {h["ref"]: h for h in accepted}
    out = {}
    for h in accepted:
        rivals = {x for x in h.get("rivals", []) if x in by and x != h["ref"]} | \
                 {o["ref"] for o in accepted if h["ref"] in o.get("rivals", []) and o["ref"] != h["ref"]}
        out[h["ref"]] = [assess_pair(h, by[x]) for x in sorted(rivals)]
    return out


def discrimination(accepted: list[dict]) -> dict:
    """{(ref, implication id): [rival refs]} for implications on a disputed quantity of a rival pair that passes EVERY
    quality gate (assess_pair). Mechanical; never from claims about what a rival 'would' predict."""
    out = {}
    for h in accepted:
        for a in rival_assessments(accepted).get(h["ref"], []):
            if not a["counted"]:
                continue
            for imp in h["observable_implications"]:
                if imp["template"] != "other" and list(quantity(imp)) in a["disputed_quantities"]:
                    out.setdefault((h["ref"], imp["id"]), []).append(a["rival"])
    return {k: sorted(set(v)) for k, v in out.items()}


def duplicate_mechanism(h: dict, earlier: list[dict]) -> str | None:
    """The ref of an earlier hypothesis with the same explained movement and a near-identical mechanism, if any."""
    for e in earlier:
        if e.get("explains") == h.get("explains") and mechanism_similarity(h, e) >= DUPLICATE_MIN_SIMILARITY:
            return e["ref"]
    return None


def gap_problems(g: dict, accepted_refs, allowed_numbers) -> list[str]:
    p = [f"information gap {g['id']} refers to {r}, which is not an accepted hypothesis"
         for r in g["distinguishes"] if r not in accepted_refs]
    text = " ".join([g["missing_measurement"], g["why_it_matters"]])
    return p + check_numbers(text, allowed_numbers) + check_directions(text)


def critique_problems(c: dict) -> list[str]:
    if c["dimension"] in EVIDENCE_REQUIRED and not c["evidence"]:
        return [f"a {c['dimension']} critique must cite the evidence metric that shows the problem"]
    return []


# ------------------------------------------------------------------------------------------------- strategist checks
_NON_TEXT = {"ref", "mode", "levers", "template", "entity", "entity_b", "direction", "linked_hypotheses",
             "supporting_evidence", "kind", "competitor", "response", "likelihood", "effect_on_option", "currency",
             "uncertainty", "comparator", "reference_metric", "numeric_claims"}


def free_text(x, key=None) -> list[str]:
    """Every free-text string of an option (all fields except ids and enums), so no number can hide in an unlisted field."""
    if key in _NON_TEXT:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for k, v in x.items() for s in free_text(v, k)]
    if isinstance(x, list):
        return [s for v in x for s in free_text(v, key)]
    return []


ASSUMPTION_FIELDS = ("intended_effect.basis", "assumptions.statement")


def text_fields(x, path="") -> list[tuple[str, str]]:
    """(field path without indices, text) for every free-text string of an option."""
    key = path.rsplit(".", 1)[-1]
    if key in _NON_TEXT:
        return []
    if isinstance(x, str):
        return [(path, x)]
    if isinstance(x, dict):
        return [t for k, v in x.items() for t in text_fields(v, f"{path}.{k}" if path else k)]
    if isinstance(x, list):
        return [t for v in x for t in text_fields(v, path)]
    return []


def disguised_assumptions(option: dict) -> list[str]:
    """An ASSUMED number may appear only where assumptions belong (the effect basis or an assumption statement) and
    only in a sentence that says it is assumed, so a hypothetical cannot pass as an observed fact."""
    assumed = {c["text"] for c in option.get("numeric_claims", []) if c["kind"] == "assumed"}
    p = []
    for path, text in text_fields(option):
        for sent in re.split(r"(?<=[.;])\s+", text):
            for tok in ANY_NUMBER.findall(sent):
                if tok not in assumed:
                    continue
                if path not in ASSUMPTION_FIELDS:
                    p.append(f"assumed number {tok} appears in {path}, where it reads as a fact")
                elif "assum" not in sent.lower():
                    p.append(f"assumed number {tok} in {path} is not marked as assumed in its sentence")
    return p


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
    want = INCREASE + ("above_zero",) if e["direction"] == "increase" else DECREASE + ("below_zero",)
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
    for tok in ANY_NUMBER.findall(" ".join(free_text(option))):
        if tok not in declared:
            p.append(f"number {tok} in the narrative is not declared in numeric_claims")
    for c in claims:
        written = c["text"].lstrip("+")
        try:
            dec = len(written.split(".")[1]) if "." in written else 0
            same = abs(float(written) - c["value"]) <= 1e-12
        except ValueError:
            dec, same = -1, False
        if dec != c["decimals"] or not same:
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


def validate_option(option: dict, entities, metric_values: dict) -> list[str]:
    return (check_pairwise(option["intended_effect"], entities) + check_objective_agreement(option)
            + check_numeric_claims(option, metric_values) + disguised_assumptions(option))
