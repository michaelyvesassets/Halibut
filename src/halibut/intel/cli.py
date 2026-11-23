"""``halibut intel`` commands.

    halibut intel smoke                       # one small live call per provider (connectivity report)
    halibut intel investigate MARKET.toml     # end-to-end investigation of a competitive market
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from ..config import ConfigError

DEFAULT_DB = "data/halibut.sqlite"


def add_intel_commands(sub) -> None:
    p = sub.add_parser("intel", help="market intelligence: providers, competitor graph, hypotheses")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--raw", default="data/raw")
    isub = p.add_subparsers(dest="intel_command", required=True)
    s = isub.add_parser("smoke", help="live connectivity check: one small request per provider")
    s.add_argument("--out", default="results/intel")
    inv = isub.add_parser("investigate", help="investigate a competitive market (network unless --offline)")
    inv.add_argument("market")
    inv.add_argument("--out", default="results/intel")
    inv.add_argument("--offline", action="store_true", help="use only evidence already in the store")
    inv.add_argument("--yes", action="store_true", help="proceed even if the estimate exceeds the market's limits")
    inv.add_argument("--dry-run", action="store_true", help="print the plan and budget, then exit")
    rp = isub.add_parser("replay", help="historical replay: full learning cycle with publication-date cutoffs (offline)")
    rp.add_argument("market")
    rp.add_argument("--agents", choices=("reference", "mock", "qwen", "anthropic"), default="reference")
    rp.add_argument("--model", help="Anthropic model id priced in configs/llm/anthropic.toml (default: its llm.model)")
    rp.add_argument("--faults", action="store_true", help="mock agents: inject citation/number faults")
    rp.add_argument("--origin", action="append", help="origin date (repeatable; default: 10 half-yearly origins)")
    rp.add_argument("--out", default="results/intel/replays")
    bm = isub.add_parser("benchmark", help="LLM agents vs fixed-rule reference on identical cutoffs and evidence")
    bm.add_argument("market")
    bm.add_argument("--agents", choices=("anthropic", "qwen", "mock"), default="anthropic")
    bm.add_argument("--model", help="Anthropic model id priced in configs/llm/anthropic.toml (default: its llm.model)")
    bm.add_argument("--origin", action="append")
    bm.add_argument("--dry-run", action="store_true", help="measure prompts and worst-case cost on a throwaway copy; no network")
    bm.add_argument("--reconstruct", metavar="REPLAY_ID",
                    help="OFFLINE: re-run a past LLM replay from cached and recorded replies on a throwaway copy and "
                         "classify every failure (no network, no spend)")
    bm.add_argument("--max-usd", type=float, help="hard per-run cap for this benchmark (tighter than the config's)")
    bm.add_argument("--yes", action="store_true", help="required for a PAID run (qwen/anthropic), after reading the plan")
    bm.add_argument("--out", default="results/intel/benchmarks")
    cv = isub.add_parser("coverage", help="market coverage contract check and as-of evidence coverage (offline)")
    cv.add_argument("market")
    cv.add_argument("--as-of", help="date for the coverage report (default: the market's as_of)")
    cp = isub.add_parser("compare", help="compare replay summaries (quality, runtime, cost)")
    cp.add_argument("summaries", nargs="+")
    p.set_defaults(handler=_handle)


def _agent_kind(args) -> str:
    """'anthropic' + --model -> 'anthropic:<model>' (the model is part of the replay identity)."""
    model = getattr(args, "model", None)
    if model and args.agents != "anthropic":
        raise SystemExit("--model applies only to --agents anthropic")
    return f"anthropic:{model}" if model else args.agents


def _handle(args) -> int:
    from ..data.store import Store  # noqa: PLC0415
    from ..data.providers.base import register  # noqa: PLC0415
    try:
        store = Store(args.db)
        register(store)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.intel_command == "smoke":
            return smoke(store, args)
        if args.intel_command == "replay":
            return _replay(store, args)
        if args.intel_command == "compare":
            return _compare(args)
        if args.intel_command == "benchmark":
            return _benchmark(store, args)
        if args.intel_command == "coverage":
            return _coverage(store, args)
        from .workflow import investigate_cli  # noqa: PLC0415
        return investigate_cli(store, args)
    finally:
        store.close()


def smoke(store, args) -> int:
    from ..data.http import Budget, PoliteClient  # noqa: PLC0415
    from ..data.providers import dft, gdelt, worldbank  # noqa: PLC0415
    from ..data.store import utc_now  # noqa: PLC0415
    results = {}
    raw = Path(args.raw)

    def run(name, fn):
        t0 = time.monotonic()
        try:
            detail = fn()
            results[name] = {"status": "ok", "seconds": round(time.monotonic() - t0, 1), "detail": detail}
        except Exception as exc:  # report, never hide
            results[name] = {"status": "failed", "seconds": round(time.monotonic() - t0, 1), "error": str(exc)[:300]}
        print(f"  {name:<10} {results[name]['status']:<7} {results[name]['seconds']:>6}s  "
              f"{json.dumps(results[name].get('detail', results[name].get('error')))[:150]}")

    mk = lambda prov, interval, **b: PoliteClient(prov, Budget(**b), store, min_interval=interval, retries=1)  # noqa: E731
    run("worldbank", lambda: worldbank.fetch_indicators(store, mk("worldbank", 1, max_requests=3, max_bytes=200_000), raw,
                                                         "GB", ["NY.GDP.PCAP.CD"], (2020, 2025))["indicators"])

    def wd():
        c = mk("wikidata", 2, max_requests=3, max_bytes=200_000)
        r = c.get("https://www.wikidata.org/w/api.php", params={"action": "wbsearchentities", "search": "Tesla",
                                                                "language": "en", "type": "item", "limit": 3, "format": "json"})
        return [(x["id"], x.get("description")) for x in json.loads(r.body)["search"]]
    run("wikidata", wd)

    def osm():
        c = mk("osm", 10, max_requests=3, max_bytes=500_000)
        status = c.get("https://overpass-api.de/api/status").body.decode()[:200]
        q = '[out:json][timeout:25];node["amenity"="cafe"](51.505,-0.13,51.515,-0.11);out count;'
        import urllib.parse  # noqa: PLC0415
        r = c.post("https://overpass-api.de/api/interpreter", data=("data=" + urllib.parse.quote(q)).encode(),
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        d = json.loads(r.body)
        return {"status_line": status.splitlines()[3] if len(status.splitlines()) > 3 else status,
                "count": d["elements"][0]["tags"]["total"], "osm_timestamp": d["osm3s"]["timestamp_osm_base"]}
    run("overpass", osm)

    def gd():
        c = mk("gdelt", 6, max_requests=2, max_bytes=8_000_000)
        out = gdelt.fetch_sample(store, c, raw, ["20261102120000"], {}, (), max_files=1)
        return {"bytes": out["bytes"], "records": out["files"][0]["records"]}
    run("gdelt", gd)

    def df():
        url, updated = dft.locate(mk("dft", 1, max_requests=2, max_bytes=200_000))
        return {"file": url.rsplit("/", 1)[-1], "publisher_updated": updated}
    run("dft", df)

    def rdw():
        import socket  # noqa: PLC0415
        try:
            return {"resolved": socket.gethostbyname("opendata.rdw.nl")}
        except OSError as exc:
            raise RuntimeError(f"opendata.rdw.nl does not resolve: {exc}") from exc
    run("rdw", rdw)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"smoke_{utc_now()[:10]}.json"
    path.write_text(json.dumps({"checked_at": utc_now(), "results": results}, indent=2) + "\n")
    print(f"report: {path}")
    return 0 if all(r["status"] == "ok" for k, r in results.items() if k != "rdw") else 1


def _replay(store, args) -> int:
    from .replay import replay  # noqa: PLC0415
    kind = _agent_kind(args)
    print(f"replay {args.market} with {kind} agents (offline; paid calls only with an approved qwen/anthropic config)")
    r = replay(store, args.market, args.origin, kind, args.faults)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{r['replay_id']}_{kind.replace(':', '_')}.json"
    path.write_text(json.dumps(r, indent=1, default=str) + "\n")
    _print_summary(r)
    for e in r["evaluations"]:
        print(f"  lesson [{e.get('lesson_status')}] {e['comparison']}: {e.get('baseline_mean_pp', 0):.3f} -> "
              f"{e.get('candidate_mean_pp', 0):.3f} pp ({e.get('result')}, {e.get('promotion')})")
    print(f"summary: {path}")
    return 0


def _print_summary(r: dict) -> None:
    llm = r["llm"]
    print(f"  {r['agents']}: {r['hypotheses_valid']} valid / {r['hypotheses_rejected_by_validation']} rejected hypotheses; "
          f"{r['hypothesis_predictions_scored']} scored: {r['supported']} supported, {r['refuted']} refuted; "
          f"direction hit {r['direction_hit_rate']:.0%}, MAE {r['hypothesis_mae_pp']:.2f} pp, 80% coverage "
          f"{r['hypothesis_coverage80']:.0%}; {r['seconds']}s; LLM calls {llm['calls']}, tokens {llm['input_tokens']}+"
          f"{llm['output_tokens']}, est. cost ${llm['estimated_cost_usd'] or 0:.4f}, duplicates caught {llm['duplicate_tool_calls']}")


def _compare(args) -> int:
    rows = [json.loads(Path(p).read_text()) for p in args.summaries]
    keys = [("agents", "agent set"), ("hypotheses_valid", "valid hypotheses"),
            ("hypotheses_rejected_by_validation", "rejected by validation"), ("supported", "supported by outcomes"),
            ("refuted", "refuted by outcomes"), ("direction_hit_rate", "direction hit rate"),
            ("hypothesis_mae_pp", "MAE (pp)"), ("hypothesis_coverage80", "80% coverage"), ("seconds", "runtime (s)")]
    print("| metric | " + " | ".join(r["replay_id"] for r in rows) + " |")
    print("|---|" + "---|" * len(rows))
    for k, label in keys:
        print(f"| {label} | " + " | ".join(f"{r[k]:.3f}" if isinstance(r[k], float) else str(r[k]) for r in rows) + " |")
    for k in ("calls", "input_tokens", "output_tokens", "estimated_cost_usd", "cache_hit_rate", "duplicate_tool_calls"):
        print(f"| llm {k} | " + " | ".join(str(r["llm"][k]) for r in rows) + " |")
    print("| cost per useful validated result (USD) | " + " | ".join(str(r["cost_per_useful_result_usd"]) for r in rows) + " |")
    return 0


def _coverage(store, args) -> int:
    from ..world.coverage import coverage_report  # noqa: PLC0415
    from .market import load_market  # noqa: PLC0415
    r = coverage_report(store, load_market(args.market), args.as_of)
    c = r["contract"]
    print(f"{c['market_id']}: contract {c['contract']} {'VALID' if c['valid'] else 'INVALID'}; coverage {r['status']} as of {r['as_of']}")
    for v in c["violations"]:
        print(f"  violation: {v}")
    for w in c["warnings"]:
        print(f"  warning: {w}")
    print("  providers: " + ", ".join(f"{k}={v}" for k, v in c["providers"].items()))
    print("  capabilities: " + "; ".join(f"{k}: {v}" for k, v in c["capabilities"].items()))
    for cid, row in r["per_competitor"].items():
        print(f"  {cid:<14} " + "  ".join(f"{k}={n}" for k, n in row.items()))
    for g in r["gaps"][:20]:
        print(f"  gap: {g}")
    return 0 if c["valid"] else 2


def _reconstruct(args) -> int:
    from . import benchmark as bm  # noqa: PLC0415
    r = bm.reconstruct(args.db, args.market, args.reconstruct, args.origin)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"reconstruction_{args.reconstruct}.json"
    path.write_text(json.dumps(r, indent=1, default=str) + "\n")
    rp = r["reproduced"]
    print(f"offline reconstruction of {r['original_replay_id']} ({r['provider']} {r['model']}, prompts {r['prompt_version']}): "
          f"{r['cache_hits']} cached replies, {len(r['recorded_outcomes_served'])} recorded outcomes served; no requests sent")
    print(f"  hypotheses: {rp['hypotheses_valid']} valid, {rp['hypotheses_rejected_by_validation']} rejected, "
          f"{rp['hypotheses_dropped']} dropped before validation")
    print(f"  scored predictions: {rp['hypothesis_predictions_scored_raw']} raw -> {rp['hypothesis_predictions_scored']} unique "
          f"({rp['duplicate_predictions_removed']} duplicates removed); supported {rp['supported']}, refuted {rp['refuted']}")
    for stage, classes in r["failures"]["items"].items():
        print(f"  {stage}: " + ", ".join(f"{k} {n}" for k, n in classes.items()))
    print("  calls: " + (", ".join(f"{k} {n}" for k, n in r["failures"]["calls"].items()) or "none failed"))
    c = r["r1_contract_on_same_items"]
    print(f"  R1 output contract on the same {c['items']} items: {c['would_pass']} would pass, {c['would_be_rejected']} would be "
          f"rejected (" + ", ".join(f"{k} {n}" for k, n in c["rejection_classes"].items()) + ")")
    print(f"report: {path}")
    return 0


def _benchmark(store, args) -> int:
    from . import benchmark as bm  # noqa: PLC0415
    from ..agents.llm import NotApproved, SpendLimitReached  # noqa: PLC0415
    if args.reconstruct:
        return _reconstruct(args)
    kind = _agent_kind(args)
    if args.max_usd is not None:
        from . import replay as rp_mod  # noqa: PLC0415
        rp_mod.RUN_CAP_USD = args.max_usd  # enforced by the SpendGuard on every attempt
    p = bm.plan(args.db, args.market, args.origin, agents=kind)
    print(f"plan ({p['provider']} {p['model']}): {p['calls']} LLM calls {p['by_role']}; prompts {p['prompt_chars_total']:,} chars; worst case "
          f"${p['worst_case_usd']:.4f} (${p['worst_case_with_retries_usd']:.4f} with retries) vs per-run cap "
          f"${p['per_run_cap_usd']} / total cap ${p['total_cap_usd']} -> {'fits' if p['fits_per_run_cap'] else 'EXCEEDS CAP'}")
    if args.dry_run:
        return 0
    if args.max_usd is not None:
        # the guard stops every attempt at --max-usd; the failure-free worst case must fit under it
        fits = p["worst_case_usd"] <= args.max_usd
        print(f"hard cap ${args.max_usd} enforced per attempt by the spend guard; failure-free worst case "
              f"${p['worst_case_usd']:.4f} -> {'fits' if fits else 'EXCEEDS'}")
    else:
        fits = p["fits_per_run_cap"]
    if not fits:
        print("refused: worst case exceeds the per-run cap", file=sys.stderr)
        return 3
    if kind != "mock" and not args.yes:
        print("refused: a paid benchmark needs --yes (review the plan above first; use --dry-run to plan only)", file=sys.stderr)
        return 3
    try:
        r = bm.run(store, args.market, kind, args.origin)
    except (NotApproved, SpendLimitReached) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"benchmark_{kind.replace(':', '_')}_{r['results'][kind]['replay_id']}.json"
    path.write_text(json.dumps({"plan": p, **r}, indent=1, default=str) + "\n")
    keys = ["hypotheses_valid", "rejected_by_validation", "scored", "supported", "refuted", "direction_hit_rate", "mae_pp",
            "coverage80", "llm_calls", "tokens_in", "tokens_out", "cost_usd", "cost_per_useful_validated_result_usd", "runtime_s"]
    names = list(r["results"])
    print("| metric | " + " | ".join(names) + " |\n|---|" + "---|" * len(names))
    for k in keys:
        print(f"| {k} | " + " | ".join(str(round(r["results"][n][k], 4)) if isinstance(r["results"][n][k], float)
                                      else str(r["results"][n][k]) for n in names) + " |")
    print(f"validation failure types: { {n: r['results'][n]['validation_failure_types'] for n in names} }")
    llm_row = r["results"][kind]
    print(f"{kind}: latency mean {llm_row.get('latency_mean_s')}s max {llm_row.get('latency_max_s')}s; failed LLM calls "
          f"{llm_row.get('failed_llm_calls')}; paid attempts {llm_row.get('paid_attempts')} "
          f"(failed, charged at reservation: {llm_row.get('failed_attempts_charged')})")
    print(f"note: {r['publication_dates']}\nreport: {path}")
    return 0
