"""Command-line interface.

    halibut validate CONFIG
    halibut run CONFIG [--scenario NAME ...] [--replicates N | --seed S ...] [--warmup W] [--out DIR]
    halibut diagnose CONFIG [--replicates N] [--warmup W] [--stockout-counterfactual]
                            [--size-convergence 2000,5000] [--out DIR]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .config import CONTROL, ConfigError, config_hash, load_config
from .experiment import run_experiment, summarize
from .export import synthetic_label, write_json, write_results
from .version import ENGINE_VERSION, SYNTHETIC_DISCLAIMER


def _add_seed_args(p):
    seeds = p.add_mutually_exclusive_group()
    seeds.add_argument("--replicates", type=int, help="use the first N configured seeds")
    seeds.add_argument("--seed", type=int, action="append", help="explicit seed (repeatable)")
    p.add_argument("--warmup", type=int, help="override experiment.warmup_weeks")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="halibut", description="Halibut AI synthetic market simulator")
    p.add_argument("--version", action="version", version=f"halibut {ENGINE_VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate a config and print its hash")
    v.add_argument("config")

    r = sub.add_parser("run", help="run an experiment")
    r.add_argument("config")
    r.add_argument("--scenario", action="append", help="scenario to run (repeatable; default: all)")
    _add_seed_args(r)
    r.add_argument("--out", default="results", help="output directory (default: results)")
    r.add_argument("--quiet", action="store_true")

    d = sub.add_parser("diagnose", help="run an experiment and its diagnostics")
    d.add_argument("config")
    _add_seed_args(d)
    d.add_argument("--stockout-counterfactual", action="store_true",
                   help="also rerun every scenario with full availability (doubles cost)")
    d.add_argument("--size-convergence", help="comma-separated population sizes to compare")
    d.add_argument("--out", default="results")

    from .analysis_cli import add_analysis_commands  # noqa: PLC0415
    add_analysis_commands(sub)
    from .data.cli import add_data_commands  # noqa: PLC0415
    add_data_commands(sub)
    from .intel.cli import add_intel_commands  # noqa: PLC0415
    add_intel_commands(sub)
    from .learning.cli import add_learn_commands  # noqa: PLC0415
    add_learn_commands(sub)
    return p


def _resolve_seeds(cfg, args):
    if args.seed:
        return args.seed
    if args.replicates:
        if args.replicates < 1 or args.replicates > len(cfg.seeds):
            raise ValueError(f"--replicates must be between 1 and {len(cfg.seeds)}")
        return cfg.seeds[: args.replicates]
    return cfg.seeds


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


def _print_checks(report: dict) -> None:
    c = report["status_counts"]
    print(f"\nDiagnostics: {c['fail']} fail, {c['warn']} warn, {c['pass']} pass, {c['info']} info")
    for chk in report["checks"]:
        if chk["status"] in ("fail", "warn"):
            print(f"  [{chk['status'].upper()}] {chk['name']}: {chk['message']}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "handler", None):
        return args.handler(args)
    try:
        cfg = load_config(args.config)
        if getattr(args, "warmup", None) is not None:
            if args.warmup < 0:
                raise ConfigError("--warmup must be >= 0")
            cfg = dataclasses.replace(cfg, warmup_weeks=args.warmup)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        print(f"OK  {cfg.name}  hash={config_hash(cfg)}")
        print(f"    {cfg.population.size} consumers, {cfg.weeks} weeks, {len(cfg.seeds)} seeds, "
              f"warmup={cfg.warmup_weeks}, drift={cfg.preference_drift.effective_model}, "
              f"products={list(cfg.product_ids)}, scenarios={cfg.scenario_names}")
        return 0

    try:
        seeds = _resolve_seeds(cfg, args)
        result = run_experiment(
            cfg, getattr(args, "scenario", None), seeds,
            progress=None if getattr(args, "quiet", False) else lambda m: print(f"  {m}"),
        )
    except (KeyError, ValueError) as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    path = write_results(result, args.out)
    rt = result.runtime
    report = json.loads((path / "diagnostics.json").read_text())
    if not getattr(args, "quiet", False):
        _print_summary(summarize(result))
        _print_checks(report)

    if args.command == "diagnose":
        from .diagnostics import size_convergence, stockout_counterfactual  # noqa: PLC0415
        if args.stockout_counterfactual:
            data = stockout_counterfactual(cfg, seeds)
            write_json(path / "stockout_counterfactual.json", {**synthetic_label(), **data})
            print(f"  wrote {path / 'stockout_counterfactual.json'}")
        if args.size_convergence:
            sizes = [int(x) for x in args.size_convergence.split(",")]
            data = size_convergence(cfg, sizes, seeds)
            write_json(path / "size_convergence.json", {**synthetic_label(), **data})
            print(f"  wrote {path / 'size_convergence.json'}")

    print(f"\n{rt['runs']} runs in {rt['total_seconds']:.2f}s "
          f"({rt['agent_weeks_per_second']:,.0f} agent-weeks/s). Results: {path}")
    return 1 if report["status_counts"]["fail"] else 0
