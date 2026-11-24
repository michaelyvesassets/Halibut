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
    s.add_parser("list", help="list stored investigations")
    sh = s.add_parser("show", help="summary of one investigation (or --json for the full result document)")
    sh.add_argument("investigation_id")
    sh.add_argument("--json", action="store_true")
    s.add_parser("schema", help="print the JSON Schema of the result document")
    p.set_defaults(handler=_inv)
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
                                       provider_config=args.provider_config)
            except AuthorizationError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
            print(revalidation_markdown(rep))
            print(f"\nresults: {rep['output_dir']}")
            if not rep["identity_preflight"]["identical"]:
                return 5
            return 0 if (not args.live or rep["verdict"]["pipeline_ready"]) else 4
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
                                    approval_id=args.approval, max_usd=args.max_usd, faults=args.faults, log=lambda *_: None)
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
