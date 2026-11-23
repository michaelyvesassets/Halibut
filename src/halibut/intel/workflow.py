"""End-to-end market investigation workflow.

    market spec -> plan & budget -> entity resolution (Wikidata) -> evidence
    (DfT transactions, World Bank macro, OSM footprint, GDELT media sample)
    -> derived signals (as of the market's date) -> prior lessons -> hypotheses
    -> critiques -> validation -> scenario drafts (validated, synthetic preview)
    -> report; the run is recorded as an experiment in the learning memory and
    every hypothesis/prediction/critique as proposed knowledge.

Provider failures are recorded and reported; nothing is substituted.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

from ..config import parse_config
from ..data.http import Budget, BudgetExhausted, PoliteClient
from ..data.providers import dft, gdelt, overpass, wikidata, worldbank
from ..data.store import to_timestamp, utc_now
from ..learning.lineage import lineage
from ..learning.memory import LearningMemory
from ..validation import check_action, validate_hypothesis
from . import signals as sig
from .agents import ReferenceCritic, ReferenceHypothesisAgent, ReferenceScenarioDrafter
from .market import load_market
from .retrieval import StoreEvidenceRetriever

PROVIDER_INTERVALS = {"wikidata": 2.0, "dft": 1.0, "worldbank": 1.0, "osm": 10.0, "gdelt": 6.0}


def plan(market) -> dict:
    n = len(market.competitors)
    gd = gdelt.sample_plan(market.media["start"], market.media["end"], market.media["n_files"],
                           market.media.get("time_of_day", "120000")) if market.media else []
    steps = {"wikidata": {"requests": 2 * n, "bytes": 60_000 * n, "seconds": 3 * n},
             "dft": {"requests": 2, "bytes": 11_000_000, "seconds": 10},
             "worldbank": {"requests": len(market.worldbank_indicators), "bytes": 20_000 * len(market.worldbank_indicators),
                           "seconds": 25 * len(market.worldbank_indicators)},
             "osm": {"requests": 1 if market.footprint else 0, "bytes": 3_000_000, "seconds": 150},
             "gdelt": {"requests": len(gd), "bytes": gdelt.AVG_FILE_BYTES * len(gd), "seconds": 8 * len(gd)}}
    total = {k: sum(s[k] for s in steps.values()) for k in ("requests", "bytes", "seconds")}
    return {"steps": steps, "total": total, "gdelt_files": gd}


def investigate(store, market_path, out_dir="results/intel", raw_dir="data/raw", offline=False, log=print,
                transport=None) -> dict:
    market = load_market(market_path)
    spec_text = Path(market_path).read_text()
    run_id = "run:" + hashlib.sha256((spec_text + utc_now()).encode()).hexdigest()[:12]
    memory = LearningMemory(store)
    pl = plan(market)
    budget = Budget(max_requests=market.limits["max_requests"], max_bytes=int(market.limits["max_megabytes"] * 1e6),
                    max_seconds=market.limits["max_seconds"])
    exp_id = memory.record_experiment("intel_investigation", f"investigate {market.id}", "engine:intel_workflow",
                                      lineage(store, config={"market_spec": spec_text}, as_of=market.as_of,
                                              extra={"plan": pl["total"], "offline": offline}))
    store.conn.execute("INSERT INTO intel_runs VALUES (?,?,?,?,?,?,?)",
                       (run_id, market.id, market.as_of, utc_now(), None, "running", json.dumps({"experiment": exp_id})))
    store.conn.commit()
    steps, t0 = {}, time.monotonic()

    def step(name, fn):
        s0 = time.monotonic()
        if offline and name != "derive":
            steps[name] = {"status": "skipped (offline)"}
            return None
        try:
            r = fn()
            steps[name] = {"status": "ok", "seconds": round(time.monotonic() - s0, 1), "detail": r}
        except BudgetExhausted as exc:
            steps[name] = {"status": "budget_exhausted", "seconds": round(time.monotonic() - s0, 1), "error": str(exc)}
        except Exception as exc:  # recorded, reported, never substituted
            steps[name] = {"status": "failed", "seconds": round(time.monotonic() - s0, 1), "error": str(exc)[:400],
                           "trace": traceback.format_exc(limit=2)[-600:]}
        log(f"  {name:<12} {steps[name]['status']:<16} {steps[name].get('seconds', 0):>6}s  {budget.as_dict()['bytes'] / 1e6:.1f} MB used")
        return steps[name].get("detail")

    client = lambda prov: PoliteClient(prov, budget, store, min_interval=0 if transport else PROVIDER_INTERVALS[prov],  # noqa: E731
                                       retries=1, run_id=run_id, transport=transport,
                                       **({"sleep": lambda s: None} if transport else {}))
    resolutions = {}

    def resolve():
        c = client("wikidata")
        for comp in market.competitors:
            resolutions[comp.id] = wikidata.resolve_competitor(c, store, raw_dir, comp, list(market.type_hints),
                                                               list(market.keywords), market.as_of)
        return {k: {"method": v["method"], "qid": v.get("qid"), "label": v.get("label")} for k, v in resolutions.items()}
    step("entities", resolve)
    if offline:  # brands must still exist as entities
        from ..data.providers.base import upsert_entity  # noqa: PLC0415
        for comp in market.competitors:
            upsert_entity(store, f"brand:{comp.id}", "brand", comp.name, "asserted", "market_spec", {"aliases": list(comp.aliases)})
    brands = {c.id: f"brand:{c.id}" for c in market.competitors}

    def transactions():
        c = client("dft")
        url, updated = dft.locate(c)
        have = store.conn.execute("SELECT COUNT(*) FROM snapshots WHERE dataset_id=? AND publisher_updated=?",
                                  ("dft_veh0160_makes", to_timestamp(updated))).fetchone()[0]
        if have:
            return {"reused_snapshot": True, "publisher_updated": updated}
        return dft.ingest(store, c, raw_dir, {comp.dft_make: brands[comp.id] for comp in market.competitors if comp.dft_make},
                          market.transactions.get("body_type", "Cars"))
    step("transactions", transactions)
    step("macro", lambda: worldbank.fetch_indicators(store, client("worldbank"), raw_dir, market.geography,
                                                     list(market.worldbank_indicators))["indicators"])
    step("footprint", lambda: overpass.fetch_footprint(
        store, client("osm"), raw_dir, market.geography, market.footprint["tags"],
        {brands[c.id]: list(c.osm_brand) for c in market.competitors if c.osm_brand}, market.as_of[:4]))
    aliases = {a: brands[c.id] for c in market.competitors for a in c.aliases}
    step("media", lambda: gdelt.fetch_sample(store, client("gdelt"), raw_dir, pl["gdelt_files"], aliases,
                                             tuple(market.gdelt_fips), max_files=max(len(pl["gdelt_files"]), 1)))

    # ---------------------------------------------------------------- analysis (as of the market date)
    a = analyse(store, market, memory, run_id, exp_id, resolutions)
    retr, signals = a["retriever"], a["signals"]
    accepted, rejected, drafts, lessons, prior_hypotheses = (a["accepted"], a["rejected"], a["drafts"], a["lessons"],
                                                            a["prior_hypotheses"])
    report = {"run_id": run_id, "experiment_id": exp_id, "market": _market_dict(market), "plan": pl, "steps": steps,
              "budget": budget.as_dict(), "resolutions": resolutions, "signals": _strip(signals), "lessons": lessons,
              "hypotheses": accepted, "rejected_hypotheses": rejected, "scenario_drafts": drafts,
              "prior_hypotheses": [{"id": k["knowledge_id"], "status": k["status"], "created_at": k["created_at"],
                                    "statement": k["statement"]} for k in prior_hypotheses],
              "graph": _graph(retr, brands), "sources": _sources(store), "track_record": a["track_record"],
              "prediction_policy": a["prediction_policy"], "audit": a["audit"], "agent_costs": a["agent_costs"],
              "seconds": round(time.monotonic() - t0, 1)}
    from .report import write_report  # noqa: PLC0415
    path = write_report(report, out_dir)
    memory.finish_experiment(exp_id, {"hypotheses": len(accepted), "rejected": len(rejected), "drafts": len(drafts),
                                      "steps": {k: v["status"] for k, v in steps.items()}, "report": str(path)})
    store.conn.execute("UPDATE intel_runs SET finished_at=?, status=?, record_json=? WHERE run_id=?",
                       (utc_now(), "completed", json.dumps({"experiment": exp_id, "report": str(path)}), run_id))
    store.conn.commit()
    report["path"] = path
    return report


def active_prediction_method(memory) -> tuple[str, dict | None]:
    """The prediction method in force: only an APPROVED promotion changes it (default 'level')."""
    prom = memory.active("intel.prediction_policy")
    if not prom:
        return "level", None
    exp = memory.experiment(prom["experiment_id"])
    return (exp.get("lineage") or {}).get("parameters", {}).get("method", "level"), prom


def track_record(store, as_of: str, replay_id: str | None = None) -> dict:
    """Accuracy of previously scored predictions whose outcomes were published by ``as_of``."""
    rows = store.conn.execute("""SELECT method, abs_error, in_interval80, direction_hit, interval_score80, record_json
        FROM prediction_scores WHERE outcome_available_at<=?""", (to_timestamp(as_of),)).fetchall()
    out: dict = {}
    for r in rows:
        rec = json.loads(r["record_json"])
        if replay_id is not None and rec.get("replay_id") != replay_id:
            continue
        key = (f"hypothesis:{rec['hypothesis_mode']}" if rec.get("hypothesis_id") and rec.get("hypothesis_mode")
               else f"{r['method']}/{rec.get('interval_method', 'naive')}/{rec.get('quantity_kind')}")
        e = out.setdefault(key, {"n": 0, "abs": [], "cov": [], "hit": [], "iscore": []})
        e["n"] += 1
        e["abs"].append(r["abs_error"])
        e["cov"].append(r["in_interval80"])
        e["iscore"].append(r["interval_score80"])
        if r["direction_hit"] is not None:
            e["hit"].append(r["direction_hit"])
    return {k: {"n": v["n"], "mae_pp": 100 * sum(v["abs"]) / v["n"], "coverage80": sum(v["cov"]) / v["n"],
                "interval_score80_pp": 100 * sum(v["iscore"]) / v["n"],
                "direction_hit_rate": (sum(v["hit"]) / len(v["hit"])) if v["hit"] else None} for k, v in out.items()}


def analyse(store, market, memory, run_id, exp_id, resolutions, *, agents=None, replay_id=None, preview=True,
            policy_eval=False) -> dict:
    """Analysis stage: as-of evidence -> signals -> lessons -> hypotheses -> critiques -> validation -> predictions
    -> scenario drafts. Network-free; reusable for historical replay (``market.as_of`` is the cutoff)."""
    from .audit import audit_transactions  # noqa: PLC0415
    from .forecast import hypothesis_prediction, policy_predictions  # noqa: PLC0415
    from ..agents.orchestrator import ReferenceAgentSet  # noqa: PLC0415
    agents = agents or ReferenceAgentSet()
    check_action("read_evidence")
    retr = StoreEvidenceRetriever(store, market.as_of)
    brands = {c.id: f"brand:{c.id}" for c in market.competitors}
    tx = sig.transaction_signals(retr, market, brands)
    signals = {"transactions": tx, "products": sig.product_signals(retr, market, [market.focal, market.target]
                                                                   if market.focal else list(brands))
               if tx.get("available") else {},
               "media": sig.media_signals(retr, market, brands), "footprint": sig.footprint_signals(retr, market, brands),
               "macro": sig.macro_signals(retr, market), "groups": sig.group_signals(retr, tx, brands) if tx.get("available") else {}}
    metrics = {m["id"]: m for m in tx.get("metrics", [])}
    audit = audit_transactions(retr, market, tx) if tx.get("available") else {"available": False}
    check_action("read_knowledge")
    tags = ["domain:share_forecasting", "domain:competitive_displacement", f"market:{market.id}"]
    lessons = memory.retrieve(tags=tags, kinds=["lesson", "outcome", "question"], as_of=market.as_of)
    lessons = [k for k in lessons if "outcome:prediction" not in k["scope"].get("tags", [])][:20]
    prior_hypotheses = memory.retrieve(tags=[f"market:{market.id}"], kinds=["hypothesis"], as_of=market.as_of)
    record = track_record(store, market.as_of, replay_id)
    method, promotion = active_prediction_method(memory)
    unresolved = [k for k, v in resolutions.items() if v.get("method") == "unresolved"]
    ctx = {"run_id": run_id, "market": market, "signals": signals, "lessons": lessons, "unresolved": unresolved,
           "metrics": metrics, "audit": audit, "track_record": record, "retriever": retr}
    extra_tags = [f"replay:{replay_id}"] if replay_id else []
    hyps, _ = agents.propose(ctx)
    decomp_numbers = {round(v, 2) for d in audit.get("decomposition", {}).values() for k, v in d.items() if k.endswith("_pp")}
    accepted, rejected, drafts, policy_preds = [], [], [], []
    for h in hyps:
        problems = validate_hypothesis(h, retr, metrics, retr.as_of, extra_numbers=decomp_numbers)
        h["validation"] = {"passed": not problems, "problems": problems}
        (accepted if not problems else rejected).append(h)
    critiques = agents.critique(accepted, ctx)
    for h in accepted:
        h["critiques"] = critiques.get(h["id"], [])
        h["track_record"] = record
        kid = memory.submit("hypothesis", h["claim"], h["produced_by"],
                            {"tags": ["domain:competitive_displacement", f"market:{market.id}", f"mode:{h['mode']}"] + extra_tags},
                            h["evidence"], experiment_id=exp_id, confidence=h["confidence"], record=h, available_at=retr.as_of)
        h["knowledge_id"] = kid
        v2 = hypothesis_prediction(h, retr, market, tx, method, replay_id)
        if v2:
            v2["hypothesis_knowledge_id"] = kid
            h["prediction"]["resolvable_after"] = v2["resolvable_after"]
            h["prediction_v2"] = v2
            pid = memory.submit("prediction", f"{h['id']}: {v2['method']} point {100 * v2['point']:.2f}% "
                                f"(80% {100 * v2['interval80'][0]:.2f}-{100 * v2['interval80'][1]:.2f}) for "
                                f"{', '.join(v2['quantity'].get('periods') or v2['quantity']['a']['periods'])}",
                                h["produced_by"], {"tags": [f"market:{market.id}", "prediction:hypothesis"] + extra_tags,
                                                   "resolvable_after": v2["resolvable_after"]},
                                h["evidence"], experiment_id=exp_id, record={"prediction": v2, "hypothesis": h["id"]},
                                available_at=retr.as_of)
            memory.link(pid, "derived_from", kid, h["produced_by"])
        for les in lessons:
            memory.link(kid, "considered", les["knowledge_id"], h["produced_by"])
        for c in h["critiques"]:
            cid = memory.submit("critique", c["statement"], c.get("produced_by", agents.critic_id),
                                {"tags": [f"market:{market.id}"] + extra_tags}, [kid], experiment_id=exp_id,
                                available_at=retr.as_of)
            memory.link(cid, "critiques", kid, c.get("produced_by", agents.critic_id))
        store.conn.execute("INSERT OR REPLACE INTO hypotheses VALUES (?,?,?,?,?)",
                           (h["id"], run_id, "proposed", h["produced_by"], json.dumps(h, default=str)))
        check_action("propose_scenario")
        raw = agents.draft(h, ctx) if preview else None
        if raw:
            drafts.append(_preview(raw, h))
    if policy_eval and tx.get("available"):
        for p in policy_predictions(store, retr, market, tx, "engine:prediction_policies", replay_id):
            memory.submit("prediction", f"policy {p['method']}/{p['interval_method']} {p['subject']}: point "
                          f"{100 * p['point']:.2f}% for {', '.join(p['quantity']['periods'])}", "engine:prediction_policies",
                          {"tags": [f"market:{market.id}", "prediction:policy", f"method:{p['method']}"] + extra_tags},
                          p["evidence"], experiment_id=exp_id, record={"prediction": p}, available_at=retr.as_of)
            policy_preds.append(p)
    store.conn.commit()
    return {"retriever": retr, "signals": signals, "metrics": metrics, "lessons": lessons,
            "prior_hypotheses": prior_hypotheses, "accepted": accepted, "rejected": rejected, "drafts": drafts,
            "track_record": record, "prediction_policy": {"method": method, "promotion": promotion},
            "audit": audit, "agent_costs": agents.costs(), "agent_set": agents.name, "policy_predictions": policy_preds}


def _preview(raw: dict, h: dict) -> dict:
    """Validate the draft config and run a tiny synthetic preview (clearly labelled)."""
    from ..engine import build_world, run_scenario  # noqa: PLC0415
    cfg = parse_config(raw)
    world = build_world(cfg, cfg.seeds[0])
    prev = {}
    for s in cfg.scenario_names:
        rows = run_scenario(cfg, world, cfg.scenario(s), collect_segments=False).metrics
        prev[s] = {p: round(rows[-1][f"expected_share_{p}"], 4) for p in cfg.product_ids}
    return {"hypothesis": h["id"], "config": raw, "validated": True, "label": "SYNTHETIC DRAFT (uncalibrated)",
            "preview_final_expected_shares": prev}


def _market_dict(m) -> dict:
    return {"id": m.id, "name": m.name, "category": m.category, "geography": m.geography, "as_of": m.as_of,
            "mode": m.mode, "focal": m.focal, "target": m.target, "competitors": [c.id for c in m.competitors]}


def _strip(signals: dict) -> dict:
    s = json.loads(json.dumps(signals, default=str))
    for m in s.get("transactions", {}).get("metrics", []):
        m["inputs_count"] = len(m.pop("inputs"))
    return s


def _graph(retr, brands) -> list[dict]:
    rows = []
    for b in brands.values():
        for r in retr.relationships(subject=b):
            if r["predicate"] == "located_at":
                continue
            rows.append(r)
            if r["predicate"] == "operated_by":
                rows.extend(retr.relationships(subject=r["object"]))
    seen, out = set(), []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            ent = retr.entity(r["object"])
            out.append({**r, "object_name": ent["name"] if ent else r["object"]})
    return out


def _sources(store) -> list[dict]:
    return [dict(r) for r in store.conn.execute("""SELECT DISTINCT so.source_id, so.publisher, so.licence, so.attribution
        FROM snapshots sn JOIN datasets d ON d.dataset_id=sn.dataset_id JOIN sources so ON so.source_id=d.source_id""")]


def investigate_cli(store, args) -> int:
    market = load_market(args.market)
    pl = plan(market)
    t = pl["total"]
    lim = market.limits
    print(f"{market.id}: {len(market.competitors)} competitors, mode={market.mode} "
          f"(focal={market.focal}, target={market.target}), as of {market.as_of}")
    print(f"  plan: ~{t['requests']} requests, ~{t['bytes'] / 1e6:.0f} MB, ~{t['seconds'] / 60:.1f} min "
          f"(limits: {lim['max_requests']} requests, {lim['max_megabytes']} MB, {lim['max_seconds'] / 60:.0f} min)")
    for k, v in pl["steps"].items():
        print(f"    {k:<10} {v['requests']:>3} requests  {v['bytes'] / 1e6:6.1f} MB  ~{v['seconds']:>4}s")
    over = t["requests"] > lim["max_requests"] or t["bytes"] > lim["max_megabytes"] * 1e6 or t["seconds"] > lim["max_seconds"]
    if args.dry_run:
        return 0
    if over and not args.yes and not args.offline:
        print("refused: plan exceeds the market's limits; adjust the spec or pass --yes", file=sys.stderr)
        return 3
    r = investigate(store, args.market, args.out, args.raw, args.offline)
    print(f"\n{len(r['hypotheses'])} validated hypotheses, {len(r['rejected_hypotheses'])} rejected by validation, "
          f"{len(r['scenario_drafts'])} scenario drafts; {r['budget']['requests']} requests, "
          f"{r['budget']['bytes'] / 1e6:.1f} MB, {r['seconds']:.0f}s")
    print(f"report: {r['path']}")
    return 0

