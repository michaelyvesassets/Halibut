"""CLI commands for Phase 2 analyses: suite, sensitivity, calibrate.

All commands print a measured runtime estimate first and refuse to start if
it exceeds the spec's ``time_limit_minutes`` unless ``--yes`` is given.
``--dry-run`` prints the estimate and exits.
"""

from __future__ import annotations

import sys

from .batch import BudgetExceeded, default_workers
from .config import ConfigError


def _common(p, out_default):
    p.add_argument("spec")
    p.add_argument("--workers", type=int, default=default_workers())
    p.add_argument("--cache", default=".halibut_cache", help="run cache directory ('' disables)")
    p.add_argument("--out", default=out_default)
    p.add_argument("--yes", action="store_true", help="proceed even if over the time limit")
    p.add_argument("--dry-run", action="store_true", help="print the runtime estimate and exit")


def add_analysis_commands(sub) -> None:
    s = sub.add_parser("suite", help="run a variants x scenarios x seeds experiment suite")
    _common(s, "results/suites")
    s.set_defaults(handler=_suite)

    g = sub.add_parser("sensitivity", help="global sensitivity analysis (Morris or Sobol)")
    _common(g, "results/sensitivity")
    g.add_argument("--method", choices=("morris", "sobol"), help="override the spec's method")
    g.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                   help="extra base-config override, e.g. preference_drift.model='\"none\"' (repeatable)")
    g.set_defaults(handler=_sensitivity)


def parse_set(items: list[str]) -> dict:
    """Parse PATH=VALUE pairs; VALUE is a TOML literal (bare words are taken as strings)."""
    import tomllib  # noqa: PLC0415
    out = {}
    for item in items:
        if "=" not in item:
            raise ConfigError(f"--set expects PATH=VALUE, got {item!r}")
        path, value = item.split("=", 1)
        try:
            out[path.strip()] = tomllib.loads(f"v = {value.strip()}")["v"]
        except tomllib.TOMLDecodeError:
            out[path.strip()] = value.strip()
    return out


def _progress(i, n):
    if i == n or i % max(1, n // 10) == 0:
        print(f"  {i}/{n} tasks simulated", flush=True)


def _guard(fn):
    def wrapped(args):
        try:
            return fn(args)
        except (ConfigError, OSError, KeyError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except BudgetExceeded as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 3
    return wrapped


@_guard
def _suite(args):
    from .suite import estimate_suite, load_suite, run_suite, write_suite  # noqa: PLC0415
    spec = load_suite(args.spec)
    est = estimate_suite(spec, args.workers)
    print(f"suite {spec.name}: {len(spec.variants)} variants x {len(spec.scenarios)} scenarios x "
          f"{len(spec.seeds)} seeds = {est['scenario_runs']} runs; estimated {est['human']} "
          f"on {args.workers} worker(s)")
    if args.dry_run:
        return 0
    result = run_suite(spec, args.workers, args.cache or None, args.yes,
                       progress=_progress)
    path = write_suite(spec, result, args.out)
    a = result["analysis"]
    for s, by_metric in a["conclusions"].items():
        for m, c in by_metric.items():
            print(f"  {s:<22} {m:<24} {c['classification']:<22} "
                  f"effect range [{c['min_effect']:+.4g}, {c['max_effect']:+.4g}]")
    rt = a["runtime"]
    print(f"done: {rt['evaluated']} tasks simulated, {rt['cache_hits']} from cache. Results: {path}")
    return 0


@_guard
def _sensitivity(args):
    from .sensitivity import estimate_sensitivity, load_sensitivity, run_sensitivity, write_sensitivity  # noqa: PLC0415
    spec = load_sensitivity(args.spec, method=args.method, extra_overrides=parse_set(args.set))
    est = estimate_sensitivity(spec, args.workers)
    print(f"{spec.name} [{spec.method}] {spec.active_space().dimension} parameters, "
          f"{est['design_points']} design points x {len(spec.base.seeds)} seeds = {est['scenario_runs']} "
          f"scenario runs; estimated {est['human']} on {args.workers} worker(s)")
    if args.dry_run:
        return 0
    run = run_sensitivity(spec, args.workers, args.cache or None, args.yes, progress=_progress)
    path = write_sensitivity(spec, run, args.out)
    res = run["result"]
    key = "mu_star" if spec.method == "morris" else "ST"
    for o, d in res["per_output"].items():
        top = ", ".join(f"{p}={d['indices'][p][key]:.3g}" for p in d["ranking"][:4])
        ns = res["noise_share"][o]
        print(f"  {o:<46} noise={ns if ns is None else round(ns, 3)}  top {key}: {top}")
    print(f"Results: {path}")
    return 0
