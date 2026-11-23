"""``halibut agents`` commands: LLM provider smoke test and spend ledger.

    halibut agents smoke [--dry-run] [--model ID]   # one minimal paid call (only if approved), or a dry run
    halibut agents spend                            # application-wide ledger summary (all providers)
    halibut agents --config configs/llm/qwen.toml smoke --dry-run   # the Qwen provider
"""

from __future__ import annotations

import json
import sys

from ..config import ConfigError

ANTHROPIC_CONFIG = "configs/llm/anthropic.toml"
QWEN_CONFIG = "configs/llm/qwen.toml"
SMOKE_SYSTEM = "You are a connectivity check. Reply with JSON only."
SMOKE_USER = 'Return the JSON object {"ok": true}.'
SMOKE_CAP_DEFAULT = 0.01


def add_agents_commands(sub) -> None:
    p = sub.add_parser("agents", help="LLM agent provider: smoke test, spend ledger")
    p.add_argument("--db", default="data/halibut.sqlite")
    p.add_argument("--config", default=ANTHROPIC_CONFIG)
    a = p.add_subparsers(dest="agents_command", required=True)
    s = a.add_parser("smoke", help="one minimal request (paid) or --dry-run")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--model", help="Anthropic model id priced in the config (default: llm.model)")
    a.add_parser("spend", help="show the spend ledger summary")
    p.set_defaults(handler=_handle)


def _provider(cfg, store, scope):
    if cfg["provider"] == "anthropic":
        from .anthropic_provider import AnthropicProvider  # noqa: PLC0415
        return AnthropicProvider(cfg, store, scope)
    from .llm import QwenProvider  # noqa: PLC0415
    return QwenProvider(cfg, store, scope)


def _handle(args) -> int:
    from ..data.store import Store, utc_now  # noqa: PLC0415
    from .budget import SpendGuard  # noqa: PLC0415
    from .llm import NotApproved, SpendLimitReached, load_llm_config, pricing_from_config  # noqa: PLC0415
    store = Store(args.db)
    try:
        cfg = load_llm_config(args.config, model=getattr(args, "model", None))
        pricing = pricing_from_config(cfg)
        if args.agents_command == "spend":
            g = SpendGuard(store, cfg["provider"], cfg["model"], pricing, cfg["max_spend_usd_total"],
                           cfg["max_spend_usd_per_run"], "")
            print(json.dumps(g.summary(), indent=1))
            return 0
        max_out = cfg["max_output_tokens"]["smoke"]
        smoke_cap = min(float(cfg["max_spend_usd_per_run"]), float(cfg.get("max_spend_usd_smoke") or SMOKE_CAP_DEFAULT))
        worst = pricing.worst_case(SMOKE_SYSTEM + SMOKE_USER, max_out)
        if cfg["provider"] == "anthropic":
            print(f"smoke test: provider=anthropic model={cfg['model']} thinking={cfg.get('thinking')} "
                  f"effort={cfg.get('effort') or 'model default'} prompt_cache={cfg.get('prompt_cache') or 'off'} "
                  f"max_output_tokens={max_out}; worst-case reservation ${worst:.6f}; smoke cap ${smoke_cap}")
            print(f"  endpoint: {cfg['base_url']} (official anthropic SDK, SDK retries off; key from ${cfg['api_key_env']})")
        else:
            print(f"smoke test: provider=qwen model={cfg['model']} region={cfg['region']} "
                  f"thinking={'on' if cfg['enable_thinking'] else 'off'} max_output_tokens={max_out}; "
                  f"worst-case reservation ${worst:.6f}; smoke cap ${smoke_cap}")
            print(f"  endpoint template: {cfg['base_url']}  (workspace id from ${cfg['workspace_id_env']}, key from ${cfg['api_key_env']})")
        print(f"  approved={cfg['approved']}  application-wide caps: total ${cfg['max_spend_usd_total']}, "
              f"per run ${cfg['max_spend_usd_per_run']}")
        if args.dry_run:
            print("  dry run: no request sent")
            return 0
        scope = f"smoke:{utc_now()}"
        # a smoke test is exactly one request: no retries
        q = _provider({**cfg, "max_spend_usd_per_run": smoke_cap, "retries": 0}, store, scope)
        r = q.complete(SMOKE_SYSTEM, SMOKE_USER, role="smoke", max_output_tokens=max_out, run_id=scope)
        ok = isinstance(r.data, dict) and r.data.get("ok") is True
        extra = (f"stop_reason={getattr(r, 'stop_reason', None)} cache_read={getattr(r, 'cache_read_tokens', 0)}"
                 if cfg["provider"] == "anthropic" else f"reasoning={getattr(r, 'reasoning_tokens', 0)}")
        print(f"  {'OK' if ok else 'UNEXPECTED REPLY'}: model={r.model} latency={r.latency_seconds:.2f}s tokens={r.input_tokens}+"
              f"{r.output_tokens} {extra} cost=${(r.actual_cost_usd or 0):.6f}")
        print(json.dumps(q.guard.summary()))
        return 0 if ok and not getattr(r, "reasoning_tokens", 0) else 1
    except (NotApproved, SpendLimitReached, ConfigError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:  # provider errors are already redacted
        print(f"failed: {exc}", file=sys.stderr)
        return 4
    finally:
        store.close()
