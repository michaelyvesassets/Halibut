"""R2 Step 1: MIXED CACHED/LIVE ENGINEERING REVALIDATION of experiment r2v-b16fc9281536.

This is NOT a fresh, independent intelligence experiment. The Hypothesis Generator is NEVER called: its two replies are
the ones recorded in the original run, served from the prompt cache. Only the smoke test and the Critic and Strategist
calls (2 each) are live. Purpose: show the fixed pipeline (ADR-020 and the postmortem fixes) completes end to end on
identical intelligence and identical evidence, and assess the critic and strategist.

Guarantees:
- identity preflight (throwaway copy of the store, no network): for each date the rebuilt generator request must hit
  the cache with the original reply, and the prompt fingerprint, model, prompt version, schema size, prompt size and
  evidence fingerprint must equal the original run's. Any mismatch refuses BEFORE the smoke test;
- in the live run the generator role is cache-only: a miss raises instead of calling the provider, so no generator
  call can be paid for;
- single-use approval bound to this run, cumulative cap, execution records (as in the original experiment);
- KNOWN_ISSUES (from the postmortem) are used only to evaluate critiques afterwards; they are never sent to any agent.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import shutil
import sqlite3
import tempfile
from pathlib import Path

from ..agents.llm import CachedProvider
from ..agents.routing import Router, load_routing
from ..control import approvals
from ..data.store import Store, utc_now
from ..epistemic.events import EventLog
from ..intel.market import load_market
from . import artifact as art
from .evaluation import evaluate_document
from .live_validation import CRITERIA, CRITERIA_HASH, THRESHOLDS, _report, _smoke, execution_records, markdown, preflight
from .loop import InvestigationInternalError, run_investigation

VERSION = "r2_engineering_revalidation.v1"
LABEL = ("MIXED CACHED/LIVE ENGINEERING REVALIDATION: generator replies are the RECORDED replies of r2v-b16fc9281536 "
         "(cache); critic and strategist replies already paid for are REUSED when the request is byte-identical; only the "
         "smoke test and the remaining critic and strategist calls are live. Recorded replies are genuine model output, "
         "not scripted mocks. Not a fresh independent intelligence experiment and not an independent validation of "
         "intelligence.")
LIVE_ROLES = ("loop_critic", "strategist")
KNOWN_ISSUES = [  # post-hoc evaluation only (postmortem of r2v-b16fc9281536); NEVER sent to any agent
    {"id": "tesla_non_bev_contradiction", "origin": "2025-11-23", "ref": "H4", "kind": "contradicted_assumption",
     "summary": "claims Tesla's total share is held up by non-BEV volume; Tesla's segment mix is 100% BEV",
     "keywords": ["100%", "all-electric", "all electric", "only bev", "only sells", "pure bev", "bev-only", "no non-bev",
                  "segment mix", "segment_mix", "entirely", "exclusively", "only battery"]},
    {"id": "direction_wording_2026_E2", "origin": "2026-05-23", "ref": "E2", "kind": "direction_error",
     "summary": "explanation says total share 'fell from 0.44% to 2.47%'", "applicable": False,
     "reason": "the explanations array was rejected (5 > 4); rejected explanations are never shown to the critic (ADR-020)"},
    {"id": "petrol_unsupported", "origin": "2026-05-23", "ref": "H1", "kind": "unsupported_detail",
     "summary": "'hybrid and petrol range expansion': the evidence does not split non-BEV fuels",
     "keywords": ["petrol", "fuel split", "fuel type", "powertrain", "not broken out", "does not distinguish", "phev",
                  "plug-in", "hybrid"]},
    {"id": "recent_launches_unsupported", "origin": "2026-05-23", "ref": "H3", "kind": "unsupported_detail",
     "summary": "'volume brands with recent BEV launches': launches are not in the evidence",
     "keywords": ["launch", "not in the evidence", "no evidence", "not observed", "unobserved", "not evidenced"]},
    {"id": "ford_product_led_outside_evidence", "origin": "2025-11-23", "ref": "H2", "kind": "unsupported_detail",
     "summary": "'product-led step-up': model-level data are not in the evidence",
     "keywords": ["model-level", "model level", "not in the evidence", "no evidence", "unobserved", "product data"]},
    {"id": "vw_mg_gap_misencoded", "origin": "2025-11-23", "ref": "H3", "kind": "mis_encoded_comparison",
     "summary": "a VW-MG gap compared with MG's share level",
     "keywords": ["gap", "reference", "level", "mismatch", "apples", "different quantit", "difference between", "compares"]},
    {"id": "mg_kia_gap_misencoded", "origin": "2026-05-23", "ref": "H3", "kind": "mis_encoded_comparison",
     "summary": "an MG-Kia gap compared with Kia's share level",
     "keywords": ["gap", "reference", "level", "mismatch", "apples", "different quantit", "difference between", "compares"]},
]
KEYWORD_SCREEN_RELIABILITY = ("UNRELIABLE: in r2r-57fa83dc8f2f the keyword screen scored the Tesla contradiction as not "
                              "detected (a high-severity critique did challenge it) and the VW-MG mis-encoding as detected "
                              "(it matched the word 'gap'; no critique addressed the mis-encoding). Use the manual review.")
MANUAL_CRITIC_REVIEW = {  # human review of the recorded r2r-57fa83dc8f2f critic reply (2025-11-23); kept separate from scores
    "source": "r2r-57fa83dc8f2f critic reply req_011ChM1EKtZ43v9Xjk9CUzww (2025-11-23), reviewed 2026-11-24",
    "known_issues": {
        "tesla_non_bev_contradiction": {"verdict": "partial", "basis": "H4 [high] calls the non-BEV assumption unverified and "
                                        "load-bearing, but does not use the 100% Tesla segment mix that refutes it"},
        "ford_product_led_outside_evidence": {"verdict": "partial", "basis": "H2 [high] offers fleet-deal timing as a rival; "
                                              "does not state that model-level data are absent"},
        "vw_mg_gap_misencoded": {"verdict": "not_detected", "basis": "H3 critiques address substitution and the conjunctive "
                                 "refutation, not the gap-versus-level reference mismatch"}},
    "promising_observations": [{
        "observation": "the critic independently identified that H1's test cannot discriminate dilution from BEV growth "
                       "(a trend-consistent, uninformative test), the postmortem's central finding, without being told",
        "status": "promising observation from ONE reply (n=1); NOT a validated general capability",
        "needed_to_validate": "the Step 2 planted-defect evaluation on held-out investigations"}]}
CRITERIA_INTERPRETATION = {  # frozen criteria unchanged; how the validator changes affect their reading (all listed)
    "A1_smoke_structured_output_ok": "unaffected by the validator changes",
    "A4_documents_validate": "unaffected (documents gain an optional failures.completion field; the schema is unchanged)",
    "A5_identical_evidence": "unaffected; additionally proven by the identity preflight",
    "A6_spend_within_cap": "unaffected; the cap is this run's own, and the original run's generator spend is reported "
                           "separately as historical",
    "A2_all_live_investigations_completed": "a 'completed' investigation may now carry a partial rejection (ADR-020); "
                                            "the completion state (clean / partial_rejection) is reported next to it; "
                                            "an option kept but not simulated (typed reason, ADR-021) does not make an "
                                            "investigation incomplete, while an internal engine error does (status "
                                            "'stopped', stop_reason 'internal_error: ...'), so A2 fails",
    "A3_no_truncation_refusal_or_top_level_schema_failure": "an over-long array is now rejected as an ARRAY, not as the "
                                                            "whole reply, so A3 can pass while the explanations array was "
                                                            "rejected; array rejections are reported separately",
    "B1_evidence_fidelity_ge_0.80": "stricter: stated directions must now match the numbers (check_directions)",
    "B2_two_original_per_origin": "originality now uses canonical signatures (own-L4 reference = trend continuation) "
                                  "and a prior-knowledge library (earlier dates, no mock/reference output): fewer items "
                                  "count as original than under the original implementation",
    "B3_falsifiability_ge_0.80": "mis-specified comparisons are now not testable, so alternatives pointing at them no "
                                 "longer count as discriminable",
    "B4_testability_ge_0.60": "same change: mis-specified comparisons now count as not testable",
    "B5_informativeness_ge_0.40": "definition unchanged; computed over the (smaller) set of correctly specified tests",
}


class GeneratorCacheMiss(RuntimeError):
    pass


class _CacheOnly:
    """Inner provider for the generator role: it is reached only on a cache MISS, and then refuses."""

    accepts_run_id = True

    def __init__(self, name, model, salt):
        self.name, self.model, self.cache_salt = name, model, salt

    def complete(self, *a, **k):
        raise GeneratorCacheMiss("generator request does not match the recorded reply (cache miss): no live generator call "
                                 "is permitted in this revalidation")


class _NotInPreflight:
    accepts_run_id = True

    def __init__(self, name, model, salt):
        self.name, self.model, self.cache_salt = name, model, salt

    def complete(self, *a, **k):
        raise RuntimeError("identity preflight: live roles are not called")


class _RecordingCache(CachedProvider):
    def complete(self, system, user, *, role, max_output_tokens=None, run_id="unscoped", schema=None):
        self.last = {"key": self.key(system, user, role, schema), "prompt_hash": hashlib.sha256((system + user).encode()).hexdigest()[:16],
                     "prompt_chars": len(system) + len(user), "schema_chars": len(json.dumps(schema)) if schema else 0}
        h0 = self.hits
        r = super().complete(system, user, role=role, max_output_tokens=max_output_tokens, run_id=run_id, schema=schema)
        self.last["cache_hit"] = self.hits > h0
        return r


class _AlwaysLive(CachedProvider):
    """Critic and strategist in Step 1 must be LIVE: never answered from the cache (the reply is still stored)."""

    def complete(self, system, user, *, role, max_output_tokens=None, run_id="unscoped", schema=None):
        self.misses += 1
        kw = {"run_id": run_id} if getattr(self.inner, "accepts_run_id", False) else {}
        if schema is not None:
            kw["schema"] = schema
        r = self.inner.complete(system, user, role=role, max_output_tokens=max_output_tokens, **kw)
        if r.data is not None:
            rec = {f: getattr(r, f) for f in ("text", "data", "input_tokens", "output_tokens", "tokens_estimated", "model",
                                              "provider", "latency_seconds", "cached", "estimated_cost_usd", "actual_cost_usd")}
            self.store.conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?,?)",
                                    (self.key(system, user, role, schema), r.provider, r.model, json.dumps(rec),
                                     r.input_tokens, r.output_tokens, utc_now()))
            self.store.conn.commit()
        return r


def reusable_calls(store, experiment_ids) -> dict:
    """Critic/strategist replies that were PAID FOR in earlier revalidation experiments and may be reused instead of being
    bought again: {(role, prompt_hash): source}. A reply qualifies only if its agent_calls row is a successful, non-cached,
    charged call whose spend scope '<experiment>:<run_id>' is settled in the ledger. The cache key (provider, model, role,
    system prompt, evidence bundle, settings and output schema) must still match at reuse time, or the call goes live."""
    out = {}
    for exp in experiment_ids or ():
        for r in store.conn.execute(
                f"""SELECT a.call_id, a.run_id, a.role, a.model, a.prompt_hash, a.actual_cost_usd, a.input_tokens,
                    a.output_tokens, a.at FROM agent_calls a WHERE a.role IN ({",".join("?" * len(LIVE_ROLES))})
                    AND a.cache_hit=0 AND a.outcome='ok' AND a.actual_cost_usd > 0 ORDER BY a.call_id""", LIVE_ROLES):
            led = store.conn.execute("SELECT note FROM llm_spend_ledger WHERE run_id=? AND status='settled' AND settled_at=?",
                                     (f"{exp}:{r['run_id']}", r["at"])).fetchone()
            if led is None:
                continue
            note = led["note"] or ""
            out.setdefault((r["role"], r["prompt_hash"]), {
                "experiment": exp, "agent_call_id": r["call_id"], "model": r["model"], "paid_usd": r["actual_cost_usd"],
                "tokens": [r["input_tokens"], r["output_tokens"]], "at": r["at"],
                "request_id": note.split("request_id=")[1].split(";")[0] if "request_id=" in note else None})
    return out


class LiveCallNotPermitted(RuntimeError):
    pass


class _ReuseOrLive(_AlwaysLive):
    """Critic/strategist: reuse a reply already PAID FOR (see reusable_calls) when the request is byte-identical (same prompt
    fingerprint and same cache key); otherwise behave as _AlwaysLive (live, never a stale cache read). Every decision is
    logged in ``self.log``."""

    def __init__(self, inner, store, reusable: dict, allow_live: bool = True):
        super().__init__(inner, store)
        self.reusable, self.log, self.allow_live = reusable or {}, [], allow_live

    def complete(self, system, user, *, role, max_output_tokens=None, run_id="unscoped", schema=None):
        ph = hashlib.sha256((system + user).encode()).hexdigest()[:16]
        src = self.reusable.get((role, ph))
        hit = src is not None and self.store.conn.execute("SELECT 1 FROM llm_cache WHERE cache_key=?",
                                                          (self.key(system, user, role, schema),)).fetchone() is not None
        self.log.append({"role": role, "prompt_hash": ph, "reused": hit, "source": src if hit else None,
                         "why_not": None if hit else ("no paid reply with this prompt fingerprint" if src is None
                                                      else "cache key differs (schema, model or settings changed)")})
        if hit:
            return CachedProvider.complete(self, system, user, role=role, max_output_tokens=max_output_tokens,
                                           run_id=run_id, schema=schema)
        if not self.allow_live:
            raise LiveCallNotPermitted(f"{role}: no reusable paid reply and live calls are not permitted for this origin")
        return super().complete(system, user, role=role, max_output_tokens=max_output_tokens, run_id=run_id, schema=schema)


class RevalidationRouter(Router):
    """Generator: cache-only. Critic and strategist: live (``mode='live'``) or refused (``mode='preflight'``); with
    ``reuse`` (from reusable_calls), a byte-identical request already paid for is answered by its recorded reply."""

    def __init__(self, store, scope, cfg, *, mode: str, max_usd=None, environ=None, http_client=None, cfg_overrides=None,
                 provider_config=None, reuse: dict | None = None, allow_live: bool = True):
        super().__init__("anthropic", store, scope, max_usd=max_usd, environ=environ, http_client=http_client,
                         cfg_overrides=cfg_overrides, provider_config=provider_config)
        from ..agents.anthropic_provider import cache_salt  # noqa: PLC0415
        self.mode, self._salt = mode, cache_salt(cfg)
        self._gen = _RecordingCache(_CacheOnly("anthropic", self.routing["roles"]["hypothesis_generator"]["model"], self._salt), store)
        self.reuse, self.allow_live = reuse or {}, allow_live
        self._stub = _ReuseOrLive(_NotInPreflight("anthropic", cfg["model"], self._salt), store, self.reuse)
        self._live = {}

    @property
    def reuse_log(self) -> list:
        return self._stub.log + [x for p in self._live.values() for x in p.log]

    def describe(self) -> dict:
        d = super().describe()
        d["kind"] = f"anthropic (revalidation: generator cache-only, {'live' if self.mode == 'live' else 'preflight'} critic/strategist)"
        return d

    def provider(self, role):
        if role == "hypothesis_generator":
            return self._gen, self.routing["roles"][role]["max_output_tokens"]
        if self.mode != "live":
            return self._stub, self.routing["roles"][role]["max_output_tokens"]
        cached, max_out = super().provider(role)
        key = ("live", cached.model)
        if key not in self._live:
            self._live[key] = _ReuseOrLive(cached.inner, self.store, self.reuse, self.allow_live)
        return self._live[key], max_out


def load_original(run_dir) -> dict:
    run_dir = Path(run_dir)
    exp = next(p for p in run_dir.iterdir() if p.is_dir() and p.name.startswith("r2v-"))
    man = json.loads((exp / "manifest.json").read_text())
    docs = {"reference": {}, "mock": {}, "anthropic": {}}
    for f in man["investigations"]:
        docs[f["workforce"]][f["origin"]] = json.loads((exp / f["file"]).read_text())
    return {"experiment_id": man["experiment_id"], "dir": str(exp), "manifest": man, "docs": docs,
            "records": json.loads((exp / "execution_records.json").read_text()),
            "comparison": json.loads((exp / "comparison.json").read_text())}


def _original_fingerprints(store, original) -> dict:
    out = {}
    for o, d in original["docs"]["anthropic"].items():
        gen = next(c for c in d["agents"]["calls"] if c["role"] == "hypothesis_generator")
        row = store.conn.execute("SELECT prompt_hash, model FROM agent_calls WHERE run_id=? AND role='hypothesis_generator' "
                                 "ORDER BY rowid LIMIT 1", (d["input_id"],)).fetchone()
        led = [r for r in original["records"]["spend_ledger"] if r["run_id"].endswith(d["input_id"])]
        out[o] = {"input_id": d["input_id"], "prompt_hash": row["prompt_hash"] if row else None, "model": row["model"] if row else None,
                  "prompt_chars": gen["prompt_chars"], "schema_chars": gen["schema_chars"],
                  "prompt_version": d["provenance"]["prompt_version"], "panel_fingerprint": d["evidence"]["panel"]["fingerprint"],
                  "request_id": next((n.split("request_id=")[1] for n in (r["note"] or "" for r in led) if "request_id=" in n), None),
                  "generator_cost_usd": sum(r["actual_usd"] or 0 for r in led), "tokens": [gen["input_tokens"], gen["output_tokens"]]}
    return out


def identity_preflight(db_path, market_path, original, origins, evaluate_as_of, cfg, reuse_from=()) -> dict:
    """On a THROWAWAY copy: rebuild each generator request and check it is the recorded one. No network. With
    ``reuse_from``, critic/strategist requests identical to replies paid for in those experiments are answered from their
    recorded reply, and ``per_origin[o]['reuse']`` lists which live-role calls are covered (the rest stay live)."""
    tmp = Path(tempfile.mkdtemp(prefix="halibut_revalidate_")) / "p.sqlite"
    a, b = sqlite3.connect(db_path), sqlite3.connect(tmp)
    a.backup(b)
    a.close(), b.close()
    st = Store(tmp)
    try:
        orig = _original_fingerprints(st, original)
        reuse = reusable_calls(st, reuse_from)
        rows = {}
        for o in origins:
            r = RevalidationRouter(st, "preflight", cfg, mode="preflight", reuse=reuse)
            internal = None
            try:
                d = run_investigation(st, market_path, as_of=o, evaluate_as_of=evaluate_as_of, agents="recorded", router=r,
                                      log=lambda *_: None)
            except InvestigationInternalError as exc:  # an engine defect reached offline: reported, nothing is sent
                d, internal = exc.doc, exc.doc["stop_reason"]
            last = getattr(r._gen, "last", {})
            cached = st.conn.execute("SELECT provider, model, created_at FROM llm_cache WHERE cache_key=?",
                                     (last.get("key", ""),)).fetchone()
            got = {"cache_hit": bool(last.get("cache_hit")), "cache_row_created_at": cached["created_at"] if cached else None,
                   "model": cached["model"] if cached else None, "prompt_hash": last.get("prompt_hash"),
                   "prompt_chars": last.get("prompt_chars"), "schema_chars": last.get("schema_chars"),
                   "prompt_version": d["provenance"]["prompt_version"], "panel_fingerprint": d["evidence"]["panel"]["fingerprint"]}
            want = orig[o]
            checks = {"cache_hit": got["cache_hit"], "model": got["model"] == want["model"],
                      "prompt_fingerprint": got["prompt_hash"] == want["prompt_hash"],
                      "prompt_version": got["prompt_version"] == want["prompt_version"],
                      "prompt_size": got["prompt_chars"] == want["prompt_chars"], "schema_size": got["schema_chars"] == want["schema_chars"],
                      "evidence_fingerprint": got["panel_fingerprint"] == want["panel_fingerprint"]}
            rows[o] = {"original": want, "rebuilt": got, "checks": checks, "identical": all(checks.values()),
                       "reuse": {role: next((x for x in r.reuse_log if x["role"] == role and x["reused"]), None)
                                 for role in LIVE_ROLES},
                       "offline_status": d["status"], "offline_completion": d["failures"]["completion"]["state"],
                       "internal_error": internal}
        return {"identical": all(v["identical"] for v in rows.values()), "per_origin": rows}
    finally:
        st.close()
        shutil.rmtree(tmp.parent, ignore_errors=True)


def critic_detection(docs: dict) -> list[dict]:
    out = []
    for k in KNOWN_ISSUES:
        d = docs.get(k["origin"])
        row = {"issue": k["id"], "kind": k["kind"], "origin": k["origin"], "target": k["ref"], "summary": k["summary"]}
        if k.get("applicable") is False:
            out.append({**row, "detected": None, "note": k["reason"], "critiques": []})
            continue
        h = next((h for h in (d or {}).get("stages", {}).get("hypotheses", []) if h["data"]["ref"] == k["ref"]), None)
        if d is None or h is None:
            out.append({**row, "detected": None, "note": "hypothesis not in the revalidation document", "critiques": []})
            continue
        crits = [c for c in d["stages"]["critiques"] if any(ln["to"] == h["id"] for ln in c["links"])]
        hits = sorted({kw for c in crits for kw in k["keywords"] if kw in c["label"].lower()
                       or kw in str(c["data"].get("challenged_assumption", "")).lower()})
        out.append({**row, "detected": bool(hits), "matched_keywords": hits, "automated_score": "keyword_screen_unreliable",
                    "note": "keyword screen only (unreliable, see KEYWORD_SCREEN_RELIABILITY): a human must confirm",
                    "critiques": [{"severity": c["data"]["severity"], "statement": c["label"],
                                   "challenged_assumption": c["data"].get("challenged_assumption")} for c in crits]})
    return out


def strategist_assessment(docs: dict, reference_docs: dict) -> dict:
    out = {}
    for o, d in docs.items():
        metrics = {m["id"] for m in d["evidence"]["metrics"]}
        ref_levers = [set(x["data"]["levers"]) for x in reference_docs.get(o, {}).get("stages", {}).get("strategic_options", [])]
        opts = []
        mde = (d["experiment_protocol"] or {}).get("data", {}).get("power", {}).get("national_mde_pp") if d["experiment_protocol"] else None
        for x in d["stages"]["strategic_options"]:
            v = x["data"]
            e = v["intended_effect"]
            ev = v["supporting_evidence"]
            sims = [ln["to"] for ln in x["links"] if ln["relation"] == "simulated_by"]
            opts.append({
                "title": x["label"], "levers": v["levers"], "mode": v["mode"],
                "originality": {"same_levers_as_a_reference_template": any(set(v["levers"]) == r for r in ref_levers)},
                "evidence": {"cited": len(ev), "all_exist": all(i in metrics for i in ev),
                             "about_intended_entity": sum(1 for i in ev if i.endswith(f":{e['entity']}"))},
                "feasibility": {"cost_known": v["cost"].get("known"), "cost_basis": v["cost"].get("basis"),
                                "assumed_effect_pp": [e["magnitude_low_pp"], e["magnitude_high_pp"]],
                                "assumed_effect_basis": e["basis"], "national_mde_pp": mde,
                                "effect_detectable_nationally": None if mde is None else e["magnitude_low_pp"] >= mde,
                                "large_effect_flag": abs(e["magnitude_high_pp"]) > 2.0},
                "assumptions": len(v["assumptions"]), "competitor_responses": v["competitor_responses"],
                "measurable_outcomes": len(v["success_criteria"]), "simulations": len(sims),
                "simulation_status": v.get("simulation", {}).get("status"),
                "compliance_flags": v["compliance"]["flags"]})
        rej = [f for f in d["failures"]["items"] if f["subject"] == "strategic_option" and f["stage"] != "simulation"]
        nsim = [f for f in d["failures"]["items"] if f["subject"] == "strategic_option" and f["stage"] == "simulation"]
        out[o] = {"accepted": opts, "rejected": [{"failure_class": f["failure_class"], "problems": f["problems"]} for f in rej],
                  "not_simulated": [{"failure_class": f["failure_class"], "problems": f["problems"], "ref": f["ref"]} for f in nsim],
                  "distinct_lever_sets": len({tuple(sorted(o_["levers"])) for o_ in opts})}
    return out


def run_revalidation(store, db_path, market_path, original_run_dir, origins, evaluate_as_of, *, live=False, approval_id=None,
                     max_usd=None, out_dir="results/r2_validation", environ=None, http_client=None, cfg_overrides=None,
                     provider_config=None, reuse_from=(), live_origins=None, log=print) -> dict:
    """``reuse_from``: earlier revalidation experiment ids whose PAID critic/strategist replies may be reused when the
    request is byte-identical (verified by the preflight and again at call time). Every other call is live.
    ``live_origins``: if given, ONLY these origins may make live critic/strategist calls; every other origin must be fully
    covered by reused replies (refused before any paid call otherwise, and refused again at call time)."""
    from ..agents.llm import load_llm_config  # noqa: PLC0415
    market = load_market(market_path)
    rt = load_routing()
    cfg_path = Path(provider_config) if provider_config else Path(__file__).resolve().parents[3] / rt["provider_config"]
    cfg = {**load_llm_config(cfg_path, model=rt["default_model"]), **(cfg_overrides or {})}
    original = load_original(original_run_dir)
    origins = sorted(origins)
    if sorted(original["docs"]["anthropic"]) != origins:
        raise ValueError(f"origins {origins} differ from the original experiment's {sorted(original['docs']['anthropic'])}")
    exp_id = "r2r-" + hashlib.sha256(json.dumps([original["experiment_id"], origins, utc_now(), uuid.uuid4().hex]).encode()).hexdigest()[:12]
    subject = f"r2_revalidation:{market.id}"
    ident = identity_preflight(db_path, market_path, original, origins, evaluate_as_of, cfg, reuse_from)
    log(f"identity preflight: {'IDENTICAL' if ident['identical'] else 'MISMATCH'}")
    from .live_validation import _plan_live  # noqa: PLC0415
    plan = _plan_live(original["docs"]["mock"], max_usd, cfg)
    reused_rows = [{**r, "origin": o, "reused_from": ident["per_origin"][o]["reuse"][r["role"]]["source"]}
                   for o, rows_ in plan["per_origin"].items() for r in rows_
                   if r["role"] in LIVE_ROLES and ident["per_origin"].get(o, {}).get("reuse", {}).get(r["role"])]
    live_rows = [r for o, rows_ in plan["per_origin"].items() for r in rows_ if r["role"] in LIVE_ROLES
                 and not ident["per_origin"].get(o, {}).get("reuse", {}).get(r["role"])]
    worst = sum(r["worst_case_usd"] for r in live_rows) + plan["smoke_worst_case_usd"]
    retries = 1 + min(int(cfg.get("retries", 1)), 2)
    plan5 = {"calls": 1 + len(live_rows), "worst_case_usd": worst,
             "worst_case_with_retries_usd": sum(r["worst_case_usd"] for r in live_rows) * retries + plan["smoke_worst_case_usd"],
             "cap_usd": max_usd, "rows": live_rows, "generator_calls": 0,
             "reused_calls": [{"origin": r["origin"], "role": r["role"], "source": r["reused_from"]} for r in reused_rows]}
    covered = {o: all(ident["per_origin"][o]["reuse"].get(r) for r in LIVE_ROLES) for o in origins}
    not_covered = [o for o in origins if live_origins is not None and o not in live_origins and not covered[o]]
    plan5["live_origins"] = list(live_origins) if live_origins is not None else origins
    if live:
        if not_covered:
            raise approvals.AuthorizationError(f"origins {not_covered} are not live origins but their critic/strategist "
                                               "requests are not covered by reusable paid replies; nothing was sent")
        if not ident["identical"]:
            raise approvals.AuthorizationError("identity preflight failed: the generator requests are not the recorded ones; "
                                               "nothing was sent")
        if max_usd is None or worst > max_usd:
            raise approvals.AuthorizationError(f"the plan's worst case ${worst:.4f} exceeds the cap {max_usd}; nothing was sent")
        approvals.require(store, approval_id, "paid_run", subject, cost_usd=max_usd)
        approvals.claim_use(store, approval_id, exp_id, f"R2 Step 1 engineering revalidation of {original['experiment_id']}")
    EventLog(store).append("revalidation.started", "L6", {"original": original["experiment_id"], "live": live,
                                                          "identity": ident["identical"], "max_usd": max_usd},
                           run_id=exp_id, subject_id=approval_id, provenance={"version": VERSION}, commit=True)
    smoke, new, internal, reuse_log = None, {}, {}, []
    if live:
        smoke = _smoke(store, exp_id, cfg, environ, http_client)
        log(f"smoke: {'OK' if smoke.get('ok') else 'FAILED'}")
        if smoke.get("ok"):
            reuse = reusable_calls(store, reuse_from)
            for o in origins:
                r = RevalidationRouter(store, exp_id, cfg, mode="live", max_usd=max_usd, environ=environ, http_client=http_client,
                                       cfg_overrides=cfg_overrides, provider_config=cfg_path, reuse=reuse,
                                       allow_live=live_origins is None or o in live_origins)
                try:
                    new[o] = run_investigation(store, market_path, as_of=o, evaluate_as_of=evaluate_as_of, agents="anthropic",
                                               router=r, approval_id=approval_id, max_usd=max_usd, approval_subject=subject,
                                               log=lambda *_: None)
                except InvestigationInternalError as exc:  # stored as 'stopped'; no further paid calls on a defective engine
                    new[o], internal[o] = exc.doc, exc.doc["stop_reason"]
                except approvals.AuthorizationError:
                    raise
                except Exception as exc:  # noqa: BLE001  outside the loop (no document): recorded, then stop spending
                    internal[o] = f"internal_error (no document): {type(exc).__name__}: {exc}"[:500]
                reuse_log += [{"origin": o, **x} for x in r.reuse_log]
                log(f"{o}: " + (f"{new[o]['status']} [{new[o]['failures']['completion']['state']}]" if o in new else "")
                    + (f" INTERNAL ERROR: {internal[o]}" if o in internal else ""))
                if o in internal:
                    skipped = [x for x in origins if x > o]
                    for x in skipped:
                        internal[x] = f"not run: an internal error in {o} stopped further paid investigations"
                    break
    out = Path(out_dir) / exp_id  # raw records first, so a failure in the reporting below cannot lose them
    (out / "investigations").mkdir(parents=True, exist_ok=True)
    files = []
    for o, d in new.items():
        name = f"investigations/mixed_cached_live_claude_{o}.json"
        (out / name).write_text(json.dumps(d, indent=1, sort_keys=True, default=str) + "\n")
        files.append({"file": name, "result_class": "mixed_cached_live", "origin": o, "investigation_id": d["investigation_id"],
                      "content_hash": d["content_hash"], "status": d["status"], "completion": d["failures"]["completion"]["state"]})
    docs_ = {"reference": original["docs"]["reference"], "mock": original["docs"]["mock"], "anthropic": new}
    (out / "execution_records.json").write_text(json.dumps({**execution_records(store, exp_id, approval_id, docs_),
                                                            "internal_errors": internal, "reuse_log": reuse_log}, indent=1,
                                                           sort_keys=True, default=str) + "\n")
    spend = store.conn.execute("""SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_usd ELSE reserved_usd END), 0),
        COUNT(*) FROM llm_spend_ledger WHERE run_id LIKE ?""", (exp_id + "%",)).fetchone()
    docs = {"reference": original["docs"]["reference"], "mock": original["docs"]["mock"], "anthropic": new}
    pf = preflight(store, market, origins, evaluate_as_of, cfg.get("training_cutoff"))
    base = _report(exp_id, market, origins, evaluate_as_of, pf, plan, smoke, docs, spend, max_usd, live, cfg, approval_id)
    orig_fp = ident["per_origin"]
    # from the LEDGER, so it agrees with the incremental total: a failed attempt keeps its reservation as charged
    # (fail-closed, budget.py) even when the provider is known not to bill it (e.g. HTTP 401)
    smoke_cost = float(store.conn.execute("""SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_usd ELSE reserved_usd
        END), 0) FROM llm_spend_ledger WHERE run_id=?""", (f"{exp_id}:smoke",)).fetchone()[0])
    costs = {}
    for o in origins:
        hist = orig_fp[o]["original"]["generator_cost_usd"]
        inc = sum(float(x["actual_usd"] or x["reserved_usd"]) for x in store.conn.execute(
            "SELECT actual_usd, reserved_usd FROM llm_spend_ledger WHERE run_id LIKE ? AND run_id NOT LIKE ?",
            (f"{exp_id}:{new[o]['input_id']}%" if o in new else "__none__", f"{exp_id}:smoke%")))
        reused = sum(x["source"]["paid_usd"] for x in plan5["reused_calls"] if x["origin"] == o)
        costs[o] = {"historical_generator_usd (r2v-b16fc9281536, recorded reply reused)": hist,
                    "historical_reused_critic_strategist_usd": reused, "incremental_live_usd": inc,
                    "total_equivalent_investigation_usd": hist + reused + inc}
    base = {**base, "plan_original_experiment": base["plan"],  # the r2v plan; Step 1's own plan is plan_step1
            "plan": {**base["plan"], "worst_case_usd": plan5["worst_case_usd"],
                     "worst_case_with_retries_usd": plan5["worst_case_with_retries_usd"], "calls": plan5["calls"]}}
    report = {**base, "label": LABEL, "version": VERSION, "original_experiment": original["experiment_id"],
              "identity_preflight": ident, "plan_step1": plan5, "criteria_hash": CRITERIA_HASH,
              "criteria_unchanged": True, "criteria_interpretation": CRITERIA_INTERPRETATION,
              "completion": {o: d["failures"]["completion"] for o, d in new.items()},
              "internal_errors": internal, "reuse_log": reuse_log,
              "critic_detection": critic_detection(new), "critic_detection_reliability": KEYWORD_SCREEN_RELIABILITY,
              "critic_review_manual": MANUAL_CRITIC_REVIEW, "strategist_assessment": strategist_assessment(new, original["docs"]["reference"]),
              "costs_step1": {"smoke_usd": smoke_cost, "per_origin": costs, "incremental_live_total_usd": float(spend[0]),
                              "historical_generator_total_usd": sum(v["original"]["generator_cost_usd"] for v in orig_fp.values()),
                              "ledger_attempts": spend[1]}}
    if internal:
        report["verdict"] = {**report["verdict"], "pipeline_ready": False,
                             "internal_error": "an unexpected engine error occurred; see internal_errors"}
    (out / "comparison.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n")
    (out / "comparison.md").write_text(revalidation_markdown(report) + "\n")
    (out / "manifest.json").write_text(json.dumps({
        "experiment_id": exp_id, "version": VERSION, "label": LABEL, "live": live, "original_experiment": original["experiment_id"],
        "original_dir": original["dir"], "criteria_hash": CRITERIA_HASH, "investigations": files,
        "baselines_reused_from_original": [{"workforce": w, "origin": o, "content_hash": d["content_hash"]}
                                           for w in ("reference", "mock") for o, d in original["docs"][w].items()],
        "result_classes": {"mixed_cached_live": "recorded Claude generator replies + live Claude critic/strategist",
                           "mock_not_intelligence": "original run's mock documents (unchanged)",
                           "deterministic_baseline": "original run's reference documents (unchanged)"}}, indent=1) + "\n")
    EventLog(store).append("revalidation.completed", "L6", {"spend_usd": spend[0], "pipeline_ready": report["verdict"]["pipeline_ready"]},
                           run_id=exp_id, subject_id=approval_id, commit=True)
    if live:
        EventLog(store).append("approval.consumed", "L8", {"spend_usd": spend[0], "cap_usd": max_usd, "experiment": exp_id},
                               subject_id=approval_id, commit=True)
    report["output_dir"] = str(out)
    return report


def revalidation_markdown(r: dict) -> str:
    L = [f"# R2 Step 1 {r['experiment_id']} ({'LIVE' if r['live'] else 'PREFLIGHT / DRY RUN'})", "", f"> **{r['label']}**", "",
         f"Original experiment `{r['original_experiment']}`; criteria hash `{r['criteria_hash'][:16]}` (unchanged).", "",
         "## Identity preflight", ""]
    for o, v in r["identity_preflight"]["per_origin"].items():
        L.append(f"- **{o}**: {'IDENTICAL' if v['identical'] else 'MISMATCH'} {v['checks']} (original request "
                 f"{v['original']['request_id']}, prompt fingerprint {v['original']['prompt_hash']})")
    p = r["plan_step1"]
    L += ["", f"Plan: {p['calls']} live calls (0 generator calls); worst case ${p['worst_case_usd']:.4f}, with retries "
              f"${p['worst_case_with_retries_usd']:.4f}; cap {p['cap_usd']}.", "", "## Costs (separately)", ""]
    c = r["costs_step1"]
    if r.get("smoke") and not r["smoke"].get("ok"):
        L.append(f"- SMOKE FAILED: {r['smoke'].get('error', '')}")
    L.append(f"- smoke ${c['smoke_usd']:.6f} (ledger; a failed attempt keeps its reservation, fail-closed); incremental live total ${c['incremental_live_total_usd']:.6f}; historical "
             f"generator total ${c['historical_generator_total_usd']:.6f} (recorded replies reused)")
    for o, v in c["per_origin"].items():
        L.append(f"- {o}: " + "; ".join(f"{k} ${x:.6f}" for k, x in v.items()))
    if p.get("reused_calls"):
        L += ["", "Reused (already paid for, byte-identical request; not bought again):"] + [
            f"- {x['origin']} {x['role']}: {x['source']['experiment']} call {x['source']['agent_call_id']} "
            f"({x['source']['request_id']}, ${x['source']['paid_usd']:.6f})" for x in p["reused_calls"]]
    if r.get("internal_errors"):
        L += ["", "## INTERNAL ERRORS (engine defects; not intelligence failures)", ""] + [
            f"- **{o}**: {e}" for o, e in r["internal_errors"].items()]
    L += ["", "## Completion (ADR-020)", ""] + [f"- {o}: {v['state']} (rejected arrays: {[a['subject'] for a in v['rejected_arrays']]})"
                                                 for o, v in r["completion"].items()]
    L += ["", "## Critic detection of the postmortem's known issues (critic was not told about them)", "",
          f"> Automated keyword scores: {r.get('critic_detection_reliability', '')}", "", "Manual review (separate record):"]
    m = r.get("critic_review_manual", {})
    L += [f"- {k}: **{v['verdict']}**, {v['basis']}" for k, v in m.get("known_issues", {}).items()]
    L += [f"- promising observation ({x['status']}): {x['observation']}" for x in m.get("promising_observations", [])]
    L += ["", "Automated keyword screen (unreliable):"]
    for x in r["critic_detection"]:
        L.append(f"- {x['issue']} ({x['origin']} {x['target']}, {x['kind']}): "
                 f"{'n/a' if x['detected'] is None else 'CANDIDATE DETECTED' if x['detected'] else 'not detected'} {x.get('matched_keywords', '')} - {x['note']}")
        L += [f"  - [{c_['severity']}] {c_['statement']}" for c_ in x["critiques"]]
    L += ["", "## Strategist assessment", ""]
    for o, v in r["strategist_assessment"].items():
        L.append(f"- **{o}**: {len(v['accepted'])} accepted, {len(v['rejected'])} rejected, {v['distinct_lever_sets']} distinct lever sets")
        for x in v["accepted"]:
            L.append(f"  - {x['title']} | levers {x['levers']} | evidence {x['evidence']} | feasibility {x['feasibility']} | "
                     f"assumptions {x['assumptions']} | competitor responses {len(x['competitor_responses'])} | measurable outcomes "
                     f"{x['measurable_outcomes']} | template-like {x['originality']['same_levers_as_a_reference_template']}")
        L += [f"  - REJECTED {x['failure_class']}: {x['problems'][:2]}" for x in v["rejected"]]
        L += [f"  - NOT SIMULATED {x['failure_class']}: {x['problems'][:1]}" for x in v.get("not_simulated", [])]
    L += ["", "## How the validator changes affect the frozen criteria", ""] + [f"- **{k}**: {v}" for k, v in r["criteria_interpretation"].items()]
    L += ["", "---", "", markdown(r)]
    return "\n".join(L)
