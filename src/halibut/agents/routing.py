"""Task-class model routing (L1, R2): which provider and model serve each agent role.

``Router('mock', ...)`` serves deterministic mock responders (offline). ``Router('anthropic', ...)``
builds one approval-gated ``AnthropicProvider`` per model (so roles sharing a model share one spend
guard) and wraps each in the prompt cache. Routing is configuration (``configs/llm/routing.toml``);
changing it changes the cache key's model and is recorded in the investigation's provenance.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import load_raw
from .llm import CachedProvider, MockProvider

ROUTING = Path(__file__).resolve().parents[3] / "configs" / "llm" / "routing.toml"
ROLES = ("hypothesis_generator", "loop_critic", "strategist")


def load_routing(path=ROUTING) -> dict:
    raw = load_raw(path)
    if raw.get("schema_version") != "1.0":
        raise ValueError("routing: schema_version must be '1.0'")
    roles = raw.get("roles", {})
    missing = [r for r in ROLES if r not in roles]
    if missing:
        raise ValueError(f"routing: roles missing {missing}")
    return {"provider_config": raw["provider_config"], "default_model": raw["default_model"],
            "roles": {r: {"model": roles[r].get("model", raw["default_model"]),
                          "max_output_tokens": int(roles[r]["max_output_tokens"])} for r in ROLES}}


class Router:
    def __init__(self, kind: str, store, scope: str, *, routing: dict | None = None, responders=None,
                 max_usd: float | None = None, environ=None, http_client=None, cfg_overrides: dict | None = None):
        """``cfg_overrides`` (tests only): merged into the provider config after loading it."""
        self.kind, self.store, self.scope = kind, store, scope
        self.routing = routing or load_routing()
        self._providers: dict = {}
        if kind in ("mock", "reference"):
            if kind == "mock":
                from .mock_loop import responder  # noqa: PLC0415
            else:
                from .reference_loop import responder  # noqa: PLC0415
            self._mock = MockProvider(responders or responder, name=kind, model=f"{kind}-loop-v1")
        elif kind == "anthropic":
            from ..agents.llm import load_llm_config  # noqa: PLC0415
            self._cfg_path = Path(__file__).resolve().parents[3] / self.routing["provider_config"]
            self._max_usd, self._environ, self._http, self._over = max_usd, environ, http_client, cfg_overrides or {}
            self._load = load_llm_config
        else:
            raise ValueError(f"unknown agent kind {kind!r} (mock, reference or anthropic)")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps([self.kind, self.routing], sort_keys=True).encode()).hexdigest()[:16]

    def describe(self) -> dict:
        return {"kind": self.kind, "fingerprint": self.fingerprint,
                "roles": {r: {**v, "model": v["model"] if self.kind == "anthropic" else f"{self.kind}-loop-v1"}
                          for r, v in self.routing["roles"].items()}}

    def provider(self, role: str) -> tuple[CachedProvider, int]:
        spec = self.routing["roles"][role]
        if self.kind in ("mock", "reference"):
            key = self.kind
            if key not in self._providers:
                self._providers[key] = CachedProvider(self._mock, self.store)
            return self._providers[key], spec["max_output_tokens"]
        model = spec["model"]
        if model not in self._providers:
            from .anthropic_provider import AnthropicProvider  # noqa: PLC0415
            cfg = {**self._load(self._cfg_path, model=model), **self._over}
            if self._max_usd is not None:
                cfg["max_spend_usd_per_run"] = min(float(cfg["max_spend_usd_per_run"]), float(self._max_usd))
            self._providers[model] = CachedProvider(AnthropicProvider(cfg, self.store, self.scope, environ=self._environ,
                                                                      http_client=self._http), self.store)
        return self._providers[model], spec["max_output_tokens"]
