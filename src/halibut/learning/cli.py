"""``halibut learn`` commands.

    halibut learn demo [--pilot SPEC]           # reproducible learning loop on stored real data
    halibut learn lessons [--tag T] [--status S]
    halibut learn import-backtest RESULT_DIR    # record an existing backtest as experience + outcome
    halibut learn promote COMPONENT EVALUATION_ID --approved-by NAME
    halibut learn rollback COMPONENT --approved-by NAME
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..config import ConfigError


def add_learn_commands(sub) -> None:
    p = sub.add_parser("learn", help="learning memory: experiments, lessons, evaluations, promotions")
    p.add_argument("--db", default="data/halibut.sqlite")
    ls = p.add_subparsers(dest="learn_command", required=True)
    d = ls.add_parser("demo", help="baseline -> controlled change -> evaluation -> lesson -> informed next experiment")
    d.add_argument("--pilot", default="configs/empirical/norway_bev_v1.toml")
    d.add_argument("--out", default="results/learning")
    le = ls.add_parser("lessons", help="retrieve knowledge (lessons, outcomes, hypotheses) with links")
    le.add_argument("--tag", action="append")
    le.add_argument("--status", action="append")
    le.add_argument("--kind", action="append")
    ib = ls.add_parser("import-backtest", help="record a completed backtest as an experiment and outcome")
    ib.add_argument("result_dir")
    sc = ls.add_parser("score", help="score open predictions with data published by --as-of (default: now)")
    sc.add_argument("--as-of")
    sc.add_argument("--tag", action="append")
    pr = ls.add_parser("promote", help="activate an eligible candidate (explicit approval required)")
    pr.add_argument("component")
    pr.add_argument("evaluation_id")
    pr.add_argument("--approved-by", required=True)
    rb = ls.add_parser("rollback", help="revert a component to its previous promotion")
    rb.add_argument("component")
    rb.add_argument("--approved-by", required=True)
    p.set_defaults(handler=_handle)


def _handle(args) -> int:
    from ..data.store import Store  # noqa: PLC0415
    from .memory import LearningMemory, TrustViolation  # noqa: PLC0415
    store = Store(args.db)
    mem = LearningMemory(store)
    try:
        if args.learn_command == "demo":
            return _demo(store, mem, args)
        if args.learn_command == "lessons":
            for k in mem.retrieve(tags=args.tag, statuses=args.status, kinds=args.kind):
                print(f"[{k['status']:<12}] {k['kind']:<10} {k['knowledge_id']}  by {k['produced_by']}\n    {k['statement']}")
                for ln in k["links"]:
                    print(f"      {ln['from_id']} --{ln['relation']}--> {ln['to_id']}  ({ln['note'] or ''})")
            return 0
        if args.learn_command == "score":
            from ..data.store import utc_now  # noqa: PLC0415
            from .predictions import score_due  # noqa: PLC0415
            res = score_due(store, mem, args.as_of or utc_now(), tags=args.tag)
            failed = sum(r["failed"] for r in res)
            print(f"scored {len(res)} predictions ({failed} failed direction or interval) as of {args.as_of or 'now'}")
            for r in res[:20]:
                print(f"  {r['prediction']}: point {100 * r['point']:.2f}% realized {100 * r['realized']:.2f}% "
                      f"{'FAILED' if r['failed'] else 'ok'} {r['explanations']}")
            return 0
        if args.learn_command == "import-backtest":
            return _import_backtest(store, mem, Path(args.result_dir))
        if args.learn_command == "promote":
            pid = mem.promote(args.component, args.evaluation_id, args.approved_by)
            print(f"promotion {pid}: {args.component} -> evaluation {args.evaluation_id} (approved by {args.approved_by})")
            return 0
        if args.learn_command == "rollback":
            now = mem.rollback(args.component, args.approved_by)
            print(f"rolled back {args.component}; active now: {now}")
            return 0
    except (TrustViolation, ConfigError, KeyError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    finally:
        store.close()
    return 0


def _demo(store, mem, args) -> int:
    from ..empirical.spec import load_pilot  # noqa: PLC0415
    from .demo import run_demo  # noqa: PLC0415
    spec = load_pilot(args.pilot)
    r = run_demo(store, spec, mem)
    for step in ("first", "second"):
        x, v = r[step], r[step]["verdict"]
        print(f"{step}: candidate {x['candidate_id']} vs baseline {x['baseline_id']}")
        print(f"   development MAE (pp)  baseline {_fmt(x['baseline_dev'])}")
        print(f"                         candidate {_fmt(x['candidate_dev'])}")
        print(f"   held-out MAE (pp)     baseline {_fmt(x['baseline_holdout'])}  candidate {_fmt(x['candidate_holdout'])}  naive {_fmt(x['naive_holdout'])}")
        print(f"   => result={v['result']} decision={v['decision']} lesson status={x['lesson_status']}")
        print(f"      reasons: {'; '.join(v['reasons'])}")
        if step == "first":
            p = r["proposal"]
            print(f"proposal by reference agent after retrieving {len(p['considered'])} lesson(s): {p['action']} "
                  f"(window={p['window']}, target={p['product']})\n   rationale: {p['rationale']}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"learning_demo_{r['first']['candidate_id'].split(':')[1]}.json"
    path.write_text(json.dumps(r, indent=1, default=float) + "\n")
    print(f"record: {path}")
    return 0


def _fmt(d: dict) -> str:
    return " ".join(f"{k[:4]}={v:.2f}" for k, v in d.items())


def _import_backtest(store, mem, path: Path) -> int:
    from .lineage import lineage  # noqa: PLC0415
    s = json.loads((path / "summary.json").read_text())
    eid = mem.record_experiment("empirical_backtest", f"{s['pilot']} rolling-origin backtest", "engine:empirical_backtest",
                                lineage(store, extra={"spec_hash": s["spec_hash"], "engine_version_at_run": s["engine_version"],
                                                      "origins": [o["origin"] for o in s["origins"]]}))
    mem.finish_experiment(eid, {"summary": {m: v["all"] for m, v in s["summary"].items()}})
    better = s["baselines_better_than_halibut"]
    hal = s["summary"]["halibut"]["all"]["mae_pp"]
    statement = (f"In {s['pilot']} ({len(s['origins'])} origins), simple baselines {better} had lower MAE than Halibut "
                 f"({hal:.2f} pp). One study: tested, not a general validated lesson.")
    kid = mem.submit("outcome", statement, "engine:empirical_backtest",
                     {"tags": ["domain:share_forecasting", "component:simulation.agent_model", f"pilot:{s['pilot']}"]},
                     [f"experiment:{eid}", f"file:{path / 'summary.json'}"], experiment_id=eid, status="tested",
                     record={"ranking": s["ranking_by_mae"]})
    q = mem.submit("question", "Does the agent model's deficit vs diffusion curves persist after calibration converges "
                   "and with alternative social-exposure signals?", "engine:empirical_backtest",
                   {"tags": ["domain:share_forecasting", "component:simulation.agent_model"]}, [kid], experiment_id=eid)
    mem.link(q, "derived_from", kid, "engine:empirical_backtest")
    print(f"recorded experiment {eid}\n  outcome {kid}: {statement}\n  open question {q}")
    return 0
