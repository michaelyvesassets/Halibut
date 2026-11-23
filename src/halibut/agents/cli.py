"""``halibut agents`` commands: LLM provider smoke test and cost planning.

    halibut agents smoke [--dry-run]         # one minimal paid call (only if approved), or a dry run
    halibut agents spend                     # ledger summary
"""

from __future__ import annotations

import json
import sys

from ..config import ConfigError

QWEN_CONFIG = "configs/llm/qwen.toml"
SMOKE_SYSTEM = "You are a connectivity check. Reply with JSON only."
SMOKE_USER = 'Return the JSON object {"ok": true}.'


def add_agents_commands(sub) -> None:
    p = sub.add_parser("agents", help="LLM agent provider: smoke test, spend ledger")
    p.add_argument("--db", default="data/halibut.sqlite")
    p.add_argument("--config", default=QWEN_CONFIG)
    a = p.add_subparsers(dest="agents_command", required=True)
    s = a.add_parser("smoke", help="one minimal request (paid) or --dry-run")
    s.add_argument("--dry-run", action="store_true")
    a.add_parser("spend", help="show the spend ledger summary")
    p.set_defaults(handler=_handle)


def _handle(args) -> int:
    from ..data.store import Store, utc_now  # noqa: PLC0415
    from .budget import SpendGuard  # noqa: PLC0415
    from .llm import NotApproved, Pricing, QwenProvider, SpendLimitReached, load_llm_config  # noqa: PLC0415
    store = Store(args.db)
    try:
        cfg = load_llm_config(args.config)
        if args.agents_command == "spend":
            g = SpendGuard(store, "qwen", cfg["model"], Pricing(cfg["input_per_mtok_usd"], cfg["output_per_mtok_usd"]),
                           cfg["max_spend_usd_total"], cfg["max_spend_usd_per_run"], "")
            print(json.dumps(g.summary(), indent=1))
            return 0
        max_out = cfg["max_output_tokens"]["smoke"]
        pricing = Pricing(cfg["input_per_mtok_usd"], cfg["output_per_mtok_usd"])
        from .budget import conservative_input_tokens  # noqa: PLC0415
        worst = pricing.cost(conservative_input_tokens(SMOKE_SYSTEM + SMOKE_USER), max_out)
        print(f"smoke test: model={cfg['model']} region={cfg['region']} thinking={'on' if cfg['enable_thinking'] else 'off'} "
              f"max_output_tokens={max_out}; worst-case reservation ${worst:.6f}")
        print(f"  endpoint template: {cfg['base_url']}  (workspace id from ${cfg['workspace_id_env']}, key from ${cfg['api_key_env']})")
        if args.dry_run:
            print("  dry run: no request sent")
            return 0
        scope = f"smoke:{utc_now()}"
        q = QwenProvider({**cfg, "max_spend_usd_per_run": min(cfg["max_spend_usd_per_run"], 0.01)}, store, scope)
        r = q.complete(SMOKE_SYSTEM, SMOKE_USER, role="smoke", max_output_tokens=max_out, run_id=scope)
        ok = isinstance(r.data, dict) and r.data.get("ok") is True
        print(f"  {'OK' if ok else 'UNEXPECTED REPLY'}: model={r.model} latency={r.latency_seconds:.2f}s tokens={r.input_tokens}+"
              f"{r.output_tokens} reasoning={getattr(r, 'reasoning_tokens', 0)} cost=${(r.actual_cost_usd or 0):.6f}")
        print(json.dumps(q.guard.summary()))
        return 0 if ok and not getattr(r, "reasoning_tokens", 0) else 1
    except (NotApproved, SpendLimitReached, ConfigError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    finally:
        store.close()
