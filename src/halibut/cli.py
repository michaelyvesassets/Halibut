"""Command-line interface.

    halibut validate CONFIG
    halibut run CONFIG [--scenario NAME ...] [--replicates N | --seed S ...] [--out DIR]
"""

from __future__ import annotations

import argparse
import sys

from .config import CONTROL, ConfigError, config_hash, load_config
from .experiment import run_experiment, summarize
from .export import write_results
from .version import ENGINE_VERSION, SYNTHETIC_DISCLAIMER


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="halibut", description="Halibut AI synthetic market simulator")
    p.add_argument("--version", action="version", version=f"halibut {ENGINE_VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate a config and print its hash")
    v.add_argument("config")

    r = sub.add_parser("run", help="run an experiment")
    r.add_argument("config")
    r.add_argument("--scenario", action="append", help="scenario to run (repeatable; default: all)")
    seeds = r.add_mutually_exclusive_group()
    seeds.add_argument("--replicates", type=int, help="use the first N configured seeds")
    seeds.add_argument("--seed", type=int, action="append", help="explicit seed (repeatable)")
    r.add_argument("--out", default="results", help="output directory (default: results)")
    r.add_argument("--quiet", action="store_true")
    return p


def _print_summary(summary: dict) -> None:
    products = summary["products"]
    w = summary["summary_window_weeks"]
    print(f"\nFinal {w}-week window, mean across {len(summary['seeds'])} seeds "
          "(deltas are paired vs control):")
    header = ["scenario"] + [f"share_{p}" for p in products] + [
        "units", "no_purchase", "d_units", "displaced", "contraction", "expansion"]
    print("  ".join(f"{h:>12}" for h in header))
    for name, s in summary["scenarios"].items():
        fw = s["final_window"]
        cells = [name[:12]] + [f"{fw[f'purchase_share_{p}']['mean']:.3f}" for p in products]
        cells += [f"{fw['category_units']['mean']:.0f}", f"{fw['no_purchase_rate']['mean']:.3f}"]
        if name == CONTROL:
            cells += ["-"] * 4
        else:
            d = s["vs_control"]["decomposition"]
            cells += [f"{d['net_category_change']['mean']:+.0f}", f"{d['displaced']['mean']:.0f}",
                      f"{d['contraction']['mean']:.0f}", f"{d['expansion']['mean']:.0f}"]
        print("  ".join(f"{c:>12}" for c in cells))
    print(f"\nNOTE: {SYNTHETIC_DISCLAIMER}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        print(f"OK  {cfg.name}  hash={config_hash(cfg)}")
        print(f"    {cfg.population.size} consumers, {cfg.weeks} weeks, {len(cfg.seeds)} seeds, "
              f"products={list(cfg.product_ids)}, scenarios={cfg.scenario_names}")
        return 0

    if args.seed:
        seeds = args.seed
    elif args.replicates:
        if args.replicates < 1 or args.replicates > len(cfg.seeds):
            print(f"error: --replicates must be between 1 and {len(cfg.seeds)}", file=sys.stderr)
            return 2
        seeds = cfg.seeds[: args.replicates]
    else:
        seeds = cfg.seeds
    try:
        result = run_experiment(
            cfg, args.scenario, seeds, progress=None if args.quiet else lambda m: print(f"  {m}")
        )
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    path = write_results(result, args.out)
    rt = result.runtime
    if not args.quiet:
        _print_summary(summarize(result))
    print(f"\n{rt['runs']} runs in {rt['total_seconds']:.2f}s "
          f"({rt['agent_weeks_per_second']:,.0f} agent-weeks/s). Results: {path}")
    return 0
