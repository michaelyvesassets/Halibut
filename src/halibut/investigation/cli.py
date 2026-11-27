"""``halibut investigation`` and ``halibut approvals`` commands.

    halibut investigation run MARKET [--as-of D] [--evaluate-at D] [--agents mock|anthropic] [--faults]
                              [--approval ID --max-usd X] [--dry-run] [--out DIR]
    halibut investigation list | show ID [--json] | schema
    halibut approvals list [--status S] | show ID
    halibut approvals request KIND SUBJECT --by NAME [--max-usd X] [--note TEXT]
    halibut approvals decide ID granted|denied|revoked --by NAME [--legal-review REF] [--outcomes TEXT]
                              [--evaluation ID] [--note TEXT]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def add_investigation_commands(sub) -> None:
    p = sub.add_parser("investigation", help="R2 intelligence loop: objective to proposed lessons (offline with mock agents)")
    p.add_argument("--db", default="data/halibut.sqlite")
    s = p.add_subparsers(dest="inv_command", required=True)
    r = s.add_parser("run", help="run one investigation")
    r.add_argument("market")
    r.add_argument("--as-of")
    r.add_argument("--evaluate-at", help="later date at which pre-registered tests and forecasts are resolved")
    r.add_argument("--agents", choices=("mock", "reference", "anthropic"), default="mock")
    r.add_argument("--faults", action="store_true", help="mock agents: inject a fabricated evidence id")
    r.add_argument("--approval", help="granted paid_run approval id (required for --agents anthropic)")
    r.add_argument("--max-usd", type=float, help="hard spend cap for this investigation (paid agents)")
    r.add_argument("--dry-run", action="store_true", help="paid agents: print the worst-case cost and exit (no request)")
    r.add_argument("--out", default="results/investigations")
    r.add_argument("--contract", choices=("loop.v1", "loop.v2"), default="loop.v1",
                   help="reasoning contract (loop.v1: Step 1, frozen; loop.v2: Step 2)")
    r.add_argument("--measurements", choices=("base", "extended"), default="base",
                   help="loop.v2 only: 'extended' adds non_segment_share and units_growth")
    se = s.add_parser("secondary-eval", help="secondary evaluation r2_eval.v2.1 of stored experiment results (offline; "
                                             "frozen scores unchanged)")
    se.add_argument("experiment_dirs", nargs="+", help="results/<run>/<experiment id> directories")
    se.add_argument("--markets", default="configs/intel/markets")
    se.add_argument("--out", help="write the full readout as JSON")
    ca = s.add_parser("critic-audit-build", help="build a purified, UNAUDITED critic benchmark candidate for human audit")
    ca.add_argument("market")
    ca.add_argument("--as-of", required=True)
    ca.add_argument("--out", required=True)
    s2 = s.add_parser("step2", help="R2 Step 2 pre-registered benchmark (plan offline; --live is PAID and needs approval)")
    s2.add_argument("run", choices=("2a", "2b"))
    s2.add_argument("--live", action="store_true")
    s2.add_argument("--approval")
    s2.add_argument("--max-usd", type=float)
    s2.add_argument("--provider-config")
    s2.add_argument("--out", default="results/r2_step2")
    s2.add_argument("--authorized-2b", action="store_true", help="run 2b is separately authorized by the founder")
    v = s.add_parser("validate", help="R2 live validation: reference vs mock vs Claude on identical evidence")
    v.add_argument("market")
    v.add_argument("--origins", nargs="+", required=True)
    v.add_argument("--evaluate-at", required=True)
    v.add_argument("--live", action="store_true", help="PAID: smoke test + one Claude investigation per origin")
    v.add_argument("--approval", help="granted paid_run approval for r2_validation:<market id>")
    v.add_argument("--max-usd", type=float, help="hard cap for all live calls of the experiment (smoke included)")
    v.add_argument("--out", default="results/r2_validation")
    v.add_argument("--provider-config", help="live: provider config to use (e.g. a temporary approved copy outside the repo)")
    rv = s.add_parser("revalidate", help="R2 Step 1: mixed cached/live engineering revalidation of a past live run")
    rv.add_argument("market")
    rv.add_argument("--original-run", required=True, help="the original run directory (results/r2_validation/run-...)")
    rv.add_argument("--origins", nargs="+", required=True)
    rv.add_argument("--evaluate-at", required=True)
    rv.add_argument("--live", action="store_true", help="PAID: smoke + critic and strategist per origin (generator from cache)")
    rv.add_argument("--approval")
    rv.add_argument("--max-usd", type=float)
    rv.add_argument("--provider-config")
    rv.add_argument("--out", default="results/r2_validation")
    rv.add_argument("--reuse-from", nargs="*", default=[],
                    help="earlier revalidation experiment ids whose PAID critic/strategist replies may be reused when the "
                         "request is byte-identical (verified offline first and again at call time)")
    rv.add_argument("--live-origins", nargs="*", default=None,
                    help="only these origins may make live critic/strategist calls; others must be fully reused")
    s.add_parser("list", help="list stored investigations")
    sh = s.add_parser("show", help="summary of one investigation (or --json for the full result document)")
    sh.add_argument("investigation_id")
    sh.add_argument("--json", action="store_true")
    s.add_parser("schema", help="print the JSON Schema of the result document")
    p.set_defaults(handler=_inv)
    pr = sub.add_parser("predictions", help="unified prediction ledger and automatic outcome resolution (no API calls)")
    pr.add_argument("--db", default="data/halibut.sqlite")
    pr.add_argument("--markets", default="configs/intel/markets", help="directory of market specs (matched by sha256)")
    ps = pr.add_subparsers(dest="pred_command", required=True)
    sy = ps.add_parser("sync", help="register every prediction of every stored investigation (idempotent)")
    sy.add_argument("--include-scripted", action="store_true", help="also register mock/reference (not intelligence)")
    rs = ps.add_parser("resolve", help="resolve what first publication allows at the current time (idempotent)")
    rs.add_argument("--dry-run", action="store_true")
    rs.add_argument("--now", help="resolution time (default: now; never in the future)")
    ls_ = ps.add_parser("list", help="predictions with their current state")
    ls_.add_argument("--status")
    ls_.add_argument("--temporal", choices=("prospective", "retrospective"))
    ls_.add_argument("--json", action="store_true")
    sh_ = ps.add_parser("show", help="one prediction with its full resolution history")
    sh_.add_argument("prediction_id")
    ps.add_parser("summary", help="counts, effective sample sizes, hit rates against baselines, errors")
    ex_ = ps.add_parser("export", help="write the ledger and resolution history to registry/predictions/ (for git)")
    ex_.add_argument("--out", default="registry/predictions")
    pr.set_defaults(handler=_pred)
    a = sub.add_parser("approvals", help="approval records (control plane)")
    a.add_argument("--db", default="data/halibut.sqlite")
    asub = a.add_subparsers(dest="ap_command", required=True)
    al = asub.add_parser("list")
    al.add_argument("--status")
    ash = asub.add_parser("show")
    ash.add_argument("approval_id")
    rq = asub.add_parser("request")
    rq.add_argument("kind", choices=("paid_run", "experiment", "external_action", "promotion", "new_source"))
    rq.add_argument("subject")
    rq.add_argument("--by", required=True)
    rq.add_argument("--max-usd", type=float)
    rq.add_argument("--note", default="")
    dc = asub.add_parser("decide")
    dc.add_argument("approval_id")
    dc.add_argument("decision", choices=("granted", "denied", "revoked"))
    dc.add_argument("--by", required=True)
    dc.add_argument("--legal-review")
    dc.add_argument("--outcomes")
    dc.add_argument("--evaluation")
    dc.add_argument("--attestation", help="sole-operator self-approval: written statement of what is authorised and why")
    dc.add_argument("--note", default="")
    a.set_defaults(handler=_ap)


def _inv(args) -> int:
    from ..command import api  # noqa: PLC0415
    from ..control.approvals import AuthorizationError  # noqa: PLC0415
    from ..data.store import Store  # noqa: PLC0415
    from .loop import run_investigation  # noqa: PLC0415
    if args.inv_command == "schema":
        print(json.dumps(api.schema(), indent=1))
        return 0
    store = Store(args.db)
    try:
        if args.inv_command == "list":
            for r in api.list_investigations(store):
                print(f"{r['investigation_id']}  {r['market_id']}  as_of {r['as_of'][:10]}  eval {str(r['evaluate_as_of'])[:10]}  "
                      f"{r['status']}  {r['content_hash'][:12]}")
            return 0
        if args.inv_command == "show":
            doc = api.get_investigation(store, args.investigation_id)
            if doc is None:
                print("not found", file=sys.stderr)
                return 2
            print(json.dumps(doc, indent=1, default=str) if args.json else summary(doc))
            return 0
        if args.inv_command == "revalidate":
            from .revalidation import revalidation_markdown, run_revalidation  # noqa: PLC0415
            try:
                rep = run_revalidation(store, args.db, args.market, args.original_run, args.origins, args.evaluate_at,
                                       live=args.live, approval_id=args.approval, max_usd=args.max_usd, out_dir=args.out,
                                       provider_config=args.provider_config, reuse_from=args.reuse_from,
                                       live_origins=args.live_origins)
            except AuthorizationError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
            print(revalidation_markdown(rep))
            if rep.get("internal_errors"):
                print(f"\nINTERNAL ERROR: {rep['internal_errors']}\nresults: {rep['output_dir']}", file=sys.stderr)
                return 6
            print(f"\nresults: {rep['output_dir']}")
            if not rep["identity_preflight"]["identical"]:
                return 5
            return 0 if (not args.live or rep["verdict"]["pipeline_ready"]) else 4
        if args.inv_command == "secondary-eval":
            from .evaluation_v21 import evaluate_dirs  # noqa: PLC0415
            rep = evaluate_dirs(store, args.experiment_dirs, args.markets)
            if args.out:
                Path(args.out).write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
            for e in rep["evaluations"]:
                print(f"{e['file']}: useful_v21 {e['useful_v21']}; pairs "
                      f"{[(p['pair'], p['class']) for p in e['pairs']]}; contradicted "
                      f"{[k for k, v in e['hypothesis_quality'].items() if v['status'] == 'contradicted']}")
            print(json.dumps(rep["aggregate"], indent=1, default=str))
            return 0
        if args.inv_command == "critic-audit-build":
            import dataclasses  # noqa: PLC0415
            from ..intel.market import load_market  # noqa: PLC0415
            from ..world.context import build_slice  # noqa: PLC0415
            from ..world.panel import build_panel  # noqa: PLC0415
            from . import critic_benchmark as cb  # noqa: PLC0415
            m = dataclasses.replace(load_market(args.market), as_of=args.as_of)
            pnl = build_panel(store, m, args.as_of)
            _, met = build_slice(m, pnl, {"status": "covered", "contract": {}, "gaps": []}, [], extended=True)
            b = cb.build_v2(pnl, met)
            Path(args.out).write_text(json.dumps(b, indent=1, sort_keys=True) + "\n")
            print(f"{args.out}: {len(b['key'])} items, audit status {b['audit']['status']} (needs independent human audit)")
            return 0
        if args.inv_command == "step2":
            from .step2 import CRITERIA_HASH, run_step2  # noqa: PLC0415
            try:
                rep = run_step2(store, args.db, args.run, live=args.live, approval_id=args.approval, max_usd=args.max_usd,
                                out_dir=args.out, provider_config=args.provider_config, authorized_2b=args.authorized_2b)
            except AuthorizationError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
            print(json.dumps({"experiment_id": rep["experiment_id"], "criteria_hash": CRITERIA_HASH, "plan": {
                k: rep["plan"][k] for k in ("live_calls", "worst_case_usd", "worst_case_with_retries_usd", "cap_usd")},
                "criteria_results": rep["criteria_results"], "summary": rep["summary"]["arms"]}, indent=1, default=str))
            print(f"results: {rep['output_dir']}")
            if rep.get("internal_errors"):
                return 6
            return 0 if (not args.live or all(v is not False for v in rep["criteria_results"].values()
                                                 if v is not None)) else 4
        if args.inv_command == "validate":
            from .live_validation import markdown, run_validation  # noqa: PLC0415
            try:
                rep = run_validation(store, args.market, args.origins, args.evaluate_at, live=args.live,
                                     approval_id=args.approval, max_usd=args.max_usd, out_dir=args.out,
                                     provider_config=args.provider_config)
            except AuthorizationError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
            print(markdown(rep))
            print(f"\nresults: {rep['output_dir']}")
            if not args.live:
                pl = rep["plan"]
                print(f"\nLIVE PLAN: {pl['calls']} calls (max {pl['max_attempts']} attempts); worst case ${pl['worst_case_usd']:.4f}, "
                      f"with retries ${pl['worst_case_with_retries_usd']:.4f}; cap {pl['cap_usd']}; "
                      f"fits without retries: {pl['fits_cap_without_retries']}, with retries: {pl['fits_cap_with_retries']}")
            return 0 if (not args.live or rep["verdict"]["pipeline_ready"]) else 4
        if args.agents == "anthropic" and args.dry_run:
            print(plan_cost(args.max_usd))
            return 0
        try:
            doc = run_investigation(store, args.market, as_of=args.as_of, evaluate_as_of=args.evaluate_at, agents=args.agents,
                                    approval_id=args.approval, max_usd=args.max_usd, faults=args.faults,
                                    contract=args.contract, measurements=args.measurements, log=lambda *_: None)
        except AuthorizationError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 3
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{doc['investigation_id'].replace(':', '_')}.json"
        path.write_text(json.dumps(doc, indent=1, default=str) + "\n")
        print(summary(doc))
        print(f"result document: {path}")
        return 0
    finally:
        store.close()


def plan_cost(max_usd) -> str:
    from ..agents.anthropic_provider import pricing_from_config  # noqa: PLC0415
    from ..agents.llm import load_llm_config  # noqa: PLC0415
    from ..agents.routing import load_routing  # noqa: PLC0415
    rt = load_routing()
    root = Path(__file__).resolve().parents[3]
    lines, total = [], 0.0
    # prompt sizes: system + slice (<= 24k chars budget) + schema; critic/strategist add the hypotheses (<= 12k chars)
    sizes = {"hypothesis_generator": 2000 + 24000 + 8000, "loop_critic": 1500 + 4000 + 12000 + 2000,
             "strategist": 2000 + 24000 + 12000 + 9000}
    for role, spec in rt["roles"].items():
        cfg = load_llm_config(root / rt["provider_config"], model=spec["model"])
        w = pricing_from_config(cfg).worst_case("x" * sizes[role], spec["max_output_tokens"])
        total += w
        lines.append(f"  {role:<22} {spec['model']}  max_out {spec['max_output_tokens']}  worst case ${w:.4f}")
    retry = total * 2
    return ("plan (one investigation, 3 calls, no request sent):\n" + "\n".join(lines) +
            f"\n  worst case ${total:.4f}; with one retry per call ${retry:.4f}" +
            (f"; cap ${max_usd}" if max_usd else "; no cap given (paid runs require --max-usd and a granted approval)"))


def summary(doc: dict) -> str:
    st = doc["stages"]
    hy = st["hypotheses"]
    comp = (doc["failures"].get("completion") or {}).get("state")
    lines = [f"{doc['investigation_id']}  {doc['objective']['market_id']}  status {doc['status']}"
             + (f" [{comp}]" if comp else "")
             + (f" ({doc['stop_reason']})" if doc["stop_reason"] else ""),
             f"  as of {doc['objective']['as_of'][:10]}; evaluated {str(doc['objective']['evaluate_as_of'])[:10]}; "
             f"content {doc['content_hash'][:16]}",
             f"  evidence: {len(doc['evidence']['metrics'])} metrics from {doc['evidence']['panel'].get('source')} "
             f"({doc['evidence']['panel'].get('first_period')} to {doc['evidence']['panel'].get('last_period')})",
             f"  explanations {len(st['explanations'])}; hypotheses {len(hy)} "
             f"(original {sum(1 for h in hy if h['data'].get('novelty') == 'original')}, "
             f"restatements {sum(1 for h in hy if h['data'].get('novelty') == 'restatement')}); critiques {len(st['critiques'])}"]
    for h in hy:
        lines.append(f"    [{h['status']}] {h['data']['ref']} ({h['data']['novelty']}): {h['label'][:110]}")
    lines += [f"  tests {len(st['tests'])} (not testable {sum(1 for t in st['tests'] if t['status'] == 'not_testable')}); "
              f"results {', '.join(f'{k} {v}' for k, v in _count(st['test_results']).items()) or 'none'}",
              f"  model versions {len(st['model_specs'])} ({', '.join(f'{k} {v}' for k, v in _count(st['model_specs']).items())}); "
              f"official forecasts {len(st['forecasts'])}; unvalidated estimates {len(st['estimates'])}",
              f"  strategic options {len(st['strategic_options'])}; simulations {len(st['simulations'])}; "
              f"experiment protocol {'draft' if doc['experiment_protocol'] else 'none'}; lessons/questions {len(st['lessons'])}",
              f"  failures: {', '.join(f'{k} {v}' for k, v in doc['failures']['counts'].items()) or 'none'}",
              f"  LLM calls {doc['agents']['costs']['calls']} (cache hits {doc['agents']['costs']['cache_hits']}), "
              f"cost ${doc['agents']['costs']['actual_cost_usd']:.6f}"]
    return "\n".join(lines)


def _count(items) -> dict:
    out = {}
    for it in items:
        out[it["status"]] = out.get(it["status"], 0) + 1
    return out


def _pred(args) -> int:
    from ..data.store import Store, utc_now  # noqa: PLC0415
    from ..epistemic import predictions as P  # noqa: PLC0415
    store = Store(args.db)
    try:
        if args.pred_command == "sync":
            print(json.dumps(P.sync(store, args.markets, include_scripted=args.include_scripted)))
        elif args.pred_command == "resolve":
            if args.now and args.now > utc_now():
                print("refused: --now may not be in the future (no unpublished evidence)", file=sys.stderr)
                return 2
            print(json.dumps(P.resolve(store, args.markets, now=args.now, dry_run=args.dry_run)))
        elif args.pred_command == "list":
            rows = [x for x in P.current(store) if (not args.status or x["status"] == args.status)
                    and (not args.temporal or x["temporal_class"] == args.temporal)]
            if args.json:
                print(json.dumps(rows, indent=1, default=str))
            else:
                for x in rows:
                    q = x["question"]
                    what = (f"{q['template']} {q['entity']}{'-' + q['entity_b'] if q.get('entity_b') else ''} "
                            + (f"{q['comparator']} {100 * q['reference_value']:.2f}%" if x["kind"] == "binary"
                               else f"= {100 * q['point']:.2f}%"))
                    print(f"{x['prediction_id']}  {x['temporal_class'][:5]}  {x['horizon']:<13} {x['status']:<11} "
                          f"{(x['outcome'] or ''):<6} {what} over {','.join(q['target_periods'])}  [{x['provenance'].get('prompt_version')}]")
        elif args.pred_command == "show":
            r = store.conn.execute("SELECT * FROM predictions WHERE prediction_id=?", (args.prediction_id,)).fetchone()
            if r is None:
                print("not found", file=sys.stderr)
                return 2
            hist = [dict(x) for x in store.conn.execute("SELECT * FROM prediction_resolutions WHERE prediction_id=? "
                                                        "ORDER BY resolved_at, resolution_id", (args.prediction_id,))]
            print(json.dumps({"prediction": dict(r), "history": hist}, indent=1, default=str))
        elif args.pred_command == "export":
            print(json.dumps(P.export(store, args.out)))
        else:
            print(json.dumps(P.summary(store), indent=1, default=str))
        return 0
    finally:
        store.close()


def _ap(args) -> int:
    from ..control import approvals as ap  # noqa: PLC0415
    from ..data.store import Store  # noqa: PLC0415
    store = Store(args.db)
    try:
        if args.ap_command == "list":
            for a in ap.list_all(store, args.status):
                print(f"{a['approval_id']}  {a['kind']:<15} {a['status']:<9} {a['subject']}  by {a['requested_by']}"
                      + (f"  max ${a['max_usd']}" if a["max_usd"] else ""))
            return 0
        if args.ap_command == "show":
            print(json.dumps(ap.get(store, args.approval_id), indent=1, default=str))
            return 0
        if args.ap_command == "request":
            print(ap.request(store, args.kind, args.subject, args.by, max_usd=args.max_usd, payload={"note": args.note}))
            return 0
        ev = {k: v for k, v in (("legal_review_ref", args.legal_review), ("measurable_outcomes", args.outcomes),
                                ("evaluation_id", args.evaluation), ("attestation", args.attestation)) if v}
        a = ap.decide(store, args.approval_id, args.decision, args.by, note=args.note, evidence=ev)
        print(f"{a['approval_id']}: {a['status']} by {a['decided_by']}"
              + (" (self-approved under sole-operator policy "
                 f"{a['decision_evidence']['policy_version']})" if (a["decision_evidence"] or {}).get("self_approved_under_policy") else ""))
        return 0
    except (ap.AuthorizationError, KeyError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    finally:
        store.close()
