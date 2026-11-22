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
