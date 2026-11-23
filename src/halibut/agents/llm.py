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
import urllib.error
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

    def complete(self, system: str, user: str, *, role: str, max_output_tokens: int = 1500, **_) -> LLMResponse:
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

    def complete(self, system: str, user: str, *, role: str, max_output_tokens: int | None = None,
                 run_id: str = "unscoped") -> LLMResponse:
        k = self.key(system, user, role)
        row = self.store.conn.execute("SELECT response_json FROM llm_cache WHERE cache_key=?", (k,)).fetchone()
        if row:
            self.hits += 1
            d = json.loads(row[0])
            d.pop("reasoning_tokens", None)
            return LLMResponse(**{**d, "cached": True, "latency_seconds": 0.0, "estimated_cost_usd": 0.0,
                                  "actual_cost_usd": 0.0 if d.get("actual_cost_usd") is not None else None})
        self.misses += 1
        kw = {"run_id": run_id} if isinstance(self.inner, QwenProvider) else {}
        r = self.inner.complete(system, user, role=role, max_output_tokens=max_output_tokens or 1500, **kw)
        rec = {f: getattr(r, f) for f in ("text", "data", "input_tokens", "output_tokens", "tokens_estimated", "model",
                                          "provider", "latency_seconds", "cached", "estimated_cost_usd", "actual_cost_usd")}
        self.store.conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?,?)",
                                (k, r.provider, r.model, json.dumps(rec), r.input_tokens, r.output_tokens, utc_now()))
        self.store.conn.commit()
        return r


class QwenProvider:
    """OpenAI-compatible chat-completions client for Qwen on Alibaba Cloud Model Studio (Singapore).

    Disabled unless the config is approved, priced and capped, and the API key and
    workspace id are present in the environment. Every attempt is reserved against
    the fail-closed ``SpendGuard`` before the request is sent. Thinking mode (on by
    default for qwen3.7-plus, billed as output) is disabled per request. The API key
    is only placed in the Authorization header: never logged, stored or printed.
    """

    name = "qwen"
    RETRY_STATUSES = (429, 500, 502, 503, 504)

    def __init__(self, cfg: dict, store, scope: str, transport=None, environ=None):
        from .budget import SpendGuard  # noqa: PLC0415
        env = os.environ if environ is None else environ
        if not cfg.get("approved"):
            raise NotApproved("Qwen provider is not approved: set approved = true in the LLM config after review")
        for k in ("base_url", "model", "api_key_env", "input_per_mtok_usd", "output_per_mtok_usd",
                  "max_spend_usd_total", "max_spend_usd_per_run"):
            if cfg.get(k) in (None, ""):
                raise NotApproved(f"LLM config missing {k!r}")
        self._key = env.get(cfg["api_key_env"])
        if not self._key:
            raise NotApproved(f"environment variable {cfg['api_key_env']} is not set")
        base = cfg["base_url"]
        if "{workspace_id}" in base:
            ws = env.get(cfg.get("workspace_id_env") or "")
            if not ws:
                raise NotApproved(f"environment variable {cfg.get('workspace_id_env')} (workspace id) is not set")
            base = base.replace("{workspace_id}", ws)
        self.url = base.rstrip("/") + "/chat/completions"
        self.cfg, self.model = cfg, cfg["model"]
        self.pricing = Pricing(float(cfg["input_per_mtok_usd"]), float(cfg["output_per_mtok_usd"]), cfg.get("pricing_source", ""))
        self.guard = SpendGuard(store, "qwen", self.model, self.pricing, float(cfg["max_spend_usd_total"]),
                                float(cfg["max_spend_usd_per_run"]), scope)
        self.transport = transport or self._urllib
        self.retries = min(int(cfg.get("retries", 1)), 2)

    def _urllib(self, url, body, headers, timeout):
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() or b""

    def request_body(self, system: str, user: str, max_output_tokens: int) -> dict:
        return {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": self.cfg.get("temperature", 0), "max_tokens": max_output_tokens,
                "response_format": {"type": "json_object"},
                "enable_thinking": bool(self.cfg.get("enable_thinking", False))}

    def complete(self, system: str, user: str, *, role: str, max_output_tokens: int | None = None,
                 run_id: str = "unscoped") -> LLMResponse:
        if "json" not in (system + user).lower():
            raise ValueError("JSON mode requires the word 'JSON' in the prompt")
        max_out = int(max_output_tokens or self.cfg.get("max_output_tokens", {}).get(role, 1200))
        body = json.dumps(self.request_body(system, user, max_out)).encode()
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        last = None
        for attempt in range(self.retries + 1):
            ledger_run = run_id if run_id.startswith(self.guard.scope) else f"{self.guard.scope}:{run_id}"
            entry, worst = self.guard.reserve(system + user, max_out, ledger_run)
            t0 = time.perf_counter()
            try:
                status, raw = self.transport(self.url, body, headers, self.cfg.get("timeout_seconds", 120))
            except Exception as exc:  # network failure: may or may not have been billed -> keep charged
                self.guard.charge_failed(entry, f"attempt {attempt}: {type(exc).__name__}")
                last = RuntimeError(f"Qwen request failed ({type(exc).__name__})")
                continue
            latency = time.perf_counter() - t0
            if status != 200:
                self.guard.charge_failed(entry, f"attempt {attempt}: HTTP {status}")
                last = RuntimeError(f"Qwen HTTP {status}: {raw[:200].decode('utf-8', 'replace')}")
                if status in self.RETRY_STATUSES:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                break
            d = json.loads(raw)
            usage = d.get("usage") or {}
            tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            actual = self.pricing.cost(tin, tout) if tin is not None and tout is not None else None
            self.guard.settle(entry, actual, f"reasoning_tokens={reasoning}" if reasoning else "")
            text = d["choices"][0]["message"].get("content") or ""
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            r = LLMResponse(text, data, tin or conservative_tokens(system + user), tout or max_out, tin is None or tout is None,
                            d.get("model", self.model), self.name, latency, estimated_cost_usd=worst, actual_cost_usd=actual)
            r.reasoning_tokens = reasoning
            return r
        raise last


def conservative_tokens(text: str) -> int:
    from .budget import conservative_input_tokens  # noqa: PLC0415
    return conservative_input_tokens(text)


def load_llm_config(path) -> dict:
    root = _Table(load_raw(path), "")
    t = root.table("llm")
    cfg = {k: t.raw(k, None) for k in ("provider", "region", "base_url", "workspace_id_env", "model", "api_key_env",
                                       "input_per_mtok_usd", "output_per_mtok_usd", "pricing_source", "pricing_note",
                                       "max_spend_usd_total", "max_spend_usd_per_run", "temperature", "enable_thinking",
                                       "retries", "timeout_seconds", "max_output_tokens", "approved", "approved_by",
                                       "approved_on")}
    for secretish in ("api_key", "key", "token", "secret"):
        if secretish in t.data:
            raise ConfigError(f"llm.{secretish}: secrets must not be stored in config files; use an environment variable")
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
