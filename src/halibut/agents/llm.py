"""Shared LLM provider interface: mock (offline), cache wrapper, Qwen (approval-gated).

Token counts from the mock are ESTIMATES (characters / 4) and flagged as such.
Costs use the prices in the provider config; the Qwen provider refuses to run
unless the config is explicitly approved, prices are set and the API key is
present in the environment, and it stops before exceeding ``max_spend_usd``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..config import ConfigError, _Table, load_raw
from ..data.store import utc_now


@dataclass
class LLMResponse:
    text: str
    data: dict | None
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool
    model: str
    provider: str
    latency_seconds: float
    cached: bool = False
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, *, role: str, max_output_tokens: int = 1500) -> LLMResponse: ...


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


@dataclass
class Pricing:
    input_per_mtok: float | None = None
    output_per_mtok: float | None = None
    source: str = "unset"

    def cost(self, tin: int, tout: int) -> float | None:
        if self.input_per_mtok is None or self.output_per_mtok is None:
            return None
        return tin / 1e6 * self.input_per_mtok + tout / 1e6 * self.output_per_mtok


class NotApproved(ConfigError):
    pass


class SpendLimitReached(RuntimeError):
    pass


@dataclass
class MockProvider:
    """Deterministic offline provider. ``responder(role, payload) -> dict`` produces the JSON answer."""

    responder: Callable[[str, dict], dict]
    name: str = "mock"
    model: str = "mock-structured-v1"
    pricing: Pricing = field(default_factory=Pricing)

    def complete(self, system: str, user: str, *, role: str, max_output_tokens: int = 1500) -> LLMResponse:
        t0 = time.perf_counter()
        payload = json.loads(user.split("BUNDLE:", 1)[1])
        data = self.responder(role, payload)
        text = json.dumps(data, sort_keys=True)
        tin, tout = estimate_tokens(system + user), estimate_tokens(text)
        return LLMResponse(text, data, tin, tout, True, self.model, self.name, time.perf_counter() - t0,
                           estimated_cost_usd=self.pricing.cost(tin, tout))


class CachedProvider:
    """Prompt-level cache in the shared store: identical (provider, model, system, user) -> stored response."""

    def __init__(self, inner: LLMProvider, store):
        self.inner, self.store = inner, store
        self.name, self.model = inner.name, inner.model
        self.hits = self.misses = 0

    def key(self, system: str, user: str, role: str) -> str:
        return hashlib.sha256(json.dumps([self.name, self.model, role, system, user]).encode()).hexdigest()

    def complete(self, system: str, user: str, *, role: str, max_output_tokens: int = 1500) -> LLMResponse:
        k = self.key(system, user, role)
        row = self.store.conn.execute("SELECT response_json FROM llm_cache WHERE cache_key=?", (k,)).fetchone()
        if row:
            self.hits += 1
            d = json.loads(row[0])
            return LLMResponse(**{**d, "cached": True, "latency_seconds": 0.0, "estimated_cost_usd": 0.0,
                                  "actual_cost_usd": 0.0 if d.get("actual_cost_usd") is not None else None})
        self.misses += 1
        r = self.inner.complete(system, user, role=role, max_output_tokens=max_output_tokens)
        self.store.conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?,?)",
                                (k, r.provider, r.model, json.dumps(r.__dict__), r.input_tokens, r.output_tokens, utc_now()))
        self.store.conn.commit()
        return r


class QwenProvider:
    """OpenAI-compatible chat-completions client for Qwen (Alibaba Cloud Model Studio).

    Disabled by default. Requires a config with ``approved = true``, explicit
    prices from the official pricing page, ``max_spend_usd``, and the API key in
    the environment variable named by ``api_key_env``. Not used in tests.
    """

    name = "qwen"

    def __init__(self, cfg: dict, spent_so_far: Callable[[], float] = lambda: 0.0):
        if not cfg.get("approved"):
            raise NotApproved("Qwen provider is not approved: set approved = true in the LLM config after review")
        for k in ("base_url", "model", "api_key_env", "input_per_mtok_usd", "output_per_mtok_usd", "max_spend_usd"):
            if cfg.get(k) in (None, ""):
                raise NotApproved(f"LLM config missing {k!r}")
        self.key = os.environ.get(cfg["api_key_env"])
        if not self.key:
            raise NotApproved(f"environment variable {cfg['api_key_env']} is not set")
        self.cfg, self.model = cfg, cfg["model"]
        self.pricing = Pricing(cfg["input_per_mtok_usd"], cfg["output_per_mtok_usd"], cfg.get("pricing_source", "config"))
        self.spent = spent_so_far

    def complete(self, system: str, user: str, *, role: str, max_output_tokens: int = 1500) -> LLMResponse:
        worst = self.pricing.cost(estimate_tokens(system + user), max_output_tokens) or 0.0
        if self.spent() + worst > self.cfg["max_spend_usd"]:
            raise SpendLimitReached(f"next call could exceed max_spend_usd={self.cfg['max_spend_usd']}")
        body = json.dumps({"model": self.model, "messages": [{"role": "system", "content": system},
                                                               {"role": "user", "content": user}],
                           "temperature": self.cfg.get("temperature", 0), "max_tokens": max_output_tokens,
                           "response_format": {"type": "json_object"}}).encode()
        req = urllib.request.Request(self.cfg["base_url"].rstrip("/") + "/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.cfg.get("timeout_seconds", 120)) as resp:
            d = json.loads(resp.read())
        text = d["choices"][0]["message"]["content"]
        usage = d.get("usage", {})
        tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
        estimated = tin is None or tout is None
        tin, tout = tin or estimate_tokens(system + user), tout or estimate_tokens(text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        return LLMResponse(text, data, tin, tout, estimated, self.model, self.name, time.perf_counter() - t0,
                           estimated_cost_usd=self.pricing.cost(tin, tout))


def load_llm_config(path) -> dict:
    root = _Table(load_raw(path), "")
    t = root.table("llm")
    cfg = {k: t.raw(k, None) for k in ("provider", "base_url", "model", "api_key_env", "input_per_mtok_usd",
                                       "output_per_mtok_usd", "pricing_source", "max_spend_usd", "temperature",
                                       "timeout_seconds", "approved", "approved_by", "approved_on")}
    t.done()
    root.done()
    if cfg["provider"] not in ("qwen", "mock"):
        raise ConfigError("llm.provider must be 'qwen' or 'mock'")
    return cfg


def record_call(store, run_id, agent: str, role: str, r: LLMResponse | None, prompt_hash: str, *,
                tool_calls: list | None = None, duplicates: int = 0, outcome: str = "ok", provider="", model="") -> None:
    store.conn.execute(
        """INSERT INTO agent_calls (run_id, agent, role, provider, model, input_tokens, output_tokens, tokens_estimated,
           estimated_cost_usd, actual_cost_usd, latency_seconds, cache_hit, prompt_hash, tool_calls_json,
           duplicate_tool_calls, outcome, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, agent, role, r.provider if r else provider, r.model if r else model, r.input_tokens if r else 0,
         r.output_tokens if r else 0, int(r.tokens_estimated) if r else 1, r.estimated_cost_usd if r else None,
         r.actual_cost_usd if r else None, r.latency_seconds if r else 0.0, int(r.cached) if r else 0, prompt_hash,
         json.dumps(tool_calls or []), duplicates, outcome, utc_now()))
    store.conn.commit()


def run_costs(store, run_id: str) -> dict:
    rows = store.conn.execute("SELECT * FROM agent_calls WHERE run_id=?", (run_id,)).fetchall()
    n = len(rows)
    est = [r["estimated_cost_usd"] for r in rows if r["estimated_cost_usd"] is not None]
    return {"calls": n, "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "tokens_estimated": any(r["tokens_estimated"] for r in rows),
            "estimated_cost_usd": sum(est) if est else None,
            "actual_cost_usd": sum(r["actual_cost_usd"] or 0 for r in rows) if any(r["actual_cost_usd"] is not None for r in rows) else None,
            "latency_seconds": sum(r["latency_seconds"] or 0 for r in rows),
            "cache_hit_rate": (sum(r["cache_hit"] for r in rows) / n) if n else None,
            "duplicate_tool_calls": sum(r["duplicate_tool_calls"] for r in rows),
            "failed_calls": sum(r["outcome"] != "ok" for r in rows),
            "by_role": {role: sum(1 for r in rows if r["role"] == role) for role in {r["role"] for r in rows}}}
