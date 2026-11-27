"""The R2 intelligence operating loop (orchestrator, L4 + all layers).

    objective -> coverage contract -> evidence panel (as of t) -> world slice -> [LLM 1] explanations + original
    hypotheses -> EI checks, novelty, duplicates -> [LLM 2] critique -> tests (pre-registered at t) -> model specs
    -> registry validation -> official forecasts / labelled estimates -> [LLM 3] strategic options (as of t) ->
    compliance + simulations -> draft experiment protocol -> (optional) evaluation at a later date: test results,
    hypothesis statuses, forecast scores -> proposed lessons -> investigation result document.

Plan: a fixed template with adaptive skips (no hypotheses -> no critic/strategist; no options -> no protocol).
Stopping rules: the generator's reply unusable (truncated, refused, invalid, top-level schema failure) stops the
investigation with that reason; budget exhaustion stops it (spend guard); everything recorded so far is kept.
At most 3 LLM calls per investigation; identical reruns hit the prompt cache and reproduce the same content.

Strategy is formed with information available at t only: test outcomes (published later) are never shown to the
strategist. Paid agents need a granted 'paid_run' approval for 'investigation:<market id>' (and the provider's
own approval and caps). Nothing here executes external actions.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import traceback
from pathlib import Path

from ..agents.loop_agents import AGENT_IDS, PROMPT_VERSION, call_role
from ..agents.routing import Router
from ..control import approvals
from ..data.store import to_timestamp, utc_now
from ..epistemic import schemas as sch
from ..epistemic.claims import ClaimLedger
from ..epistemic.events import EventLog
from ..epistemic.failures import classify_problems, record
from ..intel.market import MODES, load_market
from ..learning.memory import LearningMemory
from ..models import registry as reg
from ..validation import ALLOWED_LEVERS, allowed_numbers, check_directions, check_numbers
from ..version import ENGINE_VERSION
from ..world.context import build_slice, reference_library, signature
from ..world.coverage import check_contract
from ..world.panel import build_panel
from . import artifact as art
from . import compiler, strategy
from . import loop_v2 as v2

SUBJECT = {"hypotheses": "hypothesis", "explanations": "explanation", "information_gaps": "information_gap"}

ENGINE = "engine:investigation"


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


class _Run:
    """Collects the document while the loop runs."""

    def __init__(self, store, input_id, investigation_id, as_of_ts, tenant):
        self.store, self.input_id, self.id, self.as_of, self.tenant = store, input_id, investigation_id, as_of_ts, tenant
        self.ledger, self.events, self.memory = ClaimLedger(store), EventLog(store), LearningMemory(store)
        self.stages = {k: [] for k in art.STAGES}
        self.links, self.failures, self.calls, self.approvals_ = [], [], [], []
        self.protocol = None
        self.partial = {}  # evidence and world, once built (kept if the loop fails)

    def fail(self, subject, stage, outcome, cls, problems, item, by):
        record(self.store, run_id=self.input_id, produced_by=by, subject=subject, stage=stage, outcome=outcome,
               failure_class=cls, problems=problems, item=item, as_of=self.as_of, tenant=self.tenant, events=self.events)
        self.failures.append({"subject": subject, "stage": stage, "outcome": outcome, "failure_class": cls,
                              "problems": list(problems)[:5], "ref": item.get("ref") if isinstance(item, dict) else None})

    def claim(self, ctype, statement, by, payload, *, evidence=(), exists=None, provenance=None, available_at=None):
        cid, problems = self.ledger.submit(ctype, statement, by, as_of=self.as_of, run_id=self.input_id, payload=payload,
                                           provenance=provenance or {}, evidence=list(evidence), exists=exists,
                                           available_at=available_at, tenant=self.tenant, commit=False)
        if problems:
            self.failures.append({"subject": ctype, "stage": "ledger", "outcome": "rejected", "failure_class": f"ledger.{ctype}",
                                  "problems": problems[:5], "ref": None})
        return cid

    def link(self, frm, rel, to, by):
        self.links.append({"from": frm, "relation": rel, "to": to})
        if frm.startswith("c:") and rel in ("supports", "contradicts", "derived_from", "tests", "critiques", "supersedes",
                                            "duplicates", "explains"):
            self.ledger.link(frm, rel, to, by, commit=False)

    def add(self, stage, it):
        self.stages[stage].append(it)
        return it


# offline workforces: 'mock' and 'reference' are SCRIPTED (not intelligence); 'recorded' replays SAVED replies of a real
# model (genuine AI output, served from the cache; no network, no spend)
OFFLINE_AGENTS = ("mock", "reference", "recorded")


def run_investigation(store, market_path, *, as_of: str | None = None, evaluate_as_of: str | None = None,
                      agents: str = "mock", router: Router | None = None, approval_id: str | None = None,
                      max_usd: float | None = None, tenant: str = "public", faults: bool = False, decision: str | None = None,
                      approval_subject: str | None = None, contract: str = "loop.v1", measurements: str = "base",
                      prospective: bool = False, log=print) -> dict:
    """``approval_subject`` (default ``investigation:<market id>``): the subject of the granted paid_run approval
    that authorizes paid agents (a validation experiment authorizes several investigations with one approval)."""
    if contract not in v2.CONTRACTS or measurements not in v2.MEASUREMENTS:
        raise ValueError(f"unknown contract {contract!r} or measurements {measurements!r}")
    if contract == v2.V1 and measurements != "base":
        raise ValueError("loop.v1 is frozen to the base measurement set")
    # the v1 prompt version stays exactly 'loop.v1' in every hash, so recorded v1 requests and ids are unchanged
    pv = PROMPT_VERSION if contract == v2.V1 else f"{v2.CONTRACT}:{measurements}"
    registered_at = utc_now()
    if prospective:  # registration is the REAL call time; the evidence date must be the registration date (no backdating)
        if as_of and as_of != registered_at[:10]:
            raise ValueError(f"a prospective investigation registers on its run date ({registered_at[:10]}), not {as_of}")
        as_of, evaluate_as_of = registered_at[:10], None
    market_path = Path(market_path)
    market = load_market(market_path)
    spec_sha = hashlib.sha256(market_path.read_bytes()).hexdigest()
    as_of = as_of or market.as_of
    from .holdout import guard  # noqa: PLC0415  (sealed holdout origins are refused before anything runs)
    guard(market.id, as_of)
    market = dataclasses.replace(market, as_of=as_of)
    t0 = to_timestamp(as_of)
    t_eval = to_timestamp(evaluate_as_of) if evaluate_as_of else None
    if t_eval and t_eval <= t0:
        raise ValueError("evaluate_as_of must be after as_of")
    if agents == "recorded" and router is None:
        raise ValueError("agents='recorded' replays saved replies and needs an offline router (e.g. RevalidationRouter)")
    if agents not in OFFLINE_AGENTS:  # paid agents
        if max_usd is None:
            raise approvals.AuthorizationError("paid agents need --max-usd")
        approvals.require(store, approval_id, "paid_run", approval_subject or f"investigation:{market.id}", cost_usd=max_usd)
    pre_id = "inv:" + _h([spec_sha, t0, t_eval, agents, pv, faults, tenant, reg.RULE])[:16]
    router = router or Router(agents, store, scope=pre_id, max_usd=max_usd)
    if agents not in OFFLINE_AGENTS:  # single use: bind the approval to this spend scope (or verify the binding)
        a = approvals.require(store, approval_id, "paid_run", approval_subject or f"investigation:{market.id}",
                              cost_usd=max_usd, use_id=router.scope)
        if a["used_by"] is None:
            approvals.claim_use(store, approval_id, router.scope, f"investigation {market.id} as of {as_of}")
    input_id = "inv:" + _h([spec_sha, t0, t_eval, router.fingerprint, pv, faults, tenant, reg.RULE])[:16]
    n = store.conn.execute("SELECT COUNT(*) FROM investigations WHERE investigation_id LIKE ?", (input_id + ".%",)).fetchone()[0]
    R = _Run(store, input_id, f"{input_id}.{n + 1}", t0, tenant)
    R.events.append("investigation.started", "L4", {"market": market.id, "as_of": t0, "evaluate_as_of": t_eval, "agents": agents},
                    run_id=input_id, subject_id=R.id, provenance={"router": router.describe()}, tenant=tenant)
    objective = {"id": "obj:" + input_id.split(":")[1], "market_id": market.id, "market_name": market.name,
                 "category": market.category, "mode": market.mode, "focal": market.focal, "target": market.target,
                 "decision": decision or f"which lawful options to pursue for {market.focal or 'the focal competitor'} "
                                         f"({market.mode})",
                 "as_of": t0, "evaluate_as_of": t_eval, "horizon_quarters": 2, "jurisdiction": market.geography,
                 "success_metric": "segment_share" if (market.transactions or {}).get("segment") else "share",
                 "budget_usd": max_usd if agents not in OFFLINE_AGENTS else 0.0}
    ctx = {"market": market, "objective": objective, "router": router, "spec_sha": spec_sha, "agents": agents,
           "faults": faults, "t_eval": t_eval, "contract": contract, "measurements": measurements, "pv": pv,
           "prospective": {"registered_at": registered_at} if prospective else None}
    if contract == v2.CONTRACT:
        R.stages["information_gaps"] = []
    status, reason, evidence, world = "completed", None, {"coverage": {}, "panel": {}, "metrics": []}, \
        {"slice_id": "", "market": {}, "entities": [], "anomalies": []}
    coverage_contract = check_contract(market)
    evidence["coverage"] = {"contract": coverage_contract.as_dict()}
    panel = None
    if not coverage_contract.valid:
        status, reason = "invalid_market", "; ".join(coverage_contract.violations)[:500]
    else:
        try:
            panel = build_panel(store, market, as_of)
        except (ValueError, FileNotFoundError) as exc:
            status, reason = "insufficient_evidence", f"no transaction panel: {exc}"[:500]
        if panel is not None and len(panel.periods) < 8:
            status, reason = "insufficient_evidence", f"{len(panel.periods)} quarters available as of {t0}; at least 8 needed"
    internal = None
    if status == "completed":
        try:
            status, reason, evidence, world = _loop(R, ctx, panel, evidence)
        except Exception as exc:  # noqa: BLE001  an unexpected engine error: stored visibly, then re-raised below
            internal = exc
            evidence, world = R.partial.get("evidence", evidence), R.partial.get("world", world)
            status, reason = "stopped", f"internal_error: {type(exc).__name__}: {exc}"[:500]
            R.failures.append({"subject": "engine", "stage": "internal", "outcome": "error", "failure_class": "internal.error",
                               "problems": [f"{type(exc).__name__}: {exc}"[:300]] + _where(exc), "ref": None})
    doc = _document(R, ctx, status, reason, evidence, world, panel)
    problems = art.validate(doc)
    if problems:
        raise RuntimeError(f"investigation result violates {art.SCHEMA_ID}: {problems[:5]}")
    store.conn.execute("INSERT INTO investigations VALUES (?,?,?,?,?,?,?,?,?)",
                       (R.id, market.id, tenant, t0, t_eval, status, doc["content_hash"], json.dumps(doc, sort_keys=True,
                                                                                                    default=str), utc_now()))
    R.events.append("investigation.internal_error" if internal else "investigation.completed", "L4",
                    {"status": status, "content_hash": doc["content_hash"], "stop_reason": reason}, run_id=input_id,
                    subject_id=R.id, tenant=tenant)
    store.conn.commit()
    log(f"{R.id}: {status}" + (f" ({reason})" if reason else ""))
    if internal is not None:
        raise InvestigationInternalError(doc) from internal
    return doc


class InvestigationInternalError(RuntimeError):
    """An unexpected engine error inside an investigation. The partial document (status 'stopped', stop_reason
    'internal_error: ...', failure class internal.error) has already been stored; ``doc`` carries it."""

    def __init__(self, doc: dict):
        super().__init__(doc["stop_reason"])
        self.doc = doc


def _where(exc) -> list[str]:
    tb = traceback.extract_tb(exc.__traceback__)
    return [f"at {Path(f.filename).name}:{f.lineno} in {f.name}" for f in tb[-3:]]


def _loop(R: _Run, ctx: dict, panel, evidence: dict):
    market, router, store = ctx["market"], ctx["router"], R.store
    lessons = R.memory.retrieve(tags=[f"market:{market.id}"], kinds=["lesson"], statuses=["validated"], as_of=R.as_of)
    cov = {"status": "covered" if all(len(panel.series("share", e)) >= 8 for e in panel.entities) else "partial",
           "contract": evidence["coverage"]["contract"],
           "per_competitor_quarters": {e: len(panel.series("share", e)) for e in panel.entities}, "gaps": []}
    cov["gaps"] = [f"{e}: {q} quarters" for e, q in cov["per_competitor_quarters"].items() if q < 8]
    V2, ext = ctx["contract"] == v2.CONTRACT, ctx["measurements"] == "extended"
    slice_, metrics = build_slice(market, panel, cov, lessons, extended=ext)
    sysp = v2.systems(ctx["contract"])
    agent_slice = v2.compact_slice(slice_, metrics) if V2 else slice_
    if ctx["faults"]:
        slice_["mock_faults"] = True
    exists = (lambda e: e in metrics or panel.exists(e))  # noqa: E731
    allowed = allowed_numbers(metrics)
    fingerprint = _h(sorted((f"{k[0]}|{k[1]}", sorted((p, v[0]) for p, v in c.items())) for k, c in panel.cells.items()))
    evidence = {"coverage": cov, "panel": {"source": panel.source, "universe": panel.universe, "segment": panel.segment,
                                           "fingerprint": fingerprint,
                                           "first_period": panel.periods[0], "last_period": panel.periods[-1],
                                           "n_periods": len(panel.periods), "snapshot": panel.snapshot},
                "metrics": [art.item(m["id"], "derived_metric", "recorded", f"{m['template']} {m['entity']} {m['window']}",
                                     "engine:panel", R.as_of, {"value": m["value"], "definition": m["definition"],
                                                               "inputs_count": len(m["inputs"])}) for m in metrics.values()]}
    world = {"slice_id": slice_["slice_id"], "market": slice_["market"],
             "entities": [{"id": c.id, "name": c.name, "identifiers": c.identifiers} for c in market.competitors],
             "anomalies": slice_["anomalies"]}
    R.partial = {"evidence": evidence, "world": world}
    prov = {"market_id": market.id, "investigation": R.input_id, "slice_id": slice_["slice_id"], "prompt_version": ctx["pv"]}
    gen_by = AGENT_IDS["hypothesis_generator"]

    # ---------------------------------------------------------------- 1. explanations + original hypotheses
    families = [f for f in reg.FAMILIES if f != "simulation.share_shift"]
    if V2:
        gschema = v2.generator_schema(list(metrics), panel.entities, families, ext)
        res = call_role(router, store, "hypothesis_generator", {"slice": agent_slice, "contract": v2.CONTRACT}, gschema,
                        R.input_id, system=sysp["hypothesis_generator"])
        gschema = v2.inline(gschema)  # local validation of the same contract
    else:
        gschema = sch.generator_schema(list(metrics), panel.entities, families)
        res = call_role(router, store, "hypothesis_generator", {"slice": slice_}, gschema, R.input_id)
    R.calls.append(_call_summary("hypothesis_generator", res))
    if res["data"] is None:
        R.fail("hypothesis_generator", "call", "failed", res["failure_class"] or "call.error", [res["outcome"]], {}, gen_by)
        return "stopped", f"hypothesis generator reply unusable ({res['outcome']})", evidence, world
    arrays_v = ["explanations", "hypotheses"] + (["information_gaps"] if V2 else [])
    valid, rejected, arrays, top = sch.split_multi(res["data"], gschema, arrays_v)
    if top:
        R.fail("hypothesis_generator", "schema", "rejected", "call.schema_top_level", top, res["data"], gen_by)
        return "stopped", f"hypothesis generator reply violates its schema: {top[0]}", evidence, world
    for key, probs in arrays.items():  # an array rejected as a whole: recorded with its items, never trimmed
        R.fail(SUBJECT[key], "schema", "rejected", "schema.array_limit",
               probs, {"array": key, "items": res["data"][key]}, gen_by)
    if "hypotheses" in arrays:
        return "stopped", f"hypothesis generator reply: {arrays['hypotheses'][0]}", evidence, world
    for key, rows in rejected.items():
        for _, it, pr in rows:
            R.fail(SUBJECT[key], "schema", "rejected",
                   classify_problems(pr, None), pr, it, gen_by)
    for e in valid["explanations"]:
        pr = check_numbers(e["statement"], allowed) + check_directions(e["statement"])
        if pr:
            R.fail("explanation", "validation", "rejected", classify_problems(pr, None), pr, e, gen_by)
            continue
        cid = R.claim("explanation", e["statement"], gen_by, {"anomaly_based": True}, evidence=e["evidence"], exists=exists,
                      provenance=prov)
        if cid:
            R.add("explanations", art.item(cid, "explanation", "proposed", e["statement"], gen_by, R.as_of, {},
                                           [("cites", x) for x in e["evidence"]], prov))
    library = reference_library(R.ledger, market, panel, metrics, exclude_run=R.input_id)
    accepted, seen = [], []
    # v2: discrimination between rival hypotheses, from the schema-valid hypotheses that pass the semantic checks
    cands = [h for h in valid["hypotheses"] if not _semantic_checks(h, allowed, [])] if V2 else []
    disc, assessments = (v2.discrimination(cands), v2.rival_assessments(cands)) if V2 else ({}, {})
    for h in valid["hypotheses"]:
        pr = _semantic_checks(h, allowed, [a["ref"] for a in accepted])
        if pr:
            R.fail("hypothesis", "validation", "rejected", classify_problems(pr, None), pr, h, gen_by)
            continue
        dup = v2.duplicate_mechanism(h, accepted) if V2 else None
        if dup:
            R.fail("hypothesis", "conversion", "dropped", "duplicate.mechanism",
                   [f"its mechanism duplicates {dup} (same explained movement, near-identical cause and pathway)"], h, gen_by)
            continue
        sigs = frozenset(signature(i) for i in h["observable_implications"])
        if sigs in seen:
            R.fail("hypothesis", "conversion", "dropped", "duplicate.hypothesis",
                   ["its observable implications duplicate an earlier hypothesis in the same reply"], h, gen_by)
            continue
        seen.append(sigs)
        testable = {signature(i) for i in h["observable_implications"] if i["template"] != "other"}
        discriminating = {i: r for (ref, i), r in disc.items() if ref == h["ref"]}
        # v2: original if not all testable implications are in the library, OR it discriminates from a rival (a
        # mechanism may predict trend continuation and still say something its rival does not)
        original_common = not (testable and testable <= library)  # the definition shared by every arm
        novelty = "original" if (discriminating or original_common) else "restatement"
        payload = {"origin": "original", "novelty": novelty, "ref": h["ref"], "mechanism": h["mechanism"],
                   "conditions": h["conditions"], "assumptions": h["assumptions"],
                   "alternative_explanations": h["alternative_explanations"],
                   "observable_implications": h["observable_implications"],
                   "implications": [{"metric": f"{i['template']}:{i['entity']}" + (f":{i['entity_b']}" if i["entity_b"] else ""),
                                     "direction": i["comparator"]} for i in h["observable_implications"]],
                   "uncertainty": h["uncertainty"], "refutation_criteria": h["refutation_criteria"],
                   "novelty_claim": h["novelty_claim"], "model_request": h["model_request"]}
        if V2:  # (v1's 'novelty' IS the common definition; v1 payloads stay byte-identical)
            payload["novelty_common"] = "original" if original_common else "restatement"
            payload.update({"explains": h["explains"], "rivals": h["rivals"], "discriminating_implications": discriminating,
                            "rival_assessment": assessments.get(h["ref"], [])})
        hc = R.claim("hypothesis", h["statement"], gen_by, payload, evidence=h["supporting_evidence"], exists=exists,
                     provenance={**prov, "model": router.describe()["roles"]["hypothesis_generator"]["model"]})
        if not hc:
            continue
        h = {**h, "claim_id": hc, "novelty": novelty}
        accepted.append(h)
        R.add("hypotheses", art.item(hc, "hypothesis", "proposed", h["statement"], gen_by, R.as_of, payload,
                                     [("cites", x) for x in h["supporting_evidence"]], prov))
    if not accepted:
        return "completed", "no hypothesis passed validation", evidence, world
    if V2:
        _information_gaps(R, valid.get("information_gaps", []), accepted, allowed, prov, gen_by)

    # ---------------------------------------------------------------- 2. critique (one batched call)
    refs = [h["ref"] for h in accepted]
    if V2:  # the critic sees the evidence so it can check claims against it
        cschema = v2.critic_schema(refs, list(metrics))
        cres = call_role(router, store, "loop_critic", {"contract": v2.CONTRACT, "slice": {
            k: agent_slice[k] for k in ("market", "metrics", "metric_id_format", "templates")}, "hypotheses": [
            {k: h[k] for k in ("ref", "statement", "explains", "rivals", "mechanism", "assumptions", "supporting_evidence",
                               "observable_implications", "novelty")} for h in accepted]}, cschema, R.input_id,
            system=sysp["loop_critic"])
        cschema = v2.inline(cschema)
    else:
        cschema = sch.loop_critic_schema(refs)
        cres = call_role(router, store, "loop_critic", {"slice": {"market": slice_["market"]}, "hypotheses": [
            {k: h[k] for k in ("ref", "statement", "mechanism", "assumptions", "observable_implications", "novelty")}
            for h in accepted]}, cschema, R.input_id)
    R.calls.append(_call_summary("loop_critic", cres))
    if cres["data"] is not None:
        citems, crej, ctop = sch.split_items(cres["data"], cschema, "critiques")
        for _, it, pr in crej:
            R.fail("critique", "schema", "rejected", classify_problems(pr, it), pr, it, AGENT_IDS["loop_critic"])
        if ctop:
            R.fail("loop_critic", "schema", "rejected", "call.schema_top_level", ctop, cres["data"], AGENT_IDS["loop_critic"])
        by_ref = {h["ref"]: h for h in accepted}
        for c in citems:
            h = by_ref[c["hypothesis_ref"]]
            pr = v2.critique_problems(c) if V2 else []
            if pr:
                R.fail("critique", "validation", "rejected", "validation.critique_needs_evidence", pr, c, AGENT_IDS["loop_critic"])
                continue
            data = {"severity": c["severity"], "challenged_assumption": c["challenged_assumption"]}
            if V2:
                data.update({"dimension": c["dimension"], "evidence": c["evidence"]})
            cc = R.claim("critique", c["statement"], AGENT_IDS["loop_critic"], {"target": h["claim_id"], **data},
                         evidence=c.get("evidence", []), exists=exists, provenance=prov)
            if cc:
                R.link(cc, "critiques", h["claim_id"], AGENT_IDS["loop_critic"])
                R.add("critiques", art.item(cc, "critique", "proposed", c["statement"], AGENT_IDS["loop_critic"], R.as_of,
                                            data, [("critiques", h["claim_id"])] + [("cites", x) for x in c.get("evidence", [])],
                                            prov))
    else:
        R.fail("loop_critic", "call", "failed", cres["failure_class"] or "call.error", [cres["outcome"]], {}, AGENT_IDS["loop_critic"])

    # ---------------------------------------------------------------- 3. tests, model specs, forecasts
    tests_by_h = {}
    for h in accepted:
        tests_by_h[h["claim_id"]] = []
        for imp in h["observable_implications"]:
            spec, why = compiler.compile_implication(imp, panel, metrics)
            t = compiler.register_test(store, h["claim_id"], imp["id"], spec, why, R.input_id, R.as_of)
            tests_by_h[h["claim_id"]].append(t)
            R.add("tests", art.item(t["test_id"], "test", t["status"], f"{h['ref']}.{imp['id']}: " + (
                f"{spec['template']} {spec['entity']}{'-' + spec['entity_b'] if spec['entity_b'] else ''} {spec['comparator']} "
                f"{100 * spec['reference_value']:.2f}% over {', '.join(spec['target_periods'])}" if spec else f"not testable: {why}"),
                ENGINE, R.as_of, {"spec": spec, "reason": why, "spec_hash": t["spec_hash"], "implication": imp,
                                  **({"discriminates_from": disc.get((h["ref"], imp["id"]), [])} if V2 else {})},
                [("tests", h["claim_id"])], {"spec_version": compiler.TEST_SPEC_VERSION}))
            R.link(t["test_id"], "tests", h["claim_id"], ENGINE)
            if spec is not None and ctx["prospective"]:
                _future_test(R, ctx, panel, metrics, h, imp, spec, tests_by_h)
            if spec is None:
                continue
            fams = ["baseline.level", "baseline.momentum"]
            req = h["model_request"]
            if req["family"] != "none" and req["family"] not in fams:
                fams.append(req["family"])
            target = {"template": spec["template"], "entity": spec["entity"], "entity_b": spec["entity_b"]}
            for fam in fams:
                asm = req["numerical_assumptions"] if fam == req["family"] else []
                _model(R, panel, fam, target, asm, h, t, spec, prov)
    # ---------------------------------------------------------------- 4. strategic options (information at t only)
    baselines = {}
    hyp_view = [{"ref": h["ref"], "statement": h["statement"], "novelty": h["novelty"], "status": "proposed",
                 "entity": h["observable_implications"][0]["entity"], "assumptions": h["assumptions"],
                 "tests": [{"implication": t["implication_ref"], "status": t["status"]} for t in tests_by_h[h["claim_id"]]]}
                for h in accepted]
    for e in [market.focal] + ([market.target] if market.target else []):
        if e:
            tgt = {"template": objective_template(panel), "entity": e, "entity_b": None}
            baselines[(tgt["template"], e)] = official_baseline(R, panel, tgt, prov)
    bl_view = [{"template": k[0], "entity": k[1], **{x: v[x] for x in ("point", "interval80", "model", "status")}}
               for k, v in baselines.items()]
    if V2:
        sschema = v2.strategist_schema(refs, panel.entities, list(metrics), ALLOWED_LEVERS, MODES, ext)
        sres = call_role(router, store, "strategist", {"contract": v2.CONTRACT, "slice": agent_slice, "hypotheses": hyp_view,
                                                       "allowed_levers": ALLOWED_LEVERS, "baselines": bl_view}, sschema,
                         R.input_id, system=sysp["strategist"])
        sschema = v2.inline(sschema)
    else:
        sschema = sch.strategist_schema(refs, panel.entities, list(metrics), ALLOWED_LEVERS, MODES)
        sres = call_role(router, store, "strategist", {"slice": slice_, "hypotheses": hyp_view, "allowed_levers": ALLOWED_LEVERS,
                                                       "baselines": bl_view}, sschema, R.input_id)
    R.calls.append(_call_summary("strategist", sres))
    options = []
    if sres["data"] is not None:
        oitems, orej, otop = sch.split_items(sres["data"], sschema, "options")
        for _, it, pr in orej:
            R.fail("strategic_option", "schema", "rejected", classify_problems(pr, it), pr, it, AGENT_IDS["strategist"])
        if otop:
            R.fail("strategist", "schema", "rejected", "call.schema_top_level", otop, sres["data"], AGENT_IDS["strategist"])
        by_ref = {h["ref"]: h for h in accepted}
        for o in oitems:
            options.append(_option(R, ctx, panel, metrics, o, by_ref, allowed, baselines, prov))
        options = [o for o in options if o]
    else:
        R.fail("strategist", "call", "failed", sres["failure_class"] or "call.error", [sres["outcome"]], {}, AGENT_IDS["strategist"])
    simulated = [o for o in options if o["sims"]]  # an option without a simulation cannot be sized for an experiment
    if simulated:
        best = max(simulated, key=lambda o: strategy.min_effect_pp(o["sims"]))  # first of equals (stable order)
        proto = strategy.experiment_protocol(market, best["option"], best["criteria"][0], panel, best["sims"],
                                             objective_template(panel))
        aid = approvals.request(store, "experiment", proto["protocol_id"], ENGINE,
                                payload={"pre_registration_hash": proto["pre_registration_hash"], "option": best["claim_id"],
                                         "legal_review_required": True, "investigation": R.input_id,
                                         "eligible_for_execution": proto["eligibility"]["eligible_for_execution"],
                                         "eligibility_reasons": proto["eligibility"]["reasons"]})
        R.approvals_.append(approvals.get(store, aid))
        R.protocol = art.item(proto["protocol_id"], "experiment_protocol", "draft_not_executable",
                              ("EXPLORATORY, not eligible for execution: " if not proto["eligibility"]["eligible_for_execution"]
                               else "") + f"Draft experiment for {best['option']['title']}", ENGINE, R.as_of, proto,
                              [("tests", best["claim_id"])] + [("tests", h) for h in best["hyp_claims"]],
                              {"approval_request": aid, "spec_version": strategy.PROTOCOL_VERSION})
    # ---------------------------------------------------------------- 5. evaluation at a later date (optional)
    if ctx["t_eval"]:
        _evaluate(R, ctx, accepted, tests_by_h)
    else:
        for h in accepted:
            st = compiler.hypothesis_status(tests_by_h[h["claim_id"]], {})
            if st["status"] == "untested":
                R.ledger.set_status(h["claim_id"], "untested", ENGINE, effective_at=R.as_of, reason=st["reason"], commit=False)
                _set_item_status(R, h["claim_id"], "untested", st)
    _lessons(R, ctx, accepted, tests_by_h)
    return "completed", None, evidence, world


def objective_template(panel) -> str:
    return "segment_share" if panel.segment else "share"


def _semantic_checks(h: dict, allowed: set, earlier_refs: list) -> list[str]:
    p = []
    ids = [i["id"] for i in h["observable_implications"]]
    if len(set(ids)) != len(ids):
        p.append("observable implication ids must be unique")
    for a in h["alternative_explanations"]:
        if a["discriminating_implication"] not in ids:
            p.append(f"alternative explanation refers to implication {a['discriminating_implication']}, which does not exist")
    if h["ref"] in earlier_refs:
        p.append(f"hypothesis ref {h['ref']} used twice")
    if not h["refutation_criteria"].strip() or not h["mechanism"]["pathway"].strip():
        p.append("a hypothesis needs a mechanism pathway and refutation criteria")
    text = " ".join([h["statement"], h["mechanism"]["pathway"], h["refutation_criteria"]] +
                    [a["statement"] for a in h["alternative_explanations"]] + [a["statement"] for a in h["assumptions"]])
    p += check_numbers(text, allowed) + check_directions(text)
    return p


def _model(R: _Run, panel, fam, target, asm, h, t, spec, prov):
    spec_m = {"family": fam, "target": target, "assumptions": [{"name": a["name"], "value": a["value"]} for a in asm]}
    mv = reg.register(R.store, spec_m, panel, registered_by=ENGINE if not asm else AGENT_IDS["hypothesis_generator"],
                      run_id=R.input_id)
    if not any(x["id"] == mv["model_version_id"] for x in R.stages["model_specs"]):
        R.add("model_specs", art.item(mv["model_version_id"], "model_version", mv["status"],
                                      f"{fam} for {target['template']} {target['entity']}", ENGINE, R.as_of,
                                      {"family": fam, "target": target, "assumptions": spec_m["assumptions"],
                                       "validation": mv["validation"],
                                       "requested_by_hypothesis": h["claim_id"] if asm or fam == h["model_request"]["family"] else None},
                                      [("for_test", t["test_id"])], {"rule": reg.RULE}))
    for a in asm:  # the agent's numerical assumptions, recorded as assumption claims scoped to the model version
        ac = R.claim("assumption", f"{a['name']} = {a['value']} {a['unit']} ({a['basis']})", AGENT_IDS["hypothesis_generator"],
                     {"scope": mv["model_version_id"], "name": a["name"], "value": a["value"], "unit": a["unit"],
                      "basis": a["basis"], "accepted_by_registry": mv["status"] != "rejected"}, provenance=prov)
        if ac and not any(x["id"] == ac for x in R.stages["assumptions"]):
            R.add("assumptions", art.item(ac, "assumption", "proposed", f"{a['name']} = {a['value']} {a['unit']}",
                                          AGENT_IDS["hypothesis_generator"], R.as_of, a, [("scopes", mv["model_version_id"])], prov))
    if mv["status"] == "rejected":
        R.failures.append({"subject": "model_spec", "stage": "validation", "outcome": "rejected",
                           "failure_class": "model.spec_rejected", "problems": mv["validation"]["problems"][:5], "ref": fam})
        return
    try:
        out = reg.execute(mv["spec"], panel)
    except reg.ModelUnavailable as exc:
        R.failures.append({"subject": "model_spec", "stage": "execution", "outcome": "unavailable",
                           "failure_class": exc.failure_class, "problems": exc.problems[:5], "ref": fam})
        return
    quantity = {**target, "periods": spec["target_periods"], "market_id": panel.market_id, "panel_source": panel.source}
    payload = {"model": {"id": fam, "version": mv["model_version_id"]}, "point": out["point"], "interval80": out["interval80"],
               "sd": out["sd"], "resolution": {"quantity": quantity, "resolvable_after": spec["expected_resolvable_after"]},
               "inputs_hash": out["inputs_hash"], "output_hash": out["output_hash"]}
    label = f"{fam}: {target['template']} {target['entity']} {100 * out['point']:.2f}% (80% {100 * out['interval80'][0]:.2f}" \
            f"-{100 * out['interval80'][1]:.2f}%)"
    if mv["status"] == "validated":
        fc = R.claim("forecast", label, f"engine:model:{fam}", payload, provenance={**prov, "rule": reg.RULE})
        stage, ctype = "forecasts", "forecast"
    else:
        est = {"value": out["point"], "interval": out["interval80"], "sd": out["sd"], "method": fam, "candidate": False,
               "label": "unvalidated model output: NOT an official forecast", "model_version": mv["model_version_id"],
               "target": quantity}
        fc = R.claim("estimate", label + " [unvalidated]", f"engine:model:{fam}", est, provenance={**prov, "rule": reg.RULE})
        stage, ctype = "estimates", "estimate"
    if fc:
        R.link(fc, "tests", t["test_id"], ENGINE)
        existing = next((x for x in R.stages[stage] if x["id"] == fc), None)
        if existing:
            existing["links"].append({"relation": "informs", "to": t["test_id"]})
        else:
            R.add(stage, art.item(fc, ctype, "open" if ctype == "forecast" else "proposed", label, f"engine:model:{fam}", R.as_of,
                                  payload if ctype == "forecast" else est,
                                  [("from_model", mv["model_version_id"]), ("informs", t["test_id"])], {"rule": reg.RULE}))


def official_baseline(R: _Run, panel, target: dict, prov) -> dict:
    """Best validated forecast for a target (lowest backtest MAE), else persistence labelled unvalidated, else (no usable
    observations) status 'unavailable' with point None and a typed failure class. Never an invented value."""
    best = None
    for fam in reg.FORECAST_FAMILIES:
        mv = reg.register(R.store, {"family": fam, "target": target, "assumptions": []}, panel, registered_by=ENGINE,
                          run_id=R.input_id)
        if mv["status"] == "validated":
            mae = mv["validation"]["backtest"]["mae"]
            if best is None or mae < best[0]:
                best = (mae, fam, mv)
    try:
        if best:
            out = reg.execute(best[2]["spec"], panel)
            return {**out, "model": best[1], "model_version_id": best[2]["model_version_id"], "status": "validated"}
        out = reg.execute({"family": "baseline.level", "target": target}, panel)
        return {**out, "model": "baseline.level", "model_version_id": None, "status": "unvalidated"}
    except reg.ModelUnavailable as exc:
        return {"point": None, "sd": None, "interval80": None, "model": None, "model_version_id": None,
                "status": "unavailable", "failure_class": exc.failure_class, "problems": exc.problems}


def _option(R: _Run, ctx, panel, metrics, o, by_ref, allowed, baselines, prov):
    market = ctx["market"]
    by = AGENT_IDS["strategist"]

    def crit(sc):
        imp = {**sc, "template": sc["template"], "description": "success criterion", "would_refute_if": "criterion not met"}
        return compiler.compile_implication(imp, panel, metrics)
    V2 = ctx["contract"] == v2.CONTRACT
    # v2: the free-text number check is replaced by declared, exactly reproducible numeric claims (all text fields)
    pr, criteria = strategy.validate_option(o, set(by_ref), allowed, crit, check_text_numbers=not V2)
    if V2:
        pr += v2.validate_option(o, panel.entities, {k: m["value"] for k, m in metrics.items()})
    if pr:
        R.fail("strategic_option", "validation", "rejected", classify_problems(pr, None), pr, o, by)
        return None
    e = o["intended_effect"]
    tgt = {"template": e["template"], "entity": e["entity"], "entity_b": e.get("entity_b") or None}
    unsupported = strategy.effect_target_problems(e, panel)
    if unsupported:
        base = {"point": None, "interval80": None, "model": None, "model_version_id": None, "status": "unavailable",
                "failure_class": "option.effect_target_unsupported", "problems": unsupported}
    else:
        key = (tgt["template"], tgt["entity"]) + ((tgt["entity_b"],) if tgt["entity_b"] else ())
        if key not in baselines:
            baselines[key] = official_baseline(R, panel, tgt, prov)
        base = baselines[key]
    sims = []
    if base["status"] != "unavailable":
        try:
            sims = strategy.simulate(o, base, base["status"])
        except reg.ModelUnavailable as exc:
            base = {**base, "failure_class": exc.failure_class, "problems": exc.problems}
    simulation = {"status": "simulated" if sims else "not_simulated",
                  **({} if sims else {"failure_class": base.get("failure_class", "option.not_simulated"),
                                      "reason": "; ".join(base.get("problems", []))[:400]})}
    if not sims:  # the option is kept (it passed validation); it is not an agent-output rejection, so it is recorded in
        # the document and the event log, not in validation_records (whose stages are agent-output stages)
        cls = "option.effect_target_unsupported" if unsupported else "option.not_simulated"
        R.failures.append({"subject": "strategic_option", "stage": "simulation", "outcome": "not_simulated",
                           "failure_class": cls, "problems": [simulation["failure_class"] + ": " + simulation["reason"]],
                           "ref": o.get("ref")})
        R.events.append("option.not_simulated", "L7", {"ref": o.get("ref"), "failure_class": cls,
                                                       "reason": simulation["reason"]}, run_id=R.input_id, tenant=R.tenant)
    compliance = strategy.compliance_checklist(market, o, metrics)
    hyp_claims = [by_ref[r]["claim_id"] for r in o["linked_hypotheses"]]
    payload = {"ref": o["ref"], "mode": o["mode"], "levers": o["levers"], "intended_effect": e,
               "linked_hypotheses": hyp_claims, "supporting_evidence": o["supporting_evidence"], "assumptions": o["assumptions"],
               "competitor_responses": o["competitor_responses"],
               "cost": o["cost"] if o["cost"]["known"] else {"known": False, "basis": o["cost"]["basis"]},
               "risks": o["risks"], "uncertainty": o["uncertainty"], "success_criteria": criteria,
               "requires_approval": True, "compliance": compliance, "executable": False, "simulation": simulation}
    if V2:  # every number in the option's text, with its kind (observed / calculated / assumed) and source metrics
        payload["numeric_claims"] = o["numeric_claims"]
    oc = R.claim("strategic_proposal", o["title"], by, payload, evidence=o["supporting_evidence"],
                 exists=lambda x: x in metrics, provenance=prov)
    if not oc:
        return None
    links = [("relies_on", h) for h in hyp_claims]
    for h in hyp_claims:
        R.link(oc, "derived_from", h, by)
    for a in o["assumptions"]:
        ac = R.claim("assumption", a["statement"], by, {"scope": oc, "kind": a["kind"]}, provenance=prov)
        if ac:
            R.add("assumptions", art.item(ac, "assumption", "proposed", a["statement"], by, R.as_of, a, [("scopes", oc)], prov))
    for s in sims:
        sc = R.claim("simulated_outcome", f"{o['title']} | {s['scenario']}", "engine:model:simulation.share_shift",
                     {"scenario": {"option": oc, "name": s["scenario"], "competitor_response": s["competitor_response"]},
                      "assumptions": s["assumptions"], "conditional": True, "calibration": "uncalibrated",
                      "outputs": {k: s[k] for k in ("outcome_point", "outcome_range", "baseline_point", "effect_pp_assumed",
                                                    "competitor_offset_assumed")},
                      "baseline": {"model": base["model"], "model_version_id": base["model_version_id"], "status": base["status"]},
                      "target": tgt}, provenance=prov)
        if sc:
            R.link(sc, "derived_from", oc, ENGINE)
            links.append(("simulated_by", sc))
            R.add("simulations", art.item(sc, "simulated_outcome", "recorded", f"{o['ref']} {s['scenario']}: "
                                          f"{100 * s['outcome_range'][0]:.2f}-{100 * s['outcome_range'][1]:.2f}% "
                                          f"(baseline {100 * s['baseline_point']:.2f}%)", "engine:model:simulation.share_shift",
                                          R.as_of, s, [("simulates", oc)], {"calibration": "uncalibrated"}))
    R.add("strategic_options", art.item(oc, "strategic_proposal", "proposed", o["title"], by, R.as_of, payload, links, prov))
    return {"claim_id": oc, "option": o, "criteria": criteria, "sims": sims, "hyp_claims": hyp_claims}


def _future_test(R: _Run, ctx, panel, metrics, h, imp, spec, tests_by_h):
    """Prospective runs: label the nominal window's periods against the REAL registration time, and register the same
    implication on the first two genuinely FUTURE quarters (an engine-registered horizon extension, applied identically
    to every arm and labelled as such)."""
    reg = ctx["prospective"]["registered_at"]
    published = set(panel.periods)
    nominal = R.stages["tests"][-1]
    nominal["data"] = {**nominal["data"], "registered_at": reg, "horizon": "nominal",
                       "window_classes": compiler.window_classes(spec["target_periods"], reg, published)}
    fw = compiler.future_window(reg)
    fspec, why = compiler.compile_implication(imp, panel, metrics, target_periods=fw)
    if fspec is None:
        return
    t = compiler.register_test(R.store, h["claim_id"], f"{imp['id']}@future", fspec, why, R.input_id, R.as_of)
    tests_by_h[h["claim_id"]].append(t)
    R.add("tests", art.item(t["test_id"], "test", t["status"], f"{h['ref']}.{imp['id']}@future: {fspec['template']} "
                            f"{fspec['entity']} {fspec['comparator']} over {', '.join(fw)} (engine horizon extension)",
                            ENGINE, R.as_of, {"spec": fspec, "reason": None, "spec_hash": t["spec_hash"], "implication": imp,
                                              "registered_at": reg, "horizon": "future_window",
                                              "window_classes": compiler.window_classes(fw, reg, published),
                                              "note": "the agent's implication applied to the first quarters that start "
                                                      "after registration; identical for every arm"},
                            [("tests", h["claim_id"])], {"spec_version": compiler.TEST_SPEC_VERSION}))
    R.link(t["test_id"], "tests", h["claim_id"], ENGINE)


def _information_gaps(R: _Run, gaps, accepted, allowed, prov, by):
    """loop.v2: missing measurements that would distinguish hypotheses, recorded as open questions in learning memory
    for FUTURE research agents. Nothing is researched or fetched here; research needs a separate, approved capability."""
    refs = {h["ref"]: h["claim_id"] for h in accepted}
    for g in gaps:
        pr = v2.gap_problems(g, set(refs), allowed)
        if pr:
            R.fail("information_gap", "validation", "rejected", classify_problems(pr, None), pr, g, by)
            continue
        q = f"{g['id']}: {g['missing_measurement']} (would distinguish {', '.join(g['distinguishes'])})"
        rec = {**g, "distinguishes_claims": [refs[r] for r in g["distinguishes"]], "research_status": "not_requested",
               "autonomous_research": "disabled (needs a separately approved research capability)"}
        kid = R.memory.submit("question", q, by, {"tags": ["kind:information_gap", f"investigation:{R.input_id}"]}, [],
                              record=rec, available_at=R.as_of)
        R.add("information_gaps", art.item(kid, "information_gap", "open", q, by, R.as_of, rec,
                                           [("about", refs[r]) for r in g["distinguishes"]], prov))


def _set_item_status(R: _Run, cid, status, st):
    for it in R.stages["hypotheses"]:
        if it["id"] == cid:
            it["status"] = status
            it["data"] = {**it["data"], "status_detail": st}


def _evaluate(R: _Run, ctx, accepted, tests_by_h):
    market, t_eval = ctx["market"], ctx["t_eval"]
    eval_panel = build_panel(R.store, market, t_eval)
    results = {}
    for h in accepted:
        for t in tests_by_h[h["claim_id"]]:
            if t["status"] != "registered":
                continue
            r = compiler.resolve(R.store, t, eval_panel, t_eval)
            results[t["test_id"]] = r
            R.add("test_results", art.item(r["result_id"], "test_result", r["outcome"],
                                           f"{t['implication_ref']}: {r['outcome']}" + (
                                               f" (realized {100 * r['realized']:.2f}% vs {100 * r['reference_value']:.2f}%)"
                                               if r.get("realized") is not None else ""),
                                           ENGINE, t_eval, r, [("result_of", t["test_id"])],
                                           {"evaluated_as_of": t_eval, "available_at": r["available_at"]}))
        st = compiler.hypothesis_status(tests_by_h[h["claim_id"]], results)
        if st["status"] != "proposed":
            R.ledger.set_status(h["claim_id"], st["status"], ENGINE, effective_at=st["effective_at"] or R.as_of,
                                reason=st["reason"], commit=False)
        _set_item_status(R, h["claim_id"], st["status"], st)
        h["status_detail"] = st
    for stage in ("forecasts", "estimates"):
        for it in R.stages[stage]:
            q = (it["data"].get("resolution") or {}).get("quantity") or it["data"].get("target")
            v, _, avail = eval_panel.value(q["template"], q["entity"], q["periods"], q.get("entity_b"))
            if v is None:
                it["data"] = {**it["data"], "score": {"status": "pending"}}
                continue
            point = it["data"].get("point", it["data"].get("value"))
            lo, hi = it["data"].get("interval80", it["data"].get("interval"))
            it["data"] = {**it["data"], "score": {"status": "scored", "realized": v, "abs_error": abs(point - v),
                                                  "in_interval80": lo <= v <= hi, "available_at": avail}}
            if stage == "forecasts":
                R.ledger.set_status(it["id"], "scored", ENGINE, effective_at=avail, reason="outcome published", commit=False)
                it["status"] = "scored"


def _lessons(R: _Run, ctx, accepted, tests_by_h):
    market = ctx["market"]
    for h in accepted:
        st = h.get("status_detail") or compiler.hypothesis_status(tests_by_h[h["claim_id"]], {})
        tags = {"tags": [f"market:{market.id}", "domain:original_hypotheses", f"investigation:{R.input_id}"]}
        if st["status"] == "untested":
            reasons = [t["reason"] for t in tests_by_h[h["claim_id"]] if t["reason"]]
            q = f"{h['ref']} ({h['statement'][:120]}) is untested: {reasons[0] if reasons else 'no compilable implication'}"
            kid = R.memory.submit("question", q, ENGINE, tags, [], record={"hypothesis": h["claim_id"]}, available_at=R.as_of)
            R.add("lessons", art.item(kid, "question", "proposed", q, ENGINE, R.as_of, {"needs": reasons},
                                      [("about", h["claim_id"])], {"kind": "question"}))
            continue
        if st["status"] not in ("survived", "refuted", "inconclusive"):
            continue
        res = [r for r in R.stages["test_results"] if any(ln["to"] in [t["test_id"] for t in tests_by_h[h["claim_id"]]]
                                                         for ln in r["links"])]
        stmt = (f"{h['ref']} '{h['statement'][:140]}' was {st['status']} by its pre-registered tests "
                f"({st['reason']}; evidence {st['strength'] or 'n/a'}). "
                + ("Survival is consistent with the mechanism; it is not causal proof." if st["status"] == "survived" else ""))
        avail = st["effective_at"] or R.as_of
        kid = R.memory.submit("lesson", stmt, ENGINE, tags, [], record={"hypothesis": h["claim_id"],
                                                                         "results": [r["id"] for r in res]}, available_at=avail)
        lc = R.claim("lesson", stmt, ENGINE, {"evaluations": [r["id"] for r in res], "knowledge_id": kid,
                                              "hypothesis": h["claim_id"], "promotion": "not proposed (single investigation)"},
                     provenance={"market_id": market.id, "investigation": R.input_id}, available_at=avail)
        if lc:
            R.link(lc, "derived_from", h["claim_id"], ENGINE)
            R.add("lessons", art.item(lc, "lesson", "proposed", stmt, ENGINE, avail, {"knowledge_id": kid, "status_detail": st},
                                      [("derived_from", h["claim_id"])] + [("evaluated_by", r["id"]) for r in res],
                                      {"promotion": "requires the evaluation rule and founder approval"}))


def _completion(status: str, failures: list) -> dict:
    """ADR-020: a completed investigation is 'clean' only if nothing an agent produced was rejected or dropped; otherwise it
    is 'partial_rejection', with every rejected array and item listed. Rejected material is never passed to later agents."""
    agent_rejections = [f for f in failures if f["stage"] in ("schema", "validation", "conversion", "call")]
    arrays = [{"subject": f["subject"], "problems": f["problems"]} for f in failures if f["failure_class"] == "schema.array_limit"]
    if status != "completed":
        state = "not_completed"
    else:
        state = "partial_rejection" if agent_rejections else "clean"
    return {"state": state, "rejected_arrays": arrays, "rejected_or_dropped_items": len(agent_rejections),
            "unavailable_items": sum(1 for f in failures if f["stage"] in ("simulation", "execution")),
            "internal_error": any(f["failure_class"] == "internal.error" for f in failures),
            "downstream_rule": "rejected explanations and items are never passed to the critic or strategist"}


def _call_summary(role, res) -> dict:
    r = res.get("response")
    return {"role": role, "outcome": res["outcome"], "failure_class": res["failure_class"],
            "provider": getattr(r, "provider", None), "model": getattr(r, "model", None),
            "input_tokens": getattr(r, "input_tokens", 0), "output_tokens": getattr(r, "output_tokens", 0),
            "cached": bool(getattr(r, "cached", False)), "actual_cost_usd": getattr(r, "actual_cost_usd", None),
            "latency_seconds": getattr(r, "latency_seconds", 0.0), "prompt_chars": res.get("prompt_chars"),
            "schema_chars": res.get("schema_chars"), "max_output_tokens": res.get("max_output_tokens"),
            "stop_reason": res.get("stop_reason")}


def _document(R: _Run, ctx, status, reason, evidence, world, panel) -> dict:
    counts = {}
    for f in R.failures:
        counts[f["failure_class"]] = counts.get(f["failure_class"], 0) + 1
    doc = {"schema": art.SCHEMA_ID, "investigation_id": R.id, "input_id": R.input_id, "content_hash": "",
           "created_at": utc_now(), "tenant": R.tenant, "status": status, "stop_reason": reason, "objective": ctx["objective"],
           "labels": {"explanation": "interprets observed evidence; never a forecast",
                      "hypothesis": "an original falsifiable proposition; survival of tests is not causal proof",
                      "forecast": "official: produced by a validated, registered model version",
                      "estimate": "model output that is NOT an official forecast (unvalidated model)",
                      "simulated_outcome": "conditional on a scenario and its assumptions; uncalibrated; never scored",
                      "strategic_proposal": "a proposal for human review; not executable; needs approval and legal review",
                      "experiment_protocol": "a draft; Halibut never executes interventions"},
           "evidence": evidence, "world_model": world, "stages": R.stages, "experiment_protocol": R.protocol,
           "links": R.links, "failures": {"items": R.failures, "counts": counts, "completion": _completion(status, R.failures),
                                          "calls": [c for c in R.calls
                                                                                         if c["outcome"] != "ok"]},
           "agents": {"routing": ctx["router"].describe(), "calls": R.calls,
                      "costs": {"calls": len(R.calls), "input_tokens": sum(c["input_tokens"] or 0 for c in R.calls),
                                "output_tokens": sum(c["output_tokens"] or 0 for c in R.calls),
                                "actual_cost_usd": sum(c["actual_cost_usd"] or 0 for c in R.calls),
                                "cache_hits": sum(1 for c in R.calls if c["cached"])}},
           "approvals": [{k: a[k] for k in ("approval_id", "kind", "subject", "status", "requested_by")} for a in R.approvals_],
           "provenance": {"engine_version": ENGINE_VERSION, "prompt_version": ctx["pv"],
                          "schemas": ({k: sch.SCHEMA_VERSIONS[k] for k in ("hypothesis_generator", "loop_critic", "strategist")}
                                      if ctx["contract"] == v2.V1 else dict(v2.SCHEMA_VERSIONS)),
                          "registry_rule": reg.RULE, "test_spec_version": compiler.TEST_SPEC_VERSION,
                          "market_spec_sha256": ctx["spec_sha"], "panel": (panel.snapshot if panel else {}),
                          "agents": ctx["agents"]}}
    doc["content_hash"] = art.content_hash(doc)
    return doc
