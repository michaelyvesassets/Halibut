"""Strict agent output contracts, failure taxonomy, and the real Haiku benchmark items (R1)."""

import json
from pathlib import Path

import pytest

from halibut.epistemic import schemas as sch
from halibut.epistemic.failures import call_outcome, classify_item, classify_outcome, classify_problems
from halibut.validation import ALLOWED_LEVERS

FIX = json.loads((Path(__file__).parent / "fixtures" / "haiku_rpf5673fab06_hypothesis_items.json").read_text())
SUBJECTS = FIX["competitor_ids"]
METRICS = ["metric:share:L4:byd", "metric:share:P4:byd", "metric:segment_share:L4:byd"]
GOOD = {"kind": "momentum", "subject": "byd", "claim": "BYD rose.", "evidence": ["metric:share:L4:byd"],
        "confidence": "low", "mechanism_candidates": ["range"], "levers": ["price"]}


def test_schema_enums_come_from_the_bundle_and_nothing_is_repaired():
    s = sch.hypothesis_schema(METRICS, SUBJECTS, ALLOWED_LEVERS)
    valid, rejected, top = sch.split_items({"hypotheses": [
        GOOD,
        {**GOOD, "evidence": {"metrics_cited": ["metric:share:L4:byd"], "segment_share_L4_pct": 19.5}},  # F2: dict
        {**GOOD, "evidence": ["metric:share:L4:byd = 8.1321"]},                                         # F2: value appended
        {**GOOD, "subject": "brand:byd"},                                                               # F3: prefixed
        {**GOOD, "evidence": ["metric:share:L4:invented"]},                                             # fabricated
        {**GOOD, "claim": "x" * 700},                                                                   # over length
    ]}, s, "hypotheses")
    assert valid == [GOOD] and not top
    classes = [classify_problems(p, it, subjects=SUBJECTS) for _, it, p in rejected]
    assert classes == ["format.evidence_not_id_list", "format.evidence_value_appended", "format.subject_not_canonical",
                       "fabricated.evidence_id", "schema.other"]
    assert rejected[1][1]["evidence"] == ["metric:share:L4:byd = 8.1321"]  # recorded as returned, not cleaned up


def test_top_level_violations_reject_the_whole_reply():
    s = sch.research_schema(["store.track_record"], ["provider.new_source_search"])
    act = {"tool": "store.track_record", "target": "", "reason": "check"}
    assert sch.split_items({"actions": [act] * 7}, s, "actions")[2] == ["$.actions: more than 6 items (7)"]
    assert sch.split_items({"actions": [act], "note": "x"}, s, "actions")[2] == ["$: unexpected field 'note'"]
    assert sch.split_items([act], s, "actions")[2] == ["$: expected object, got array"]
    assert sch.split_items({"actions": [{**act, "tool": "network.anything"}]}, s, "actions")[1]


def test_api_schema_keeps_enforceable_keywords_only():
    s = sch.hypothesis_schema(METRICS, SUBJECTS, ALLOWED_LEVERS)
    api = sch.api_schema(s)
    text = json.dumps(api)
    assert all(k not in text for k in sch.UNSUPPORTED_BY_API)
    assert api["additionalProperties"] is False and api["properties"]["hypotheses"]["items"]["additionalProperties"] is False
    item = api["properties"]["hypotheses"]["items"]
    assert set(item["required"]) == set(item["properties"])  # every field required (structured-output friendly)
    assert item["properties"]["evidence"]["items"]["enum"] == sorted(METRICS)
    assert "maxItems" in json.dumps(s)  # the local (full) schema still carries the limits


def test_call_outcomes_are_typed():
    assert call_outcome("max_tokens") == "truncated: stop_reason=max_tokens"
    assert classify_outcome("truncated: stop_reason=max_tokens") == classify_outcome("unparseable: stop_reason=max_tokens") \
        == "call.truncated"
    assert classify_outcome("refusal: stop_reason=refusal") == "call.refusal"
    assert classify_outcome("invalid_json: stop_reason=end_turn") == "call.invalid_json"
    assert classify_outcome("schema_invalid: $.x") == "call.schema_top_level"
    assert classify_outcome("error: Anthropic HTTP 500") == "call.error" and classify_outcome("ok") is None


def test_real_haiku_items_classify_as_diagnosed():
    """The 36 items of the first live benchmark, run through the legacy (v1) conversion rules:
    momentum needs a canonical competitor subject (else dropped); everything else is judged on evidence format."""
    dropped, rejected, valid = {}, {}, 0
    for reply in FIX["replies"]:
        for it in reply["hypotheses"]:
            cls = classify_item(it, subjects=SUBJECTS, kinds=sch.KINDS)
            subj = [c for c in cls if c in ("format.subject_not_canonical", "unsupported.kind_or_subject")]
            if it.get("kind") == "momentum" and subj:
                dropped[subj[0]] = dropped.get(subj[0], 0) + 1
                continue
            ev = [c for c in cls if c.startswith("format.evidence")]
            if ev:
                rejected[ev[0]] = rejected.get(ev[0], 0) + 1
            else:
                valid += 1
    assert dropped == {"format.subject_not_canonical": 8, "unsupported.kind_or_subject": 1}
    assert rejected == {"format.evidence_not_id_list": 13, "format.evidence_value_appended": 4}
    assert valid == 10
    assert sum(dropped.values()) + sum(rejected.values()) + valid == 36


def test_real_haiku_items_under_the_r1_contract():
    """Under the strict v2 contract (well-formed metric ids allowed) only items with canonical subjects and plain id
    lists pass; nothing is repaired. Fabrication count stays zero, as diagnosed."""
    ids = sorted({e for r in FIX["replies"] for it in r["hypotheses"] if isinstance(it.get("evidence"), list)
                  for e in it["evidence"] if isinstance(e, str) and " " not in e and e.startswith("metric:")})
    item_schema = sch.hypothesis_schema(ids, SUBJECTS, ALLOWED_LEVERS)["properties"]["hypotheses"]["items"]
    results = [(it, sch.validate(it, item_schema)) for r in FIX["replies"] for it in r["hypotheses"]]
    classes = {}
    for it, p in results:
        if p:
            c = classify_problems(p, it, subjects=SUBJECTS)
            classes[c] = classes.get(c, 0) + 1
    assert sum(1 for _, p in results if not p) == 4
    assert classes == {"format.evidence_not_id_list": 16, "format.subject_not_canonical": 9,
                       "format.evidence_value_appended": 7}
    assert "fabricated.evidence_id" not in classes


@pytest.mark.parametrize("item,expected", [
    ({"evidence": "metric:a"}, ["format.evidence_not_id_list"]),
    ({"evidence": ["metric:a: 1.2"]}, ["format.evidence_value_appended"]),
    ({"evidence": ["metric:share:L4:byd"], "subject": "byd"}, []),
])
def test_classify_item_cases(item, expected):
    assert classify_item(item, subjects=SUBJECTS) == expected
