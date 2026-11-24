"""OFFLINE end-to-end reconstruction of the R2 experiments from every saved live reply (no network, no paid calls).

Replies used, all served from the prompt cache on a THROWAWAY copy of the store (the real store is never written):
- r2v-b16fc9281536: the two Hypothesis Generator replies (2025-11-23, 2026-05-23);
- r2r-57fa83dc8f2f: the critic and strategist replies for 2025-11-23 (paid; reused only if byte-identical).
The 2026-05-23 critic and strategist were never called; in the reconstruction those calls are refused (recorded as
call errors), never invented.

Usage: python scripts/r2_offline_reconstruction.py [--db data/halibut.sqlite] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from halibut.agents.llm import load_llm_config
from halibut.agents.routing import load_routing
from halibut.data.store import Store, utc_now
from halibut.investigation import artifact as art
from halibut.investigation.evaluation import evaluate_document
from halibut.investigation.loop import InvestigationInternalError, run_investigation
from halibut.investigation.revalidation import (RevalidationRouter, critic_detection, load_original, reusable_calls,
                                                strategist_assessment)

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "configs/intel/markets/uk_cars_bev_v1.toml"
ORIGINAL = ROOT / "results/r2_validation/run-20261124T021311Z"
ORIGINS, EVAL, REUSE = ["2025-11-23", "2026-05-23"], "2026-11-23", ["r2r-57fa83dc8f2f"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/halibut.sqlite"))
    ap.add_argument("--out", default=str(ROOT / "results/r2_validation/step1-20261124T025850Z/offline_reconstruction"))
    a = ap.parse_args()
    out = Path(a.out)
    (out / "investigations").mkdir(parents=True, exist_ok=True)
    rt = load_routing()
    cfg = load_llm_config(ROOT / rt["provider_config"], model=rt["default_model"])
    original = load_original(ORIGINAL)
    tmp = Path(tempfile.mkdtemp(prefix="halibut_recon_")) / "copy.sqlite"
    src, dst = sqlite3.connect(a.db), sqlite3.connect(tmp)
    src.backup(dst)
    src.close(), dst.close()
    st = Store(tmp)
    try:
        reuse = reusable_calls(st, REUSE)
        docs, calls, internal = {}, {}, {}
        for o in ORIGINS:
            r = RevalidationRouter(st, "offline-reconstruction", cfg, mode="preflight", reuse=reuse)
            try:
                docs[o] = run_investigation(st, MARKET, as_of=o, evaluate_as_of=EVAL, agents="mock", router=r,
                                            log=lambda *_: None)
            except InvestigationInternalError as exc:
                docs[o], internal[o] = exc.doc, exc.doc["stop_reason"]
            calls[o] = {"generator_cache_hit": bool(getattr(r._gen, "last", {}).get("cache_hit")), "reuse_log": r.reuse_log}
            (out / "investigations" / f"offline_reconstruction_{o}.json").write_text(
                json.dumps(docs[o], indent=1, sort_keys=True, default=str) + "\n")
        summary = {
            "label": "OFFLINE RECONSTRUCTION from saved replies (no network, no paid calls, throwaway store copy)",
            "created_at": utc_now(), "replies_used": {"generator": "r2v-b16fc9281536 (both dates)",
                                                      "critic_strategist": "r2r-57fa83dc8f2f (2025-11-23 only)"},
            "internal_errors": internal, "calls": calls,
            "per_origin": {o: {"status": d["status"], "stop_reason": d["stop_reason"],
                               "completion": d["failures"]["completion"], "validates": art.validate(d) == [],
                               "counts": {k: len(v) for k, v in d["stages"].items()},
                               "failure_classes": d["failures"]["counts"], "evaluation": evaluate_document(d),
                               "options": [{"ref": x["data"]["ref"], "title": x["label"],
                                            "intended_effect": x["data"]["intended_effect"],
                                            "simulation": x["data"]["simulation"]}
                                           for x in d["stages"]["strategic_options"]],
                               "protocol_option": (d["experiment_protocol"] or {}).get("data", {}).get("option_ref")}
                           for o, d in docs.items()},
            "critic_detection": critic_detection(docs),
            "strategist_assessment": strategist_assessment(docs, original["docs"]["reference"])}
        (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n")
        for o, v in summary["per_origin"].items():
            print(o, v["status"], v["completion"]["state"], "validates" if v["validates"] else "INVALID",
                  {k: n for k, n in v["counts"].items() if n})
        print("internal errors:", internal or "none")
        print("written:", out)
        return 1 if internal else 0
    finally:
        st.close()
        shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
