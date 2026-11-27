"""Unified prediction ledger and automatic outcome resolution (Architecture V2, milestone 1).

This module extends the existing machinery rather than duplicating it. Predictions are registered FROM the stored
investigation documents:
- hypothesis tests (``hypothesis_tests``) become BINARY questions;
- official forecasts and model estimates (claims) become NUMERIC questions.

Resolution reuses the bitemporal store (the as-of retriever), ``build_panel``, the compiler's comparators and the
registry's baselines. Execution is logged in the hash-chained event log.

Guarantees:
- **Stable identities.** ``prediction_id`` = hash of kind, source and source id and the question's sha256.
  ``sync`` is idempotent.
- **Immutability.** ``predictions`` rows are immutable (SQL triggers). The original question, registration time,
  evidence time, baselines and provenance never change.
- **Real registration time.** It is the time the test or claim was registered. **Temporal class** is derived from the
  bitemporal store: a prediction whose target periods were first published before registration is RETROSPECTIVE
  (leakage-prone); otherwise it is PROSPECTIVE.
- **First-publication rule** (``first_publication.v1``). A question resolves once, on the vintage at the moment its
  LAST target period was first published. Later data revisions are recorded as separate ``revision`` resolutions and
  never replace the first.
- **No unpublished evidence.** Only observations with ``available_at <= now`` are used, and ``now`` can never exceed
  the real clock from the CLI. A prospective question whose evidence predates its registration is INVALID.
- **Typed outcomes:**
  - ``pending``: a target period not yet published;
  - ``resolved``;
  - ``invalid``: an integrity failure (for example the market definition changed);
  - ``ambiguous``: an exact tie with the reference;
  - ``unavailable``: published, but the quantity is not computable.
- **Idempotent and auditable.** Each resolution id is the hash of the prediction, the vintage, the evidence time and
  the outcome; reruns without new evidence add nothing. Every run is an event.
- **Scores:**
  - binary categorical claims (no stated probability): hit, and the Brier score at p = 1;
  - binary claims with a probability: Brier and log score;
  - numeric: absolute and squared error, 80% interval hit, and the Gaussian CRPS and log score when an SD exists.

  Explicit persistence and momentum baselines are computed from the origin evidence only.
- **Nothing is promoted.** Resolutions never create lessons, validated mechanisms or shared knowledge.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import tomllib
from pathlib import Path

from ..data.store import utc_now

RULE = "first_publication.v1"
RESOLVER_VERSION = "prediction_resolver.v3"  # v3: inferred (not "verified") first capture; v2 rows in the store used only estimated_lag
SCRIPTED = ("mock", "reference")
TIE = 1e-12


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------- markets
def market_paths(markets_dir) -> dict:
    """market id -> (path, sha256 of the spec file), for every market spec in the directory."""
    out = {}
    for p in sorted(Path(markets_dir).glob("*.toml")):
        try:
            mid = tomllib.loads(p.read_text())["market"]["id"]
        except Exception:  # noqa: BLE001  (not a market spec)
            continue
        out.setdefault(mid, []).append((p, hashlib.sha256(p.read_bytes()).hexdigest()))
    return out


def _market(markets, market_id, sha):
    """The market spec with the SAME sha256 as at registration (a changed definition is never used to resolve)."""
    for p, s in markets.get(market_id, []):
        if s == sha:
            return p
    return None


class _Panels:
    def __init__(self, store):
        self.store, self.cache = store, {}

    def get(self, market_path, as_of):
        import dataclasses  # noqa: PLC0415
        from ..intel.market import load_market  # noqa: PLC0415
        from ..world.panel import build_panel  # noqa: PLC0415
        k = (str(market_path), as_of)
        if k not in self.cache:
            m = dataclasses.replace(load_market(market_path), as_of=as_of[:10])
            self.cache[k] = build_panel(self.store, m, as_of)
        return self.cache[k]


MAX_RELEASE_GAP_DAYS = 120  # one quarterly cycle plus slippage: a longer gap may hide an unseen intermediate release


def first_publication(store, panel_now, periods) -> dict:
    """{period: (earliest available_at, basis)} for the periods the panel (built at 'now') contains.

    Provenance basis (never upgraded silently; nothing here is VERIFIED publication, which would need the publisher's
    own release archive or calendar):
      estimated_lag             history backfilled at our FIRST retrieval: the original publication was not observed;
                                the value is the earliest CAPTURED vintage and its date an estimate
      first_capture_inferred    INFERRED (heuristic): the period was absent in our previous snapshot, and the release that
                                added it follows that snapshot's release within MAX_RELEASE_GAP_DAYS, so no unseen
                                intermediate release is likely. Useful, not definitive
      first_capture_unverified  first captured in a later snapshot; an unseen intermediate release (and so an unseen
                                original value) cannot be excluded
      published / retrieval     non-DfT panels: the availability carried by the rows"""
    from datetime import datetime  # noqa: PLC0415
    out = {}
    for p in periods:
        if p not in panel_now.periods:
            continue
        if panel_now.source != "dft":
            out[p] = (panel_now.cells[(panel_now.MARKET, "all")][p][2], "published")
            continue
        row = store.conn.execute("""SELECT o.available_at, o.availability_basis, o.snapshot_id FROM observations o
            JOIN series s USING(series_id) WHERE s.universe_key=? AND o.period=? ORDER BY o.available_at, o.snapshot_id
            LIMIT 1""", (panel_now.universe, p)).fetchone()
        t, basis, sid = row
        if basis == "estimated_lag":
            out[p] = (t, "estimated_lag")
            continue
        snap = store.conn.execute("SELECT dataset_id, publisher_updated, retrieved_at FROM snapshots WHERE snapshot_id=?",
                                  (sid,)).fetchone()
        prev = store.conn.execute("""SELECT snapshot_id, publisher_updated, retrieved_at FROM snapshots WHERE dataset_id=?
            AND snapshot_id<? ORDER BY snapshot_id DESC LIMIT 1""", (snap[0], sid)).fetchone()
        verified = False
        if prev is not None and prev[1] and snap[1]:
            absent = store.conn.execute("""SELECT COUNT(*) FROM observations o JOIN series s USING(series_id)
                WHERE s.universe_key=? AND o.period=? AND o.snapshot_id<=?""", (panel_now.universe, p, prev[0])).fetchone()[0] == 0
            gap = (datetime.fromisoformat(snap[1].replace("Z", "+00:00"))
                   - datetime.fromisoformat(prev[1].replace("Z", "+00:00"))).days
            verified = absent and 0 < gap <= MAX_RELEASE_GAP_DAYS
        out[p] = (t, "first_capture_inferred" if verified else "first_capture_unverified")
    return out


def combined_basis(fp: dict, periods) -> str:
    """The weakest basis over the target periods (a question is only as verified as its least verified period)."""
    order = ["estimated_lag", "first_capture_unverified", "retrieval", "published", "first_capture_inferred"]
    return min((fp[p][1] for p in periods), key=lambda b: order.index(b) if b in order else 0)


# ------------------------------------------------------------------------------------------------------- baselines
def _baselines(panel, q: dict) -> dict:
    """Persistence (baseline.level) and momentum (baseline.momentum) from the ORIGIN evidence only."""
    from ..investigation.compiler import holds  # noqa: PLC0415
    from ..models import registry as reg  # noqa: PLC0415
    out = {}
    tgt = {"template": q["template"], "entity": q["entity"], "entity_b": q.get("entity_b")}
    for name, fam in (("persistence", "baseline.level"), ("momentum", "baseline.momentum")):
        try:
            pt = reg.execute({"family": fam, "target": tgt}, panel)["point"]
        except Exception as exc:  # noqa: BLE001  (typed unavailability; never an invented value)
            out[name] = {"point": None, "note": str(exc)[:160]}
            continue
        b = {"point": pt}
        if q.get("comparator"):
            ref = q["reference_value"]
            b["predicts"] = None if abs(pt - ref) <= TIE and q["comparator"] not in ("above_zero", "below_zero") else \
                bool(holds(pt, q["comparator"], ref))
        out[name] = b
    return out


# ------------------------------------------------------------------------------------------------------- sync
def _docs(store, include_scripted: bool):
    for (art,) in store.conn.execute("SELECT artifact_json FROM investigations ORDER BY created_at, investigation_id"):
        d = json.loads(art)
        if d["provenance"].get("agents") in SCRIPTED and not include_scripted:
            continue
        yield d


def sync(store, markets_dir="configs/intel/markets", include_scripted: bool = False) -> dict:
    """Register every prediction of every stored investigation (idempotent). Scripted mock/reference workforces are
    skipped unless asked (their output is not intelligence)."""
    markets, panels = market_paths(markets_dir), _Panels(store)
    added = skipped = 0
    for d in _docs(store, include_scripted):
        prov = d["provenance"]
        mpath = _market(markets, d["objective"]["market_id"], prov["market_spec_sha256"])
        origin = d["objective"]["as_of"]
        panel = panels.get(mpath, origin) if mpath else None
        avail = None
        if panel is not None:
            avail = max((c[2] or "" for cell in panel.cells.values() for c in cell.values()), default=None) or None
        base_prov = {"investigation_id": d["investigation_id"], "input_id": d["input_id"],
                     "prompt_version": prov.get("prompt_version"), "schemas": prov.get("schemas"), "agents": prov.get("agents"),
                     "models": {r: v.get("model") for r, v in (d["agents"]["routing"].get("roles") or {}).items()},
                     "panel_fingerprint": d["evidence"]["panel"].get("fingerprint"), "market_spec_sha256": prov["market_spec_sha256"],
                     "registry_rule": prov.get("registry_rule"), "test_spec_version": prov.get("test_spec_version"),
                     "market_spec_found": mpath is not None}
        items = []
        for t in d["stages"]["tests"]:
            if t["status"] != "registered":
                continue
            spec, imp = t["data"]["spec"], t["data"]["implication"]
            hyp = next((ln["to"] for ln in t["links"] if ln["relation"] == "tests"), None)
            href = next((h["data"].get("ref") for h in d["stages"]["hypotheses"] if h["id"] == hyp), None)
            row = store.conn.execute("SELECT registered_at FROM hypothesis_tests WHERE test_id=?", (t["id"],)).fetchone()
            q = {k: spec.get(k) for k in ("template", "entity", "entity_b", "comparator", "reference_value", "reference_source",
                                           "target_periods", "would_refute_if")}
            q["probability"] = None  # R2 claims are categorical (no stated probability)
            items.append(("binary", "hypothesis_test", t["id"], q, t["data"].get("horizon") or "standard",
                          t["data"].get("registered_at") or (row["registered_at"] if row else d["created_at"]),
                          {**base_prov, "hypothesis_claim_id": hyp, "hypothesis_ref": href, "implication_id": imp.get("id"),
                           "statement": t["label"][:300]},
                          _h([hyp, q["template"], q["entity"], q.get("entity_b") or "", q["comparator"]])))
        for stage in ("forecasts", "estimates"):
            for f in d["stages"][stage]:
                x = f["data"]
                qq = (x.get("resolution") or {}).get("quantity") or x.get("target") or {}
                if not qq.get("periods"):
                    continue
                q = {"template": qq["template"], "entity": qq["entity"], "entity_b": qq.get("entity_b"),
                     "target_periods": qq["periods"], "point": x.get("point", x.get("value")),
                     "interval80": x.get("interval80", x.get("interval")), "sd": x.get("sd"),
                     "model_version": (x.get("model") or {}).get("version") or x.get("model_version")}
                items.append(("numeric", stage[:-1], f["id"], q, "standard", d["created_at"],
                              {**base_prov, "official": stage == "forecasts", "statement": f["label"][:300]},
                              _h([q["model_version"], q["template"], q["entity"], q.get("entity_b") or ""])))
        n = max(1, len(items))
        cost = float(d["agents"]["costs"].get("actual_cost_usd") or 0)
        for kind, source, sid, q, horizon, reg_at, pv, group in items:
            qsha = _h(q)
            pid = "pq:" + _h([kind, source, sid, qsha])[:20]
            fp = first_publication(store, panels.get(mpath, utc_now()), q["target_periods"]) if mpath else {}
            retro = any(v[0] and v[0] <= reg_at for v in fp.values())
            base = _baselines(panel, q) if panel is not None else {"note": "market spec not found: no baselines"}
            cur = store.conn.execute("INSERT OR IGNORE INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                     (pid, kind, source, sid, d["investigation_id"], d["objective"]["market_id"],
                                      json.dumps(q, sort_keys=True), qsha, RULE, horizon,
                                      "retrospective" if retro else "prospective", "cg:" + group[:16], reg_at, origin, avail,
                                      json.dumps(base, sort_keys=True, default=float), json.dumps(pv, sort_keys=True),
                                      json.dumps({"investigation_cost_usd": cost, "predictions_in_investigation": n,
                                                  "share_usd": cost / n}), utc_now()))
            added += cur.rowcount
            skipped += 1 - cur.rowcount
    store.conn.commit()
    return {"added": added, "already_registered": skipped}


# ------------------------------------------------------------------------------------------------------- scoring
def _phi(z):
    return math.exp(-z * z / 2) / math.sqrt(2 * math.pi)


def _Phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


Z80 = 1.2815515655446004


def gaussian_valid(q: dict) -> tuple[bool, str]:
    """A Gaussian predictive distribution is scored only if it is the distribution actually stated: SD > 0 and the 80%
    interval equals point +/- z80 x SD (an interval clipped to [0, 1] is no longer that distribution)."""
    sd, iv = q.get("sd"), q.get("interval80")
    if not sd or sd <= 0:
        return False, "no predictive SD"
    if not iv:
        return False, "no interval to check the stated distribution"
    lo, hi = iv
    if abs(lo - (q["point"] - Z80 * sd)) > 1e-9 or abs(hi - (q["point"] + Z80 * sd)) > 1e-9:
        return False, "interval is not point +/- z80 x SD (clipped or non-Gaussian): no Gaussian proper score"
    return True, ""


def score_numeric(q: dict, y: float, baselines: dict) -> dict:
    pt = q["point"]
    s = {"abs_error": abs(pt - y), "sq_error": (pt - y) ** 2}
    if q.get("interval80"):
        lo, hi = q["interval80"]
        s["in_interval80"] = lo <= y <= hi
    ok, why = gaussian_valid(q)
    if ok:
        sd = q["sd"]
        z = (y - pt) / sd
        s["crps_gaussian"] = sd * (z * (2 * _Phi(z) - 1) + 2 * _phi(z) - 1 / math.sqrt(math.pi))
        s["log_score_gaussian"] = math.log(_phi(z) / sd)
    else:
        s["probabilistic_scores"] = f"not applied: {why}"
    for name, b in baselines.items():
        if isinstance(b, dict) and b.get("point") is not None:
            e = abs(b["point"] - y)
            s[f"{name}_abs_error"] = e
            s[f"skill_vs_{name}"] = (1 - s["abs_error"] / e) if e > 0 else None
    return s


def score_binary(q: dict, hit: bool, baselines: dict) -> dict:
    """Proper probabilistic scores (Brier, log) ONLY for a stated probability; a categorical claim gets its hit only."""
    o = 1.0 if hit else 0.0
    p = q.get("probability")
    s = {"hit": hit}
    if p is None:
        s["probabilistic_scores"] = "not applied: categorical claim (no stated probability)"
    else:
        s["brier"] = (p - o) ** 2
        s["log_score"] = math.log(max(p if hit else 1 - p, 1e-12))
    for name, b in baselines.items():
        if isinstance(b, dict) and "predicts" in b:
            s[f"{name}_predicts"] = b["predicts"]
            s[f"{name}_hit"] = None if b["predicts"] is None else (b["predicts"] == hit)
    s["informative_vs_baselines"] = all(isinstance(b, dict) and b.get("predicts") is not True
                                        for b in baselines.values() if isinstance(b, dict) and "predicts" in b)
    return s


def outcome_group(market_id: str, q: dict) -> str:
    """Predictions resolved by the SAME realised value (same market, quantity and target periods) share an outcome
    group. Counting groups removes exact duplicates of one outcome; it does NOT make outcomes statistically independent
    (quantities in the same market and quarters share demand, supply and reporting shocks)."""
    return "og:" + _h([market_id, q["template"], q["entity"], q.get("entity_b") or "", sorted(q["target_periods"])])[:16]


# ------------------------------------------------------------------------------------------------------- resolve
def _insert(store, pid, status, vintage, as_of, basis, realized, outcome, scores, detail, now):
    rid = "pr:" + _h([pid, status, vintage, as_of, realized, outcome, detail])[:20]
    cur = store.conn.execute("INSERT OR IGNORE INTO prediction_resolutions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (rid, pid, status, vintage, as_of, basis, realized, outcome,
                              json.dumps(scores, sort_keys=True, default=float), detail, RESOLVER_VERSION, now))
    return cur.rowcount


def _value(panel, q):
    return panel.value(q["template"], q["entity"], q["target_periods"], q.get("entity_b"))[0]


def _resolve_one(store, r, markets, panels, now) -> list[tuple]:
    """Rows to append for one prediction: (status, vintage, evidence_as_of, basis, realized, outcome, scores, detail)."""
    from ..investigation.compiler import holds  # noqa: PLC0415
    q, prov, base = json.loads(r["question_json"]), json.loads(r["provenance_json"]), json.loads(r["baselines_json"])
    first = store.conn.execute("""SELECT * FROM prediction_resolutions WHERE prediction_id=? AND vintage='first_publication'
        ORDER BY resolved_at, resolution_id LIMIT 1""", (r["prediction_id"],)).fetchone()
    mpath = _market(markets, r["market_id"], prov["market_spec_sha256"])
    if mpath is None:
        return [("invalid", "none", None, None, None, None, {},
                 "market definition changed or not found: never resolved against a different definition")]
    pnow = panels.get(mpath, now)
    fp = first_publication(store, pnow, q["target_periods"])
    missing = [p for p in q["target_periods"] if p not in fp or (fp[p][0] or "") > now]
    if missing:
        return [("pending", "none", None, None, None, None, {}, f"not yet published: {missing}")]
    t_first = max(fp[p][0] for p in q["target_periods"])
    basis = combined_basis(fp, q["target_periods"])
    if r["temporal_class"] == "prospective" and t_first <= r["registered_at"]:
        return [("invalid", "first_publication", t_first, basis, None, None, {},
                 "prospective question whose evidence predates its registration")]

    def judge(y):
        if y is None:
            return "unavailable", None, {}, "target periods published but the quantity is not computable"
        if r["kind"] == "numeric":
            return "resolved", None, score_numeric(q, y, base), "first publication"
        zero = q["comparator"] in ("above_zero", "below_zero")
        if (abs(y) if zero else abs(y - q["reference_value"])) <= TIE:
            return "ambiguous", None, {}, "exact tie with the reference"
        hit = bool(holds(y, q["comparator"], q["reference_value"]))
        return "resolved", "passed" if hit else "failed", score_binary(q, hit, base), "first publication"
    rows = []
    if first is None:  # the ONE first-publication resolution: the vintage when the last target period was first published
        y1 = _value(panels.get(mpath, t_first), q)
        st, out, sc, det = judge(y1)
        rows.append((st, "first_publication", t_first, basis, y1, out, sc, det))
        first_y, first_status = y1, st
    else:
        first_y, first_status = first["realized"], first["status"]
    if first_status in ("resolved", "ambiguous") and first_y is not None:  # later revisions: recorded, never replacing
        y_now = _value(pnow, q)
        if y_now is not None and abs(y_now - first_y) > TIE:
            latest = store.conn.execute(
                "SELECT MAX(o.available_at) FROM observations o JOIN series s USING(series_id) WHERE s.universe_key=? "
                f"AND o.period IN ({','.join('?' * len(q['target_periods']))}) AND o.available_at<=?",
                (pnow.universe, *q["target_periods"], now)).fetchone()[0] if pnow.source == "dft" else now
            st, out, sc, _ = judge(y_now)
            rows.append((st, "revision", latest, "published", y_now, out, sc,
                         "data revision after first publication (the first-publication resolution stands)"))
    return rows


def resolve(store, markets_dir="configs/intel/markets", now: str | None = None, dry_run: bool = False) -> dict:
    """Resolve every prediction that can be resolved at ``now`` (default: the real clock), idempotently."""
    t0, now = time.perf_counter(), now or utc_now()
    markets, panels = market_paths(markets_dir), _Panels(store)
    counts, new = {}, 0
    for r in store.conn.execute("SELECT * FROM predictions ORDER BY registered_at, prediction_id").fetchall():
        rows = _resolve_one(store, r, markets, panels, now)
        state = rows[0][0] if rows else store.conn.execute(
            "SELECT status FROM prediction_resolutions WHERE prediction_id=? AND vintage='first_publication' LIMIT 1",
            (r["prediction_id"],)).fetchone()[0]
        counts[state] = counts.get(state, 0) + 1
        if not dry_run:
            for row in rows:
                new += _insert(store, r["prediction_id"], *row, now)
    if not dry_run:
        from .events import EventLog  # noqa: PLC0415
        EventLog(store).append("predictions.resolution_run", "L6", {"now": now, "counts": counts, "new_records": new,
                                                                    "seconds": round(time.perf_counter() - t0, 3),
                                                                    "api_cost_usd": 0.0, "resolver": RESOLVER_VERSION},
                               provenance={"rule": RULE})
        store.conn.commit()
    return {"now": now, "status_counts": counts, "new_records": new, "dry_run": dry_run,
            "seconds": round(time.perf_counter() - t0, 3), "api_cost_usd": 0.0}


# ------------------------------------------------------------------------------------------------------- reporting
def current(store) -> list[dict]:
    """Each prediction with its current state (first-publication resolution, else the latest attempt) and revisions."""
    out = []
    for r in store.conn.execute("SELECT * FROM predictions ORDER BY registered_at, prediction_id").fetchall():
        res = store.conn.execute("SELECT * FROM prediction_resolutions WHERE prediction_id=? ORDER BY resolved_at, resolution_id",
                                 (r["prediction_id"],)).fetchall()
        first = next((x for x in res if x["vintage"] == "first_publication"), None)
        latest = res[-1] if res else None
        state = first or latest
        out.append({**{k: r[k] for k in ("prediction_id", "kind", "source", "source_id", "investigation_id", "market_id",
                                          "horizon", "temporal_class", "correlation_group", "registered_at", "evidence_as_of")},
                    "question": json.loads(r["question_json"]), "provenance": json.loads(r["provenance_json"]),
                    "status": state["status"] if state else "unattempted",
                    "outcome": state["outcome"] if state else None, "realized": state["realized"] if state else None,
                    "scores": json.loads(state["scores_json"]) if state else {},
                    "availability_basis": state["availability_basis"] if state else None,
                    "outcome_group": outcome_group(r["market_id"], json.loads(r["question_json"])),
                    "revisions": sum(1 for x in res if x["vintage"] == "revision"), "history": len(res)})
    return out


def summary(store) -> dict:
    rows = current(store)
    by = {}
    for x in rows:
        k = (x["provenance"].get("prompt_version"), x["temporal_class"], x["kind"])
        g = by.setdefault(k, {"n": 0, "status": {}, "groups": set(), "ogroups": set(), "basis": {}, "hits": 0,
                              "resolved_binary": 0, "baseline_hits": {}, "abs_error": [], "cost_usd": 0.0})
        g["n"] += 1
        g["status"][x["status"]] = g["status"].get(x["status"], 0) + 1
        if x["status"] == "resolved":
            g["groups"].add(x["correlation_group"])
            g["ogroups"].add(x["outcome_group"])
            g["basis"][x["availability_basis"]] = g["basis"].get(x["availability_basis"], 0) + 1
            if x["kind"] == "binary":
                g["resolved_binary"] += 1
                g["hits"] += bool(x["scores"].get("hit"))
                for b in ("persistence", "momentum"):
                    v = x["scores"].get(f"{b}_hit")
                    if v is not None:
                        g["baseline_hits"].setdefault(b, [0, 0])
                        g["baseline_hits"][b][0] += v
                        g["baseline_hits"][b][1] += 1
            else:
                g["abs_error"].append(x["scores"]["abs_error"])
    out = []
    for (pv, tc, kind), g in sorted(by.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        out.append({"contract": pv, "temporal_class": tc, "kind": kind, "predictions": g["n"], "status": g["status"],
                    "resolved_claim_groups": len(g["groups"]),
                    "unique_realised_outcomes": len(g["ogroups"]),
                    "resolution_basis": g["basis"],
                    "hit_rate": (g["hits"] / g["resolved_binary"]) if g["resolved_binary"] else None,
                    "baseline_hit_rates": {b: (v[0] / v[1]) for b, v in g["baseline_hits"].items() if v[1]},
                    "mean_abs_error": (sum(g["abs_error"]) / len(g["abs_error"])) if g["abs_error"] else None})
    return {"resolver": RESOLVER_VERSION, "rule": RULE, "groups": out,
            "note": "retrospective = target periods published before registration (leakage-prone); predictive support "
                    "is not causal validation; unique_realised_outcomes removes duplicates of one realised value but "
                    "outcomes in the same market and quarters remain correlated (not independent observations)"}


def export(store, out_dir="registry/predictions") -> dict:
    """Write the whole ledger (every prediction and its full resolution history) as deterministic JSON for git, so
    registrations and resolutions survive the loss of the local store. No credentials; no bulky evidence (see registry/evidence)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    preds = [dict(r) for r in store.conn.execute("SELECT * FROM predictions ORDER BY prediction_id")]
    hist = [dict(r) for r in store.conn.execute("SELECT * FROM prediction_resolutions ORDER BY prediction_id, resolved_at, resolution_id")]
    for r in preds:
        for k in ("question_json", "baselines_json", "provenance_json", "cost_json"):
            r[k] = json.loads(r[k])
        r.pop("synced_at")
    for r in hist:
        r["scores_json"] = json.loads(r["scores_json"])
    body = {"rule": RULE, "resolver": RESOLVER_VERSION, "predictions": preds, "resolutions": hist}
    body["ledger_sha256"] = _h(body)
    (out / "ledger.json").write_text(json.dumps(body, indent=1, sort_keys=True, default=float) + "\n")
    return {"file": str(out / "ledger.json"), "predictions": len(preds), "resolutions": len(hist),
            "ledger_sha256": body["ledger_sha256"]}
