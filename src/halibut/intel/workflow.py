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
    check_action("read_evidence")
    retr = StoreEvidenceRetriever(store, market.as_of)
    tx = sig.transaction_signals(retr, market, brands)
    signals = {"transactions": tx, "products": sig.product_signals(retr, market, [market.focal, market.target]
                                                                   if market.focal else list(brands))
               if tx.get("available") else {},
               "media": sig.media_signals(retr, market, brands), "footprint": sig.footprint_signals(retr, market, brands),
               "macro": sig.macro_signals(retr, market), "groups": sig.group_signals(retr, tx, brands) if tx.get("available") else {}}
    metrics = {m["id"]: m for m in tx.get("metrics", [])}
    check_action("read_knowledge")
    lessons = memory.retrieve(tags=["domain:share_forecasting", "domain:competitive_displacement", f"market:{market.id}"])
    unresolved = [k for k, v in resolutions.items() if v.get("method") == "unresolved"]
    ctx = {"run_id": run_id, "market": market, "signals": signals, "lessons": lessons, "unresolved": unresolved}
    check_action("propose_hypothesis")
    hyps = ReferenceHypothesisAgent().propose(ctx)
    critic, drafter = ReferenceCritic(), ReferenceScenarioDrafter()
    accepted, rejected, drafts = [], [], []
    for h in hyps:
        problems = validate_hypothesis(h, retr, metrics, retr.as_of)
        h["validation"] = {"passed": not problems, "problems": problems}
        (accepted if not problems else rejected).append(h)
    for h in accepted:
        h["critiques"] = critic.critique(h, ctx)
        kid = memory.submit("hypothesis", h["claim"], h["produced_by"],
                            {"tags": ["domain:competitive_displacement", f"market:{market.id}", f"mode:{h['mode']}"]},
                            h["evidence"], experiment_id=exp_id, confidence=h["confidence"], record=h)
        pid = memory.submit("prediction", json.dumps(h["prediction"], sort_keys=True), h["produced_by"],
                            {"tags": [f"market:{market.id}"], "resolvable_after": h["prediction"]["resolvable_after"]},
                            h["evidence"], experiment_id=exp_id, record={"hypothesis": h["id"]})
        memory.link(pid, "derived_from", kid, h["produced_by"])
        for les in lessons:
            memory.link(kid, "considered", les["knowledge_id"], h["produced_by"])
        for c in h["critiques"]:
            cid = memory.submit("critique", c["statement"], critic.id, {"tags": [f"market:{market.id}"]}, [kid], experiment_id=exp_id)
            memory.link(cid, "critiques", kid, critic.id)
        store.conn.execute("INSERT OR REPLACE INTO hypotheses VALUES (?,?,?,?,?)",
                           (h["id"], run_id, "proposed", h["produced_by"], json.dumps(h, default=str)))
        h["knowledge_id"] = kid
        check_action("propose_scenario")
        raw = drafter.draft(h, ctx)
        if raw:
            drafts.append(_preview(raw, h))
    store.conn.commit()
    report = {"run_id": run_id, "experiment_id": exp_id, "market": _market_dict(market), "plan": pl, "steps": steps,
              "budget": budget.as_dict(), "resolutions": resolutions, "signals": _strip(signals), "lessons": lessons,
              "hypotheses": accepted, "rejected_hypotheses": rejected, "scenario_drafts": drafts,
              "graph": _graph(retr, brands), "sources": _sources(store),
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

