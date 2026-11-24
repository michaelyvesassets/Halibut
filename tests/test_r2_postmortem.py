"""Regression tests from the R2 live validation postmortem (experiment r2v-b16fc9281536).

Fixture: tests/fixtures/haiku_r2v_b16fc9281536_generator_replies.json, the two complete Haiku generator replies
(stop_reason end_turn), recorded verbatim. The original experiment and its pre-registered criteria are unchanged; these
tests pin the engineering failure and its fix.
"""

import json
from pathlib import Path

import pytest

from halibut.epistemic import schemas as sch
from halibut.investigation import compiler
from halibut.models import registry as reg
from halibut.validation import check_directions
from halibut.world.context import signature

from .test_r2_registry_approvals import panel as synthetic_panel

FIX = json.loads((Path(__file__).parent / "fixtures" / "haiku_r2v_b16fc9281536_generator_replies.json").read_text())
ENTITIES = ["byd", "volkswagen", "tesla", "kia", "mg", "toyota", "ford", "bmw"]


def _ids(rep):
    d = rep["data"]
    out = {x for e in d["explanations"] for x in e["evidence"]}
    for h in d["hypotheses"]:
        out |= set(h["supporting_evidence"]) | {i["reference_metric"] for i in h["observable_implications"] if i["reference_metric"]}
    return sorted(out)


def _schema(rep):
    return sch.generator_schema(_ids(rep), ENTITIES, [f for f in reg.FAMILIES if f != "simulation.share_shift"])


@pytest.mark.parametrize("i,n_hyp", [(0, 4), (1, 3)])
def test_overlong_explanations_no_longer_discard_valid_hypotheses(i, n_hyp):
    rep = FIX["replies"][i]
    assert rep["data"]["explanations"] and len(rep["data"]["explanations"]) == 5  # the recorded failure: 5 > maxItems 4
    valid, rejected, arrays, top = sch.split_multi(rep["data"], _schema(rep), ["explanations", "hypotheses"])
    assert top == []                                   # before the fix this was a whole-reply rejection that stopped the run
    assert list(arrays) == ["explanations"] and "rejected, not trimmed" in arrays["explanations"][0]
    assert valid["explanations"] == []                 # the over-long array is rejected in full: nothing is trimmed or kept
    assert len(valid["hypotheses"]) == n_hyp and not rejected["hypotheses"]
    assert "maxItems" not in json.dumps(sch.api_schema(_schema(rep)))  # why: the provider does not enforce maxItems


def test_hypotheses_array_overflow_still_stops():
    rep = FIX["replies"][0]
    data = {**rep["data"], "explanations": rep["data"]["explanations"][:2],
            "hypotheses": rep["data"]["hypotheses"] * 2}  # 8 > maxItems 6
    valid, _, arrays, top = sch.split_multi(data, _schema(rep), ["explanations", "hypotheses"])
    assert top == [] and "hypotheses" in arrays and valid["hypotheses"] == []


def test_direction_words_must_match_numbers():
    flagged = [(r["origin"], e["statement"]) for r in FIX["replies"] for e in r["data"]["explanations"]
               if check_directions(e["statement"])]
    assert len(flagged) == 1 and flagged[0][0] == "2026-05-23" and "fell from 0.44% to 2.47%" in flagged[0][1]
    assert check_directions("BYD rose from 2.11% to 6.30%") == []
    assert check_directions("MG fell from 30.05% (P4) to 22.85% (L4)") == []
    assert check_directions("Tesla increased from 13.20% to 10.11%")


def test_mis_specified_gap_tests_are_not_registered():
    p = synthetic_panel()
    m = p.metrics()
    l4b = p.metric_id("segment_share", "L4", "b")
    # the recorded form: a share GAP compared with a single brand's share level (2025 H3 I3, 2026 H3 I4)
    spec, why = compiler.compile_implication({"template": "segment_share_gap", "entity": "a", "entity_b": "b",
                                              "comparator": "above_reference", "reference_metric": l4b}, p, m)
    assert spec is None and "no reference metric" in why
    # a reference must measure the same quantity
    mix = p.metric_id("segment_mix", "L4", "a")
    spec, why = compiler.compile_implication({"template": "segment_share", "entity": "a", "entity_b": "",
                                              "comparator": "above_reference", "reference_metric": mix}, p, m)
    assert spec is None and "like with like" in why
    # still valid: another brand's share of the same template, and the gap against its own latest window
    assert compiler.compile_implication({"template": "segment_share", "entity": "a", "entity_b": "", "comparator": "above_reference",
                                         "reference_metric": l4b}, p, m)[0]
    assert compiler.compile_implication({"template": "segment_share_gap", "entity": "a", "entity_b": "b",
                                         "comparator": "increase_vs_L4", "reference_metric": ""}, p, m)[0]


def test_own_latest_window_reference_is_the_same_claim_as_trend_continuation():
    h2 = next(h for h in FIX["replies"][0]["data"]["hypotheses"] if h["ref"] == "H2")  # Ford, 2025-11-23
    imp = h2["observable_implications"][0]
    assert imp["comparator"] == "above_reference" and imp["reference_metric"] == "metric:segment_share:L4:ford"
    assert signature(imp) == ("segment_share", "ford", "", "increase_vs_L4")
    other = {**imp, "reference_metric": "metric:segment_share:L4:tesla"}
    assert signature(other)[3] == "above_reference"  # comparing with ANOTHER brand stays a distinct claim


def test_originality_does_not_depend_on_sibling_runs(tmp_path):
    """The reference library is prior knowledge: earlier dates only, never mock/reference output. Running another
    workforce first at the same date must not change a hypothesis's novelty."""
    from halibut.data.providers.base import register
    from halibut.data.store import Store
    from halibut.intel.workflow import investigate
    from halibut.investigation.loop import run_investigation
    from .test_intel import MARKET, routes
    from .test_providers import FakeNet
    from .test_r2_loop import MEASUREMENT
    (tmp_path / "m.toml").write_text(MARKET + MEASUREMENT)
    store = Store(tmp_path / "s.sqlite")
    register(store, Path(__file__).resolve().parents[1] / "configs" / "data" / "providers.toml")
    investigate(store, tmp_path / "m.toml", tmp_path / "out", tmp_path / "raw", log=lambda *_: None, transport=FakeNet(routes()))
    kw = dict(as_of="2025-05-23", evaluate_as_of="2026-11-23", log=lambda *_: None)
    alone = run_investigation(store, tmp_path / "m.toml", agents="mock", **kw)
    run_investigation(store, tmp_path / "m.toml", agents="reference", **kw)
    after = run_investigation(store, tmp_path / "m.toml", agents="mock", faults=True, **kw)  # different inputs, same date
    nov = lambda d: {h["data"]["ref"]: h["data"]["novelty"] for h in d["stages"]["hypotheses"]}  # noqa: E731
    assert {k: v for k, v in nov(after).items() if k in nov(alone)} == nov(alone)
    store.close()


def test_overlong_explanations_do_not_stop_the_loop(tmp_path):
    """End to end on fixture data: a generator reply with 5 explanations completes the investigation (critic and strategist
    run); the explanations array is recorded as rejected with class schema.array_limit."""
    from halibut.agents.mock_loop import responder
    from halibut.agents.routing import Router
    from halibut.data.providers.base import register
    from halibut.data.store import Store
    from halibut.intel.workflow import investigate
    from halibut.investigation.loop import run_investigation
    from .test_intel import MARKET, routes
    from .test_providers import FakeNet
    from .test_r2_loop import MEASUREMENT
    (tmp_path / "m.toml").write_text(MARKET + MEASUREMENT)
    store = Store(tmp_path / "s.sqlite")
    register(store, Path(__file__).resolve().parents[1] / "configs" / "data" / "providers.toml")
    investigate(store, tmp_path / "m.toml", tmp_path / "out", tmp_path / "raw", log=lambda *_: None, transport=FakeNet(routes()))

    def five(role, p):
        out = responder(role, p)
        if role == "hypothesis_generator":
            out["explanations"] = (out["explanations"] * 3)[:5]
        return out
    d = run_investigation(store, tmp_path / "m.toml", router=Router("mock", store, "x", responders=five),
                          as_of="2025-05-23", evaluate_as_of="2026-11-23", log=lambda *_: None)
    assert d["status"] == "completed" and d["stages"]["hypotheses"] and not d["stages"]["explanations"]
    assert d["failures"]["counts"].get("schema.array_limit") == 1
    assert [c["role"] for c in d["agents"]["calls"]] == ["hypothesis_generator", "loop_critic", "strategist"]
    rec = store.conn.execute("SELECT stage, failure_class, item_json FROM validation_records WHERE failure_class='schema.array_limit'")
    row = rec.fetchone()
    assert row[0] == "schema" and len(json.loads(row[2])["items"]) == 5  # the rejected array is kept verbatim
    store.close()
