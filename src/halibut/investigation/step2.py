"""R2 Step 2: pre-registered benchmark of the loop.v2 reasoning contract (prepared offline; LIVE RUNS NEED APPROVAL).

Design: matched factorial arms on IDENTICAL evidence, fresh held-out origins, a prospective origin and a second
market. Step 1 stays the historical engineering benchmark, and its dates are the development set: they are never
used for comparisons.

| Arm | Contract | Measurements | Isolates |
|---|---|---|---|
| A_v1_base | loop.v1 | base | control (the Step 1 contract) |
| B_v2_base | loop.v2 | base | B vs A: the prompts and reasoning contract |
| C_v2_extended | loop.v2 | extended | C vs B: the expanded measurements |

Model changes are a LATER factor: the same arms rerun with another model, after these results. Every arm uses the
same model (routing default), the same panel snapshot and the same origins.

Runs (each with its own single-use approval; each worst case is within the ADR-019 self-approval limit):

| Run | Market and origins | Arms |
|---|---|---|
| 2a: matched factor run | UK BEV, held-out 2025-08-23 and 2026-02-23 (resolved by 2026-11-23) | A, B, C |
| 2b: prospective, second market and critic benchmark | UK BEV 2026-11-23 (tests on 2026-Q3/Q4, after the model's training cutoff, unpublished now; registered and resolved later); UK hybrid-electric (petrol) 2026-02-23 (second market, same source, different segment); the blinded critic benchmark (2 critic calls, UK BEV 2026-02-23 evidence) | A, C |

Leakage labels:
- retrospective origins: outcome windows before the model's training cutoff (June 2026) may have been seen in
  training;
- prospective: outcomes did not exist at the call.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

from ..agents.loop_agents import call_role
from ..agents.routing import Router, load_routing
from ..control import approvals
from ..data.store import Store, utc_now
from ..epistemic import schemas as sch
from ..epistemic.events import EventLog
from ..intel.market import load_market
from ..world.context import build_slice
from ..world.panel import build_panel
from . import artifact as art
from . import critic_benchmark as cb
from . import loop_v2 as v2
from .evaluation import evaluate_document
from .live_validation import execution_records
from .loop import InvestigationInternalError, run_investigation

VERSION = "r2_step2_benchmark.v1"
ROOT = Path(__file__).resolve().parents[3]
BEV = "configs/intel/markets/uk_cars_bev_v1.toml"
HEV = "configs/intel/markets/uk_cars_hev_v1.toml"
EVAL = "2026-11-23"
ARMS = {"A_v1_base": ("loop.v1", "base"), "B_v2_base": ("loop.v2", "base"), "C_v2_extended": ("loop.v2", "extended")}
RUNS = {
    "2a": {"investigations": [(BEV, o, arm, EVAL, "retrospective_held_out") for o in ("2025-08-23", "2026-02-23")
                              for arm in ARMS], "benchmark": None},
    "2b": {"investigations": [(BEV, None, arm, None, "prospective") for arm in ("A_v1_base", "C_v2_extended")]
           + [(HEV, "2026-02-23", arm, EVAL, "retrospective_second_market") for arm in ("A_v1_base", "C_v2_extended")],
           "benchmark": (BEV, "2026-02-23")}}
CRITERIA = {  # pre-registered BEFORE any Step 2 call; computed on Claude output with ONE method for every arm
    "A1_smoke_with_defs_ok": "the structured-output smoke call with a $defs/$ref schema returns {\"ok\": true}",
    "A2_all_investigations_completed": "every live investigation has status 'completed' (no internal error)",
    "A3_no_truncation_refusal_or_top_level_failure": "no live call truncated, refused or rejected as a whole",
    "A4_documents_validate": "every document validates against halibut.investigation_result.v1",
    "A5_matched_evidence": "all arms at the same market and origin saw the same panel (fingerprint)",
    "A6_spend_within_cap": "total live spend (smoke, retries and failed-attempt reservations) <= the approved cap",
    # intelligence: COMMON method (evaluation._common), identical for arms A, B and C
    "B1_fidelity_ge_0.80": "arm C: accepted / (accepted + rejected) hypotheses >= 0.80",
    "B2_original_ge_2_per_investigation": "arm C: >= 2 original hypotheses (common, library-based definition) per investigation",
    "B3_competing_pair_per_investigation": "arm C: >= 1 competing pair passing the mechanism gates in every investigation",
    "B4_informativeness_ge_0.40": "arm C: >= 40% of registered tests informative (common definition)",
    "B5_testability_ge_0.60": "arm C: >= 60% of implications compile into registered tests",
    "B6_combined_effect_ge_0.20": "informativeness (common) of arm B minus arm A >= +0.20: the effect of the reasoning "
                                  "contract AND evidence presentation together (not prompt wording alone; descriptive)",
    # contract compliance (v2 only): reported, never an intelligence score against v1
    "K1_gap_distinguishing_rivals": "arm C: every investigation has >= 1 information gap distinguishing >= 2 hypotheses",
    "K2_options_pass_v2_ge_0.50": "arm C: >= 50% of proposed options pass v2 validation (comparators, agreement, numbers)",
    # critic benchmark (2b)
    "C1_critic_recall_ge_0.60": "benchmark: >= 60% of planted defects detected (target, dimension and evidence)",
    "C2_critic_precision_ge_0.60": "benchmark: >= 60% of medium/high critiques detect a planted defect",
    "C3_clean_false_alarms_le_0.25": "benchmark: <= 25% of clean controls receive a high-severity critique",
    "C4_evidence_reference_accuracy_ge_0.80": "benchmark: >= 80% of evidence-citing critiques on evidence-bound defects cite an expected metric",
    # safeguards and efficiency
    "D1_no_ineligible_protocol_executable": "no drafted protocol of any arm is eligible unless every eligibility check passes",
    "E1_generator_tokens_not_above_v1": "arm B generator input tokens (API usage) <= arm A at the same origin",
    # prospective (2b)
    "P1_registered_at_run_time_with_future_window": "every prospective test is registered at the real call time, on data "
                                                    "retrieved <= 7 days before, with >= 1 'future' window",
    "P2_no_published_period_counted_prospective": "no target period already published at registration is scored as prospective",
}
PROSPECTIVE_PROTOCOL = {  # frozen with the criteria (in CRITERIA_HASH) before any registration or outcome
    "registration": "at the real call time (utc_now), never backdated; evidence as of the registration date",
    "publication_check": "DfT VEH0160 retrieved <= 7 days before registration (snapshots.retrieved_at), else refused",
    "windows": "nominal (next two quarters after the last published one), each period labelled already_published, "
               "unpublished_historical, partially_elapsed (nowcast) or future; PLUS the agent's implication on the first "
               "two quarters starting after registration (engine horizon extension, identical for every arm)",
    "resolution": "each test resolves once on the FIRST published vintage containing all its target periods "
                  "(compiler.resolve on a panel as of that publication); later revisions are ignored",
    "scoring": "informative confirmations and refutations by window class, reported separately; already_published "
               "periods are never prospective; predictive support is not causal proof",
    "no_tuning": "prompts, contracts, schemas, thresholds and this protocol are frozen by CRITERIA_HASH; nothing is "
                 "rerun or re-scored after outcomes are seen",
}
MAX_DATA_AGE_DAYS = 7
THRESHOLDS = {"B1": 0.80, "B4": 0.40, "B5": 0.60, "B6": 0.20, "K2": 0.50, "C1": 0.60, "C2": 0.60, "C3": 0.25, "C4": 0.80}
RETRIES = 0  # Step 2 live calls are not retried, so the worst case INCLUDING retries fits the cap (a transient failure
#              stops that investigation, recorded; it is never silently re-bought)
CRITERIA_HASH = hashlib.sha256(json.dumps([VERSION, CRITERIA, THRESHOLDS, ARMS, RUNS, PROSPECTIVE_PROTOCOL, RETRIES,
                                           MAX_DATA_AGE_DAYS], sort_keys=True).encode()).hexdigest()
SMOKE_SCHEMA_DEFS = {"type": "object", "properties": {"ok": {"$ref": "#/$defs/flag"}}, "required": ["ok"],
                     "additionalProperties": False, "$defs": {"flag": {"type": "boolean"}}}
REPORTED_SEPARATELY = ("informative_confirmations", "informative_refutations", "validated_useful_discoveries",
                       "prospective_pending_tests")


def _copy_db(db_path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="halibut_step2_")) / "copy.sqlite"
    a, b = sqlite3.connect(db_path), sqlite3.connect(tmp)
    a.backup(b)
    a.close(), b.close()
    return tmp


def _cfg(provider_config=None, overrides=None):
    from ..agents.llm import load_llm_config  # noqa: PLC0415
    rt = load_routing()
    path = Path(provider_config) if provider_config else ROOT / rt["provider_config"]
    return {**load_llm_config(path, model=rt["default_model"]), **(overrides or {}), "retries": RETRIES}, path


def plan(db_path, run: str, cfg: dict, max_usd: float | None, runs: dict | None = None) -> dict:
    """Worst-case cost from the REAL prompt and schema sizes (mock run on a throwaway store copy; no network)."""
    RUNS_ = runs or RUNS
    from ..agents.anthropic_provider import pricing_from_config  # noqa: PLC0415
    pr, rt, rows = pricing_from_config(cfg), load_routing(), []
    tmp = _copy_db(db_path)
    st = Store(tmp)
    try:
        for market, origin, arm, ev, cls in RUNS_[run]["investigations"]:
            contract, meas = ARMS[arm]
            d = run_investigation(st, ROOT / market, as_of=origin, evaluate_as_of=ev, agents="mock", contract=contract,
                                  measurements=meas, prospective=cls == "prospective", log=lambda *_: None)
            gen = next(c for c in d["agents"]["calls"] if c["role"] == "hypothesis_generator")
            g = (gen["prompt_chars"] or 0) + (gen["schema_chars"] or 0)
            metric_chars = len(json.dumps(d["evidence"]["metrics"])) // 4 if contract == v2.CONTRACT else 0
            sizes = {"hypothesis_generator": g, "loop_critic": 4000 + 36000 + 6000 + metric_chars,
                     "strategist": g + 15000 + 12000}
            for role, chars in sizes.items():
                rows.append({"market": Path(market).stem, "origin": origin, "arm": arm, "role": role, "chars": chars,
                             "worst_case_usd": pr.worst_case("x" * chars, rt["roles"][role]["max_output_tokens"])})
        if RUNS_[run]["benchmark"]:
            for _ in range(2):
                rows.append({"market": "benchmark", "origin": RUNS_[run]["benchmark"][1], "arm": "critic_benchmark",
                             "role": "loop_critic", "chars": 30000,
                             "worst_case_usd": pr.worst_case("x" * 30000, rt["roles"]["loop_critic"]["max_output_tokens"])})
    finally:
        st.close()
        shutil.rmtree(tmp.parent, ignore_errors=True)
    smoke = pr.worst_case("x" * 600, 20)
    calls = sum(r["worst_case_usd"] for r in rows)
    retries = 1 + min(int(cfg.get("retries", 1)), 2)
    worst = calls * retries + smoke
    return {"run": run, "model": cfg["model"], "live_calls": 1 + len(rows), "rows": rows, "smoke_worst_case_usd": smoke,
            "worst_case_usd": worst, "worst_case_with_retries_usd": worst, "retries": retries - 1, "cap_usd": max_usd,
            "fits_cap": max_usd is not None and worst <= max_usd,
            "note": "includes the smoke test, every retry and failed-attempt reservations (fail-closed)"}


def publication_check(store, registered_at: str | None = None) -> dict:
    """How fresh the DfT publication information is at registration (prospective runs refuse stale data)."""
    from datetime import datetime, timedelta  # noqa: PLC0415
    row = store.conn.execute("""SELECT MAX(retrieved_at), MAX(publisher_updated) FROM snapshots
        WHERE dataset_id LIKE 'dft_veh0160%'""").fetchone()
    latest = store.conn.execute("""SELECT MAX(o.period) FROM observations o JOIN series s USING(series_id)
        WHERE s.dataset_id='dft_veh0160_makes' AND o.period LIKE '____-Q_'""").fetchone()[0]
    reg = registered_at or utc_now()
    fresh = bool(row and row[0]) and datetime.fromisoformat(reg.replace("Z", "+00:00")) - \
        datetime.fromisoformat(row[0].replace("Z", "+00:00")) <= timedelta(days=MAX_DATA_AGE_DAYS)
    return {"retrieved_at": row[0] if row else None, "publisher_updated": row[1] if row else None,
            "latest_published_quarter": latest, "checked_at": reg, "fresh": fresh, "max_age_days": MAX_DATA_AGE_DAYS}


def _smoke_defs(store, scope, cfg, environ, http_client) -> dict:
    from ..agents.anthropic_provider import AnthropicProvider  # noqa: PLC0415
    from ..agents.cli import SMOKE_SYSTEM, SMOKE_USER  # noqa: PLC0415
    q = AnthropicProvider({**cfg, "max_spend_usd_per_run": min(0.001, float(cfg["max_spend_usd_per_run"])), "retries": 0},
                          store, f"{scope}:smoke", environ=environ, http_client=http_client)
    try:
        r = q.complete(SMOKE_SYSTEM, SMOKE_USER, role="smoke", max_output_tokens=20, run_id=f"{scope}:smoke",
                       schema=SMOKE_SCHEMA_DEFS)
    except Exception as exc:  # noqa: BLE001  (recorded; the experiment stops)
        return {"ok": False, "error": str(exc)[:300]}
    return {"ok": r.data == {"ok": True}, "data": r.data, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "cost_usd": r.actual_cost_usd, "stop_reason": getattr(r, "stop_reason", None)}


def run_benchmark(store, router, market_path, origin, run_id) -> dict:
    """Blinded critic benchmark: one loop.v2 critic call per set; accepted critiques are scored against the key."""
    market = dataclasses.replace(load_market(ROOT / market_path), as_of=origin)
    panel = build_panel(store, market, origin)
    slice_, metrics = build_slice(market, panel, {"status": "covered", "contract": {}, "gaps": []}, [], extended=True)
    agent_slice = v2.compact_slice(slice_, metrics)
    bench = cb.build(panel, metrics)
    accepted, calls, rejected = {}, [], []
    for s in bench["sets"]:
        refs = [h["ref"] for h in s["hypotheses"]]
        schema = v2.critic_schema(refs, list(metrics))
        res = call_role(router, store, "loop_critic", cb.payload(s, agent_slice), schema, f"{run_id}:{s['set_id']}",
                        system=v2.SYSTEMS["loop_critic"])
        calls.append({k: res.get(k) for k in ("outcome", "failure_class", "prompt_chars", "schema_chars", "stop_reason")}
                     | {"input_tokens": getattr(res.get("response"), "input_tokens", 0),
                        "output_tokens": getattr(res.get("response"), "output_tokens", 0),
                        "cost_usd": getattr(res.get("response"), "actual_cost_usd", None)})
        ok = []
        if res["data"] is not None:
            items, rej, top = sch.split_items(res["data"], v2.inline(schema), "critiques")
            rejected += [{"set": s["set_id"], "problems": p} for _, _, p in rej] + ([{"set": s["set_id"], "top": top}] if top else [])
            for c in items:
                pr = v2.critique_problems(c)
                (rejected.append({"set": s["set_id"], "problems": pr}) if pr else ok.append(c))
        accepted[s["set_id"]] = ok
    return {"benchmark": {k: bench[k] for k in ("version", "seed", "key_sha256", "payload_sha256")},
            "score": cb.score(accepted, bench), "critiques": accepted, "rejected": rejected, "calls": calls,
            "key": bench["key"]}


def _rate(evs: list[dict], key: str = "informative") -> float | None:
    num = sum(e["common"]["tests"][key] for e in evs)
    den = sum(e["common"]["tests"]["registered"] for e in evs)
    return num / den if den else None


def criteria_results(docs: dict, evs: dict, bench: dict | None, smoke: dict | None, spend: float, cap: float | None,
                     pub: dict | None = None) -> dict:
    """docs/evs keyed by (market, origin, arm). Intelligence criteria use evaluation['common'] (one method for every
    arm); compliance criteria (K*) are reported apart. Criteria a run cannot evaluate are None, never False."""
    T = THRESHOLDS
    by_arm = lambda a: [evs[k] for k in evs if k[2] == a]  # noqa: E731
    C, A_, B_ = by_arm("C_v2_extended"), by_arm("A_v1_base"), by_arm("B_v2_base")
    calls = [c for d in docs.values() for c in d["agents"]["calls"]]
    fp = {}
    for (m, o, _), d in docs.items():
        fp.setdefault((m, o), set()).add(d["evidence"]["panel"].get("fingerprint"))
    r = {"A1_smoke_with_defs_ok": bool(smoke and smoke.get("ok")),
         "A2_all_investigations_completed": bool(docs) and all(d["status"] == "completed" for d in docs.values()),
         "A3_no_truncation_refusal_or_top_level_failure": bool(docs) and not any(
             c["failure_class"] in ("call.truncated", "call.refusal", "call.schema_top_level") for c in calls),
         "A4_documents_validate": bool(docs) and all(art.validate(d) == [] for d in docs.values()),
         "A5_matched_evidence": bool(fp) and all(len(v) == 1 for v in fp.values()),
         "A6_spend_within_cap": cap is not None and spend <= cap + 1e-12}
    if C:
        acc = sum(e["hypotheses"]["accepted"] for e in C)
        rej = sum(e["hypotheses"]["rejected"] for e in C)
        reg = sum(e["tests"]["registered"] for e in C)
        tot = sum(e["tests"]["total"] for e in C)
        props = [(e["strategy"]["options"], e["strategy"]["rejected"]) for e in C]
        n_opt, n_rej = sum(x[0] for x in props), sum(x[1] for x in props)
        r.update({"B1_fidelity_ge_0.80": (acc / (acc + rej) >= T["B1"]) if acc + rej else False,
                  "B2_original_ge_2_per_investigation": all(e["common"]["hypotheses_original"] >= 2 for e in C),
                  "B3_competing_pair_per_investigation": all(len(e["common"]["competing_pairs"]) >= 1 for e in C),
                  "B4_informativeness_ge_0.40": (_rate(C) or 0) >= T["B4"],
                  "B5_testability_ge_0.60": (reg / tot >= T["B5"]) if tot else False,
                  "K1_gap_distinguishing_rivals": all((e.get("v2") or {}).get("information_gaps", {}).get(
                      "distinguishing_two_or_more", 0) >= 1 for e in C),
                  "K2_options_pass_v2_ge_0.50": (n_opt / (n_opt + n_rej) >= T["K2"]) if n_opt + n_rej else False})
    if A_ and B_:
        a, b = _rate(A_), _rate(B_)
        r["B6_combined_effect_ge_0.20"] = (b - a >= T["B6"]) if a is not None and b is not None else False
        pairs = [(evs[k], evs[(k[0], k[1], "A_v1_base")]) for k in evs if k[2] == "B_v2_base" and (k[0], k[1], "A_v1_base") in evs]
        r["E1_generator_tokens_not_above_v1"] = bool(pairs) and all(
            b_["roles"]["hypothesis_generator"]["input_tokens"] <= a_["roles"]["hypothesis_generator"]["input_tokens"]
            for b_, a_ in pairs)
    r["D1_no_ineligible_protocol_executable"] = all(
        (d["experiment_protocol"] is None) or (d["experiment_protocol"]["data"]["eligibility"]["eligible_for_execution"]
                                               == (not d["experiment_protocol"]["data"]["eligibility"]["reasons"]))
        for d in docs.values())
    if bench:
        sc = bench["score"]
        r.update({"C1_critic_recall_ge_0.60": (sc["recall"] or 0) >= T["C1"],
                  "C2_critic_precision_ge_0.60": (sc["precision"] or 0) >= T["C2"],
                  "C3_clean_false_alarms_le_0.25": sc["clean_false_alarm_rate"] is not None
                  and sc["clean_false_alarm_rate"] <= T["C3"],
                  "C4_evidence_reference_accuracy_ge_0.80": (sc["evidence_reference_accuracy"] or 0) >= T["C4"]})
    pros = [d for d in docs.values() if any(t["data"].get("horizon") for t in d["stages"]["tests"])]
    if pros:
        tests = [t for d in pros for t in d["stages"]["tests"] if t["data"].get("horizon")]
        r["P1_registered_at_run_time_with_future_window"] = bool(pub and pub["fresh"]) and all(
            any(c == "future" for c in t["data"]["window_classes"].values()) for t in tests
            if t["data"]["horizon"] == "future_window") and any(t["data"]["horizon"] == "future_window" for t in tests)
        r["P2_no_published_period_counted_prospective"] = all(
            "already_published" not in t["data"]["window_classes"].values() for t in tests
            if t["data"]["horizon"] == "future_window")
    return {k: r.get(k) for k in CRITERIA}


def summarize(docs: dict, evs: dict, bench: dict | None) -> dict:
    """Per-arm pooled COMMON metrics, contrasts, separately reported learning, compliance, and measured costs."""
    arms = {}
    for a in ARMS:
        E = [evs[k] for k in evs if k[2] == a]
        if not E:
            continue
        cost = sum(e["cost_usd"] for e in E)
        useful = [u for e in E for u in e["common"]["validated_useful_results"]]
        roles = {}
        for e in E:
            for role, v in e["roles"].items():
                x = roles.setdefault(role, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0,
                                            "cache_hits": 0})
                for f in x:
                    x[f] += v.get(f, 0) or 0
        arms[a] = {"investigations": len(E), "hypotheses_accepted": sum(e["hypotheses"]["accepted"] for e in E),
                   "original_common": sum(e["common"]["hypotheses_original"] for e in E),
                   "competing_pairs": sum(len(e["common"]["competing_pairs"]) for e in E),
                   "registered_tests": sum(e["common"]["tests"]["registered"] for e in E),
                   "informativeness": _rate(E), "disputed_tests": sum(e["common"]["tests"]["disputed"] for e in E),
                   "informative_confirmations": sum(e["common"]["tests"]["informative_confirmations"] for e in E),
                   "informative_refutations": sum(e["common"]["tests"]["informative_refutations"] for e in E),
                   "discriminating_resolutions": sum(e["common"]["resolutions"]["discriminating"] for e in E),
                   "validated_useful_results": len(useful),
                   "cost_usd": {"incremental": cost, "historical_reused": 0.0},
                   "cost_per_validated_useful_result_usd": (cost / len(useful)) if useful else None,
                   "per_role": roles,
                   "compliance_v2": [e["v2"] for e in E] if E[0].get("v2") else None}
    ctr = {}
    if "A_v1_base" in arms and "B_v2_base" in arms:
        ctr["combined_contract_and_evidence_presentation_B_minus_A"] = {
            "informativeness": _diff(arms, "B_v2_base", "A_v1_base", "informativeness"),
            "note": "B differs from A in prompts, schemas, compact evidence presentation and the critic seeing the evidence: "
                    "the contrast is their COMBINED effect, not prompt wording alone"}
    if "B_v2_base" in arms and "C_v2_extended" in arms:
        ctr["measurement_effect_C_minus_B"] = {"informativeness": _diff(arms, "C_v2_extended", "B_v2_base", "informativeness")}
    pending = [{"investigation": d["investigation_id"], "test": t["id"], "spec_hash": t["data"]["spec_hash"],
                "horizon": t["data"].get("horizon"), "window_classes": t["data"].get("window_classes"),
                "registered_at": t["data"].get("registered_at")}
               for d in docs.values() for t in d["stages"]["tests"] if t["status"] == "registered" and t["data"].get("horizon")]
    return {"arms": arms, "contrasts": ctr, "prospective_pending_tests": pending,
            "critic_benchmark": bench["score"] if bench else None,
            "note": "one assessment method (evaluation.common) for every arm; compliance reported apart; small samples: "
                    "descriptive contrasts; model effects are a later factor"}


def _diff(arms, a, b, k):
    x, y = arms[a][k], arms[b][k]
    return None if x is None or y is None else x - y


def run_step2(store, db_path, run: str, *, live: bool = False, approval_id: str | None = None, max_usd: float | None = None,
              out_dir="results/r2_step2", environ=None, http_client=None, cfg_overrides=None, provider_config=None,
              runs: dict | None = None, authorized_2b: bool = False, log=print) -> dict:
    """``runs``: tests only (another market); the pre-registered RUNS are the default. Run 2b is separately
    authorized: a live 2b run needs ``authorized_2b=True`` (CLI --authorized-2b) in addition to its approval."""
    RUNS_ = runs or RUNS
    if run not in RUNS_:
        raise ValueError(f"unknown Step 2 run {run!r}; known: {sorted(RUNS)}")
    cfg, cfg_path = _cfg(provider_config, cfg_overrides)
    exp_id = f"r2s{run}-" + hashlib.sha256(json.dumps([run, utc_now(), uuid.uuid4().hex]).encode()).hexdigest()[:12]
    subject = f"r2_step2:{run}"
    pl = plan(db_path, run, cfg, max_usd, runs)
    log(f"plan: {pl['live_calls']} live calls; worst case ${pl['worst_case_usd']:.4f} "
        f"(with retries ${pl['worst_case_with_retries_usd']:.4f}); cap {max_usd}")
    prospective = any(x[4] == "prospective" for x in RUNS_[run]["investigations"])
    pub = publication_check(store) if prospective else None
    if live:
        if run == "2b" and runs is None and not authorized_2b:
            raise approvals.AuthorizationError("run 2b is separately authorized (pass --authorized-2b once approved); nothing was sent")
        if prospective and not pub["fresh"]:
            raise approvals.AuthorizationError(f"prospective registration needs DfT data retrieved <= {MAX_DATA_AGE_DAYS} days "
                                               f"ago (latest {pub['retrieved_at']}): refresh the data first; nothing was sent")
        if not pl["fits_cap"]:
            raise approvals.AuthorizationError(f"worst case ${pl['worst_case_usd']:.4f} exceeds the cap {max_usd}; nothing was sent")
        approvals.require(store, approval_id, "paid_run", subject, cost_usd=max_usd)
        approvals.claim_use(store, approval_id, exp_id, f"R2 Step 2 run {run} ({VERSION})")
    EventLog(store).append("step2.started", "L6", {"run": run, "live": live, "criteria_hash": CRITERIA_HASH, "max_usd": max_usd},
                           run_id=exp_id, subject_id=approval_id, commit=True)
    smoke, docs, internal, bench = None, {}, {}, None
    if live:
        smoke = _smoke_defs(store, exp_id, cfg, environ, http_client)
        log(f"smoke ($defs schema): {'OK' if smoke.get('ok') else 'FAILED'}")
        if smoke.get("ok"):
            for market, origin, arm, ev, cls in RUNS_[run]["investigations"]:
                contract, meas = ARMS[arm]
                key = (Path(market).stem, origin or "registration_date", arm)
                router = Router("anthropic", store, scope=exp_id, max_usd=max_usd, environ=environ, http_client=http_client,
                                cfg_overrides=cfg_overrides, provider_config=cfg_path)
                try:
                    docs[key] = run_investigation(store, ROOT / market, as_of=origin, evaluate_as_of=ev, agents="anthropic",
                                                  router=router, approval_id=approval_id, max_usd=max_usd,
                                                  approval_subject=subject, contract=contract, measurements=meas,
                                                  prospective=cls == "prospective", log=lambda *_: None)
                except InvestigationInternalError as exc:  # stored as 'stopped'; stop paying for a defective engine
                    docs[key], internal["/".join(key)] = exc.doc, exc.doc["stop_reason"]
                    break
                log(f"{'/'.join(key)}: {docs[key]['status']} [{docs[key]['failures']['completion']['state']}]")
            if RUNS_[run]["benchmark"] and not internal:
                router = Router("anthropic", store, scope=exp_id, max_usd=max_usd, environ=environ, http_client=http_client,
                                cfg_overrides=cfg_overrides, provider_config=cfg_path)
                bench = run_benchmark(store, router, *RUNS_[run]["benchmark"], f"{exp_id}:benchmark")
                log(f"critic benchmark: recall {bench['score']['recall']}, precision {bench['score']['precision']}")
    out = Path(out_dir) / exp_id
    (out / "investigations").mkdir(parents=True, exist_ok=True)
    files = []
    for key, d in docs.items():
        name = f"investigations/claude_{'_'.join(key)}.json"
        (out / name).write_text(json.dumps(d, indent=1, sort_keys=True, default=str) + "\n")
        files.append({"file": name, "key": list(key), "content_hash": d["content_hash"], "status": d["status"]})
    (out / "execution_records.json").write_text(json.dumps({**execution_records(store, exp_id, approval_id, {"anthropic": {}}),
                                                            "internal_errors": internal}, indent=1, sort_keys=True,
                                                           default=str) + "\n")
    spend = float(store.conn.execute("""SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_usd ELSE reserved_usd END),
        0) FROM llm_spend_ledger WHERE run_id LIKE ?""", (exp_id + "%",)).fetchone()[0])
    evs = {k: evaluate_document(d) for k, d in docs.items()}
    for e in evs.values():  # gaps that distinguish >= 2 hypotheses (B7)
        if e.get("v2"):
            e["v2"]["information_gaps"]["distinguishing_two_or_more"] = 0
    for k, d in docs.items():
        if evs[k].get("v2"):
            evs[k]["v2"]["information_gaps"]["distinguishing_two_or_more"] = sum(
                1 for g in d["stages"].get("information_gaps", []) if len(g["data"]["distinguishes"]) >= 2)
    report = {"experiment_id": exp_id, "version": VERSION, "run": run, "live": live, "criteria": CRITERIA,
              "thresholds": THRESHOLDS, "criteria_hash": CRITERIA_HASH, "plan": pl, "smoke": smoke,
              "prospective_protocol": PROSPECTIVE_PROTOCOL, "publication_check": pub,
              "criteria_results": criteria_results(docs, evs, bench, smoke, spend, max_usd, pub) if live else {},
              "summary": summarize(docs, evs, bench), "critic_benchmark": bench, "spend_usd": spend,
              "internal_errors": internal, "evaluations": {"/".join(k): v for k, v in evs.items()},
              "label": ("R2 Step 2 pre-registered benchmark: fresh Claude calls on held-out and prospective origins; matched "
                        "arms on identical evidence. Retrospective outcome windows may predate the model's training cutoff.")}
    if internal:
        report["verdict"] = "internal_error"
    (out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n")
    (out / "manifest.json").write_text(json.dumps({"experiment_id": exp_id, "version": VERSION, "run": run, "live": live,
                                                   "criteria_hash": CRITERIA_HASH, "investigations": files}, indent=1) + "\n")
    EventLog(store).append("step2.completed", "L6", {"run": run, "spend_usd": spend}, run_id=exp_id, subject_id=approval_id,
                           commit=True)
    report["output_dir"] = str(out)
    return report
