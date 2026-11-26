"""Implication compiler, pre-registered tests and their resolution (L5/L6, deterministic).

An observable implication becomes a TEST frozen at the investigation's as-of date: a metric (template,
entity, optional entity_b) over the next two quarters after the last quarter knowable at as-of, a
comparator and a reference value fixed at as-of. Tests resolve at a later evaluation date using ONLY
data published by then; unpublished targets stay 'pending'. Template 'other' (data not in the
evidence) is registered as 'not_testable'.

Informativeness: each test records whether simple trend extrapolation (baseline.momentum) already
predicts it ('trend_consistent'). Passing only trend-consistent tests is weak support.

Hypothesis status (deterministic, v1):
  untested      no implication could be compiled
  refuted       a registered test failed
  survived      every registered test resolved and passed. SURVIVAL IS NOT CAUSAL PROOF: the outcome is
                consistent with the mechanism; alternatives are not excluded unless a discriminating test
                failed for them.
  inconclusive  some tests passed, others are still pending at the evaluation date
  proposed      nothing resolved yet (no evaluation date, or all pending)
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from ..data.store import to_timestamp, utc_now
from ..world.panel import next_quarters

TEST_SPEC_VERSION = "hypothesis_test.v1"
PUBLICATION_LAG_DAYS = 120
INCREASE = ("increase_vs_L4", "above_reference")
ZERO = ("above_zero", "below_zero")  # loop.v2: only for pairwise templates


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def _quarter_end(p: str) -> date:
    y, q = int(p[:4]), int(p[-1])
    return date(y, 3 * q, {1: 31, 2: 30, 3: 30, 4: 31}[q])


def holds(x: float, comparator: str, ref: float) -> bool:
    if comparator in ZERO:  # pairwise "A exceeds B" / "B exceeds A" (the correct encoding of a gap claim)
        return x > 0 if comparator == "above_zero" else x < 0
    return x > ref if comparator in INCREASE else x < ref


def compile_implication(imp: dict, panel, metrics: dict) -> tuple[dict | None, str | None]:
    t, ent, entb = imp["template"], imp["entity"], (imp.get("entity_b") or None)
    if t == "other":
        return None, f"not testable with this evidence: {imp.get('description') or 'measurement not available'}"
    if t in ("segment_share", "segment_mix", "market_segment_share", "segment_share_gap", "non_segment_share") and not panel.segment:
        return None, f"template {t} needs a market segment; this panel has none"
    if t == "market_segment_share":
        if ent != "market":
            return None, "market_segment_share is a market-level metric (entity must be 'market')"
    elif t == "units_growth" and ent == "market":
        pass
    elif ent not in panel.entities:
        return None, f"entity {ent!r} is not a competitor in this market"
    if t == "segment_share_gap":
        if not entb or entb == ent or entb not in panel.entities:
            return None, "segment_share_gap needs a second, different competitor (entity_b)"
    elif entb:
        return None, f"entity_b is only used by segment_share_gap (got {entb!r})"
    if not panel.periods:
        return None, "no transaction periods available as of the investigation date"
    L4, _ = panel.windows()
    comp = imp["comparator"]
    if comp in ZERO:
        if t != "segment_share_gap":
            return None, f"{comp} compares a pairwise gap with zero; template {t} is not pairwise"
        ref, src = 0.0, "zero (pairwise comparison)"
    elif comp in ("increase_vs_L4", "decrease_vs_L4"):
        ref, src = panel.value(t, ent, L4, entb)[0], "L4 window"
    else:
        rm = imp.get("reference_metric") or ""
        if rm not in metrics:
            return None, f"comparator {comp} needs a reference metric id from the evidence (got {rm!r})"
        if t == "segment_share_gap":
            return None, ("a share gap has no reference metric in the evidence: compare it with its own latest window "
                          "(increase_vs_L4 / decrease_vs_L4)")
        if metrics[rm]["template"] != t:
            return None, (f"reference metric {rm} measures {metrics[rm]['template']}, not {t}: a test must compare like with "
                          "like")
        ref, src = metrics[rm]["value"], rm
    if ref is None:
        return None, "the reference value is not computable from the evidence"
    targets = next_quarters(panel.periods[-1], 2)
    spec = {"spec_version": TEST_SPEC_VERSION, "template": t, "entity": ent, "entity_b": entb, "comparator": comp,
            "reference_value": ref, "reference_source": src, "target_periods": targets, "as_of": panel.as_of,
            "market_id": panel.market_id, "panel_source": panel.source, "would_refute_if": imp.get("would_refute_if", ""),
            "expected_resolvable_after": (_quarter_end(targets[-1]) + timedelta(days=PUBLICATION_LAG_DAYS)).isoformat()}
    from ..models.registry import MIN_QUARTERS, execute  # noqa: PLC0415
    ser = panel.series(t, ent, entb)
    if len(ser) >= 8:
        mom = execute({"family": "baseline.momentum", "target": {"template": t, "entity": ent, "entity_b": entb}}, panel)["point"]
        spec["trend_check"] = {"momentum_point": mom, "trend_consistent": holds(mom, comp, ref),
                               "note": "trend_consistent = simple extrapolation already predicts this outcome"}
    else:
        spec["trend_check"] = {"momentum_point": None, "trend_consistent": None,
                               "note": f"fewer than 8 quarters (min for models: {MIN_QUARTERS})"}
    return spec, None


def register_test(store, hypothesis_claim_id: str, implication_ref: str, spec: dict | None, reason: str | None,
                  run_id: str, as_of: str) -> dict:
    body = spec if spec is not None else {"not_testable": reason, "as_of": as_of}
    tid = "t:" + _h([hypothesis_claim_id, implication_ref, body])[:20]
    status = "registered" if spec is not None else "not_testable"
    store.conn.execute("INSERT OR IGNORE INTO hypothesis_tests VALUES (?,?,?,?,?,?,?,?,?)",
                       (tid, hypothesis_claim_id, implication_ref, run_id, to_timestamp(as_of), status,
                        json.dumps(body, sort_keys=True, default=str), _h(body), utc_now()))
    return {"test_id": tid, "hypothesis_claim_id": hypothesis_claim_id, "implication_ref": implication_ref, "status": status,
            "spec": spec, "reason": reason, "spec_hash": _h(body)}


def resolve(store, test: dict, eval_panel, evaluated_as_of: str) -> dict:
    s = test["spec"]
    v, ids, avail = eval_panel.value(s["template"], s["entity"], s["target_periods"], s["entity_b"])
    if v is None:
        outcome, res = "pending", {"realized": None, "note": f"target periods {s['target_periods']} not all published by "
                                                             f"{evaluated_as_of}"}
    else:
        ok = holds(v, s["comparator"], s["reference_value"])
        outcome = "passed" if ok else "failed"
        res = {"realized": v, "reference_value": s["reference_value"], "comparator": s["comparator"],
               "evidence": ids[:50], "evidence_count": len(ids), "trend_consistent": s.get("trend_check", {}).get("trend_consistent")}
    rid = "tr:" + _h([test["test_id"], to_timestamp(evaluated_as_of)])[:20]
    store.conn.execute("INSERT OR IGNORE INTO test_results VALUES (?,?,?,?,?,?,?)",
                       (rid, test["test_id"], to_timestamp(evaluated_as_of), outcome, avail or None,
                        json.dumps(res, sort_keys=True, default=str), utc_now()))
    return {"result_id": rid, "test_id": test["test_id"], "outcome": outcome, "available_at": avail or None, **res}


def hypothesis_status(tests: list[dict], results: dict) -> dict:
    """Status from registered tests and their results (see module docstring)."""
    reg = [t for t in tests if t["status"] == "registered"]
    if not reg:
        return {"status": "untested", "reason": "no implication could be tested with this evidence", "effective_at": None,
                "strength": None}
    rs = [results.get(t["test_id"]) for t in reg]
    resolved = [r for r in rs if r and r["outcome"] in ("passed", "failed")]
    failed = [r for r in resolved if r["outcome"] == "failed"]
    when = max((r["available_at"] for r in resolved if r.get("available_at")), default=None)
    if failed:
        return {"status": "refuted", "reason": f"{len(failed)} pre-registered test(s) failed", "effective_at": when,
                "strength": None}
    if not resolved:
        return {"status": "proposed", "reason": "no test resolved yet", "effective_at": None, "strength": None}
    against = sum(1 for r in resolved if r.get("trend_consistent") is False)
    strength = "moderate (passed a test that trend extrapolation did not predict)" if against else \
               "weak (passed only tests that trend extrapolation already predicted)"
    if len(resolved) < len(reg):
        return {"status": "inconclusive", "reason": f"{len(resolved)} of {len(reg)} tests resolved (all passed); others pending",
                "effective_at": when, "strength": strength}
    return {"status": "survived", "reason": "all pre-registered tests passed; survival is not causal proof",
            "effective_at": when, "strength": strength}
