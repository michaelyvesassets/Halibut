"""R2 live intelligence validation experiment: reference vs mock vs Claude on identical evidence.

    plan (dry run, no request):  preflight every origin (evidence cut-off, test window, publication of the window by
                                 the evaluation date, leakage versus the model's training cutoff); run the REFERENCE and
                                 MOCK workforces offline; price every live call at its worst case from the measured
                                 prompt sizes; check the plan fits the approved cap.
    live (approved):             the same, then ONE structured-output smoke call; only if it succeeds, one Claude
                                 investigation per origin (3 calls each). All live spend is under one experiment scope,
                                 so the fail-closed spend guard enforces the approved cap across the whole experiment
                                 (smoke included). Evidence identity is checked (panel fingerprints must match per origin).

Outputs (``<out>/<experiment id>/``): every investigation result document (dashboard format), ``comparison.json``,
``comparison.md`` and ``manifest.json``. Success criteria are pre-registered in ``CRITERIA`` and evaluated mechanically.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from ..agents.routing import Router, load_routing
from ..control import approvals
from ..data.store import to_timestamp, utc_now
from ..epistemic.events import EventLog
from ..intel.market import load_market
from ..world.panel import build_panel, next_quarters
from . import artifact as art
from .evaluation import evaluate_document, overlap
from .loop import run_investigation

EXPERIMENT_VERSION = "r2_live_validation.v1"
SMOKE_CAP_USD = 0.01
KINDS = ("reference", "mock", "anthropic")
CRITERIA = {  # pre-registered (v1). A: pipeline integrity (all must hold). B: quality bar for the Claude workforce.
    "A1_smoke_structured_output_ok": "the structured-output smoke call returns {\"ok\": true}",
    "A2_all_live_investigations_completed": "every Claude investigation has status 'completed'",
    "A3_no_truncation_refusal_or_top_level_schema_failure": "no Claude call truncated, refused or rejected as a whole",
    "A4_documents_validate": "every exported document validates against halibut.investigation_result.v1",
    "A5_identical_evidence": "reference, mock and Claude saw the same panel (fingerprint) at each origin",
    "A6_spend_within_cap": "total live spend (smoke included) <= the approved cap",
    "B1_evidence_fidelity_ge_0.80": "accepted / (accepted + rejected) Claude hypotheses >= 0.80",
    "B2_two_original_per_origin": ">= 2 original (non-restatement) Claude hypotheses at each origin",
    "B3_falsifiability_ge_0.80": ">= 80% of accepted Claude hypotheses are falsifiable (see evaluation.py)",
    "B4_testability_ge_0.60": ">= 60% of Claude implications compile into registered tests",
    "B5_informativeness_ge_0.40": ">= 40% of Claude registered tests are not predicted by trend extrapolation",
}


def _quarter_end(p: str) -> date:
    y, q = int(p[:4]), int(p[-1])
    return date(y, 3 * q, {1: 31, 2: 30, 3: 30, 4: 31}[q])


def preflight(store, market, origins, evaluate_as_of, training_cutoff: str | None) -> list[dict]:
    out, eval_panel = [], build_panel(store, market, evaluate_as_of)
    for o in origins:
        p = build_panel(store, market, o)
        targets = next_quarters(p.periods[-1], 2) if p.periods else []
        published = {q: eval_panel.cells.get((eval_panel.MARKET, "all"), {}).get(q, (None, None, None))[2] for q in targets}
        bases = sorted({r[0] for r in store.conn.execute(
            """SELECT DISTINCT o.availability_basis FROM observations o JOIN series s USING(series_id)
               WHERE s.dataset_id='dft_veh0160_makes' AND o.period IN (%s)""" % ",".join("?" * len(targets)), targets)}) \
            if targets and market.transactions.get("provider") == "dft" else []
        leak, before = [], 0
        if training_cutoff:
            cut = training_cutoff + "-30" if len(training_cutoff) == 7 else training_cutoff
            for q in targets:
                pub = published.get(q)
                if pub and pub[:10] <= cut:
                    before += 1
                    leak.append(f"{o}: {q} was published ({pub[:10]}) before the model's training cutoff ({training_cutoff}): "
                                f"the model may know the outcome")
                elif pub:
                    leak.append(f"{o}: {q} was published ({pub[:10]}) after the training cutoff ({training_cutoff})")
                if _quarter_end(q).isoformat() <= cut:
                    leak.append(f"{o}: {q} ended before the training cutoff: news and commentary about it may be in training data")
        out.append({"origin": o, "evidence_last_quarter": p.periods[-1] if p.periods else None, "n_quarters": len(p.periods),
                    "test_window": targets, "window_published_by_evaluation": {q: (v[:10] if v else None) for q, v in published.items()},
                    "window_resolvable": bool(targets) and all(published.values()),
                    "window_availability_basis": bases, "training_data_leakage": leak,
                    "leakage_label": ("training cutoff unknown" if not training_cutoff else
                                      "pre-cutoff: potentially contaminated" if before == len(targets) else
                                      "straddles the training cutoff: partly contaminated" if before else
                                      "post-cutoff outcome data (commentary may still predate the cutoff)")})
    return out


def _plan_live(docs_mock: dict, max_usd: float | None, cfg: dict) -> dict:
    from ..agents.anthropic_provider import pricing_from_config  # noqa: PLC0415
    pr = pricing_from_config(cfg)
    rt = load_routing()
    per_origin, total = {}, 0.0
    for o, doc in docs_mock.items():
        gen = next(c for c in doc["agents"]["calls"] if c["role"] == "hypothesis_generator")
        g = (gen["prompt_chars"] or 0) + (gen["schema_chars"] or 0)
        # upper bounds, independent of how many hypotheses the mock produced: 6 hypotheses at the schema's length
        # limits are ~36k characters; the critic sees them, the strategist sees the slice plus a summary of them
        sizes = {"hypothesis_generator": g, "loop_critic": 4000 + 36000 + 6000, "strategist": g + 15000 + 12000}
        rows = []
        for role, chars in sizes.items():
            w = pr.worst_case("x" * chars, rt["roles"][role]["max_output_tokens"])
            rows.append({"role": role, "prompt_and_schema_chars": chars, "max_output_tokens": rt["roles"][role]["max_output_tokens"],
                         "worst_case_usd": w})
            total += w
        per_origin[o] = rows
    smoke = pr.worst_case("x" * 400, 20)
    retries = 1 + min(int(cfg.get("retries", 1)), 2)
    worst = total + smoke
    return {"model": cfg["model"], "calls": 1 + sum(len(v) for v in per_origin.values()), "per_origin": per_origin,
            "smoke_worst_case_usd": smoke, "worst_case_usd": worst, "worst_case_with_retries_usd": total * retries + smoke,
            "max_attempts": 1 + retries * sum(len(v) for v in per_origin.values()), "cap_usd": max_usd,
            "fits_cap_without_retries": max_usd is not None and worst <= max_usd,
            "fits_cap_with_retries": max_usd is not None and total * retries + smoke <= max_usd,
            "note": "the spend guard reserves every attempt before sending and refuses any that would exceed the cap"}


def _smoke(store, scope, cfg, environ, http_client) -> dict:
    from ..agents.anthropic_provider import AnthropicProvider  # noqa: PLC0415
    from ..agents.cli import SMOKE_SYSTEM, SMOKE_USER  # noqa: PLC0415
    from ..epistemic.schemas import SMOKE_SCHEMA  # noqa: PLC0415
    q = AnthropicProvider({**cfg, "max_spend_usd_per_run": min(SMOKE_CAP_USD, float(cfg["max_spend_usd_per_run"])), "retries": 0},
                          store, f"{scope}:smoke", environ=environ, http_client=http_client)
    try:
        r = q.complete(SMOKE_SYSTEM, SMOKE_USER, role="smoke", max_output_tokens=20, run_id=f"{scope}:smoke", schema=SMOKE_SCHEMA)
    except Exception as exc:  # noqa: BLE001  (recorded, then the experiment stops)
        return {"ok": False, "error": str(exc)[:300]}
    return {"ok": r.data == {"ok": True}, "data": r.data, "model": r.model, "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens, "cost_usd": r.actual_cost_usd, "latency_s": round(r.latency_seconds, 2),
            "stop_reason": getattr(r, "stop_reason", None), "request_id": getattr(r, "request_id", None)}


def run_validation(store, market_path, origins, evaluate_as_of, *, live: bool = False, approval_id: str | None = None,
                   max_usd: float | None = None, out_dir="results/r2_validation", environ=None, http_client=None,
                   cfg_overrides: dict | None = None, log=print) -> dict:
    """``cfg_overrides`` is for offline tests only (e.g. approved=True); the CLI never sets it."""
    from ..agents.llm import load_llm_config  # noqa: PLC0415
    market = load_market(market_path)
    rt = load_routing()
    cfg = {**load_llm_config(Path(__file__).resolve().parents[3] / rt["provider_config"], model=rt["default_model"]),
           **(cfg_overrides or {})}
    origins = sorted(origins)
    exp_id = "r2v-" + hashlib.sha256(json.dumps([market.id, origins, evaluate_as_of, utc_now()]).encode()).hexdigest()[:12]
    subject = f"r2_validation:{market.id}"
    events = EventLog(store)
    if live:
        if max_usd is None:
            raise approvals.AuthorizationError("a live validation needs --max-usd")
        approvals.require(store, approval_id, "paid_run", subject, cost_usd=max_usd)
    events.append("validation.started", "L6", {"market": market.id, "origins": origins, "evaluate_as_of": evaluate_as_of,
                                               "live": live, "max_usd": max_usd}, run_id=exp_id, subject_id=approval_id,
                  provenance={"experiment_version": EXPERIMENT_VERSION}, commit=True)
    pf = preflight(store, market, origins, evaluate_as_of, cfg.get("training_cutoff"))
    bad = [p["origin"] for p in pf if not p["window_resolvable"]]
    if bad:
        raise ValueError(f"test windows not published by {evaluate_as_of} for origins {bad}")
    docs = {k: {} for k in KINDS}
    for o in origins:
        for kind in ("reference", "mock"):
            docs[kind][o] = run_investigation(store, market_path, as_of=o, evaluate_as_of=evaluate_as_of, agents=kind,
                                              log=lambda *_: None)
    plan = _plan_live(docs["mock"], max_usd, cfg)
    smoke = None
    if live:
        smoke = _smoke(store, exp_id, cfg, environ, http_client)
        log(f"smoke: {'OK' if smoke.get('ok') else 'FAILED'} {smoke}")
        if smoke.get("ok"):
            for o in origins:
                router = Router("anthropic", store, scope=exp_id, max_usd=max_usd, environ=environ, http_client=http_client,
                                cfg_overrides=cfg_overrides)
                docs["anthropic"][o] = run_investigation(store, market_path, as_of=o, evaluate_as_of=evaluate_as_of,
                                                         agents="anthropic", router=router, approval_id=approval_id,
                                                         max_usd=max_usd, approval_subject=subject, log=lambda *_: None)
                log(f"{o}: anthropic investigation {docs['anthropic'][o]['status']}")
    spend = store.conn.execute("""SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_usd ELSE reserved_usd END), 0),
        COUNT(*) FROM llm_spend_ledger WHERE run_id LIKE ?""", (exp_id + "%",)).fetchone()
    report = _report(exp_id, market, origins, evaluate_as_of, pf, plan, smoke, docs, spend, max_usd, live, cfg, approval_id)
    out = Path(out_dir) / exp_id
    (out / "investigations").mkdir(parents=True, exist_ok=True)
    for kind, by_o in docs.items():
        for o, d in by_o.items():
            (out / "investigations" / f"{kind}_{o}.json").write_text(json.dumps(d, indent=1, sort_keys=True, default=str) + "\n")
    (out / "comparison.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n")
    (out / "comparison.md").write_text(markdown(report) + "\n")
    (out / "manifest.json").write_text(json.dumps({"experiment_id": exp_id, "version": EXPERIMENT_VERSION,
                                                   "schema": art.SCHEMA_ID, "files": sorted(str(p.relative_to(out))
                                                                                            for p in out.rglob("*.json"))},
                                                  indent=1) + "\n")
    events.append("validation.completed", "L6", {"live": live, "spend_usd": spend[0], "attempts": spend[1],
                                                 "pipeline_ready": report["verdict"]["pipeline_ready"]},
                  run_id=exp_id, subject_id=approval_id, commit=True)
    if live:
        events.append("approval.consumed", "L8", {"spend_usd": spend[0], "cap_usd": max_usd, "experiment": exp_id},
                      subject_id=approval_id, commit=True)
    report["output_dir"] = str(out)
    return report


def _report(exp_id, market, origins, evaluate_as_of, pf, plan, smoke, docs, spend, max_usd, live, cfg, approval_id) -> dict:
    ev = {k: {o: evaluate_document(d) for o, d in by_o.items()} for k, by_o in docs.items()}
    comp = {}
    for o in origins:
        c = {}
        if o in ev["anthropic"]:
            c["anthropic_vs_reference"] = overlap(ev["anthropic"][o], ev["reference"][o])
            c["anthropic_vs_mock"] = overlap(ev["anthropic"][o], ev["mock"][o])
        c["mock_vs_reference"] = overlap(ev["mock"][o], ev["reference"][o])
        c["identical_evidence"] = len({ev[k][o]["panel_fingerprint"] for k in KINDS if o in ev[k]}) == 1
        comp[o] = c
    A, B = {}, {}
    an = ev["anthropic"]
    valid_docs = all(not art.validate(d) for by_o in docs.values() for d in by_o.values())
    if live:
        A["A1_smoke_structured_output_ok"] = bool(smoke and smoke.get("ok"))
        A["A2_all_live_investigations_completed"] = len(an) == len(origins) and all(e["status"] == "completed" for e in an.values())
        A["A3_no_truncation_refusal_or_top_level_schema_failure"] = len(an) == len(origins) and all(
            e["calls"]["truncated"] == 0 and e["calls"]["refusals"] == 0 and e["calls"]["schema_top_level"] == 0 for e in an.values())
        A["A4_documents_validate"] = valid_docs
        A["A5_identical_evidence"] = all(c["identical_evidence"] for c in comp.values())
        A["A6_spend_within_cap"] = spend[0] <= (max_usd or 0) + 1e-12
        if an:
            acc = sum(e["hypotheses"]["accepted"] for e in an.values())
            rej = sum(e["hypotheses"]["rejected"] for e in an.values())
            regs = sum(e["tests"]["registered"] for e in an.values())
            B["B1_evidence_fidelity_ge_0.80"] = (acc + rej) > 0 and acc / (acc + rej) >= 0.80
            B["B2_two_original_per_origin"] = all(e["hypotheses"]["original"] >= 2 for e in an.values())
            B["B3_falsifiability_ge_0.80"] = acc > 0 and sum(e["hypotheses"]["falsifiable"] for e in an.values()) / acc >= 0.80
            tot = sum(e["tests"]["total"] for e in an.values())
            B["B4_testability_ge_0.60"] = tot > 0 and regs / tot >= 0.60
            B["B5_informativeness_ge_0.40"] = regs > 0 and sum(e["tests"]["informative"] for e in an.values()) / regs >= 0.40
    useful = {k: sum(e["hypotheses"]["useful_validated"] for e in by_o.values()) for k, by_o in ev.items()}
    paid = float(spend[0])
    return {"experiment_id": exp_id, "version": EXPERIMENT_VERSION, "market": market.id, "origins": origins,
            "evaluate_as_of": evaluate_as_of, "live": live, "approval_id": approval_id, "model": cfg["model"],
            "training_cutoff": cfg.get("training_cutoff"), "preflight": pf, "plan": plan, "smoke": smoke,
            "evaluations": ev, "comparisons": comp, "criteria": CRITERIA, "criteria_results": {**A, **B},
            "useful_validated_results": useful,
            "cost": {"live_spend_usd": paid, "ledger_attempts": spend[1], "cap_usd": max_usd,
                     "cost_per_useful_result_usd": (paid / useful["anthropic"]) if useful.get("anthropic") else None},
            "verdict": {"pipeline_ready": bool(A) and all(A.values()), "quality_bar_met": bool(B) and all(B.values()),
                        "statistical_note": "two origins give a handful of tests: no claim of superiority over the baselines "
                                            "is possible from this experiment; it establishes feasibility, quality signals and cost",
                        "decision": "the founder decides whether to propose R3"},
            "leakage": {"historical": ["inputs are the latest published vintage, not the vintage known at each origin",
                                       "DfT history before the first retrieval uses ESTIMATED publication dates "
                                       "(quarter end + 121 days); only 2026-Q2 has a recorded publication date",
                                       "the world slice contains only data available at the origin (as-of retrieval)"],
                        "training_data": [x for p in pf for x in p["training_data_leakage"]],
                        "interpretation": "a Claude hypothesis that passes a test on a window published before its training "
                                          "cutoff may reflect remembered outcomes, not reasoning; such results are labelled "
                                          "potentially contaminated and must not be used for promotion"}}


def markdown(r: dict) -> str:
    L = [f"# R2 live validation {r['experiment_id']} ({'LIVE' if r['live'] else 'PLAN / DRY RUN'})", "",
         f"Market `{r['market']}`; origins {', '.join(r['origins'])}; evaluated at {r['evaluate_as_of']}; model `{r['model']}` "
         f"(training cutoff {r['training_cutoff']}).", "", "## Preflight and leakage", ""]
    for p in r["preflight"]:
        L.append(f"- **{p['origin']}**: evidence to {p['evidence_last_quarter']}; test window {', '.join(p['test_window'])} "
                 f"published {p['window_published_by_evaluation']}; basis {p['window_availability_basis']}; "
                 f"**{p['leakage_label']}**")
    L += ["", "## Results by workforce", "",
          "| workforce | origin | status | proposed | accepted | original | restatement | falsifiable | testable | tests | "
          "informative | passed/failed/pending | survived/refuted/untested | useful | calls | tokens in/out | cost $ |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, by_o in r["evaluations"].items():
        for o, e in by_o.items():
            h, t, s = e["hypotheses"], e["tests"], e["hypotheses"]["status"]
            res = t["results"]
            L.append(f"| {k} | {o} | {e['status']} | {h['proposed']} | {h['accepted']} | {h['original']} | {h['restatements']} | "
                     f"{h['falsifiable']} | {h['testable']} | {t['registered']}/{t['total']} | {t['informative']} | "
                     f"{res.get('passed', 0)}/{res.get('failed', 0)}/{res.get('pending', 0)} | "
                     f"{s.get('survived', 0)}/{s.get('refuted', 0)}/{s.get('untested', 0)} | {h['useful_validated']} | "
                     f"{e['calls']['n']} | {e['calls']['input_tokens']}/{e['calls']['output_tokens']} | {e['cost_usd']:.6f} |")
    L += ["", "## Criteria (pre-registered)", ""]
    for k, desc in r["criteria"].items():
        v = r["criteria_results"].get(k)
        L.append(f"- {'PASS' if v else 'FAIL' if v is False else 'n/a (dry run)'} **{k}**: {desc}")
    L += ["", f"Live spend ${r['cost']['live_spend_usd']:.6f} of cap ${r['cost']['cap_usd']}; plan worst case "
              f"${r['plan']['worst_case_usd']:.4f} (with retries ${r['plan']['worst_case_with_retries_usd']:.4f}); "
              f"cost per useful result: {r['cost']['cost_per_useful_result_usd']}.", "",
          f"Verdict: pipeline ready = {r['verdict']['pipeline_ready']}; quality bar met = {r['verdict']['quality_bar_met']}. "
          f"{r['verdict']['statistical_note']}. {r['verdict']['decision']}.", "", "## Leakage", ""]
    L += [f"- {x}" for x in r["leakage"]["historical"] + r["leakage"]["training_data"]] + ["", r["leakage"]["interpretation"]]
    return "\n".join(L)
