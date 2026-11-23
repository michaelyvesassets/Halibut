"""Anthropic (Claude) provider behind the shared ``LLMProvider`` interface. Approval-gated.

Uses the official ``anthropic`` Python SDK (optional dependency: ``pip install -e '.[anthropic]'``).

Safety and cost rules (same contract as the Qwen provider):
- Disabled unless the config is approved, the selected model is priced and capped, and
  ``ANTHROPIC_API_KEY`` (the variable named in the config) is set. The key is passed to the
  SDK client only; it is never logged, stored, printed or included in error messages.
- The base URL is pinned from the config, so an ``ANTHROPIC_BASE_URL`` in the environment
  cannot redirect the key elsewhere.
- SDK auto-retries are disabled (``max_retries=0``); Halibut retries itself so that EVERY
  attempt is reserved against the fail-closed ``SpendGuard`` before it is sent.
- Costs are computed from the reported usage at the official rates, including cache writes
  (5-minute and 1-hour) and cache reads, and Haiku 5.5's higher rate card for prompts over
  100,000 tokens. Missing usage -> the worst-case reservation is kept as the charge.
- No sampling parameters (temperature/top_p/top_k are rejected by current Claude models) and
  no assistant prefill (rejected); JSON is requested in the prompt and parsed from text blocks.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass

from .llm import LLMResponse, NotApproved, Pricing

RETRY_STATUSES = (408, 409, 429, 500, 502, 503, 504, 529)
RETRY_EXCEPTIONS = ("APIConnectionError", "APITimeoutError")

# thinking settings each model accepts (from the model migration guide); anything else -> refuse offline
THINKING_RULES = {
    "claude-haiku-5-5": {"disabled", "adaptive"},    # 'disabled' only at effort <= high
    "claude-sonnet-5-5": {"between_tools", "adaptive"},  # 'disabled' is a 400 on Sonnet 5.5
}
EFFORTS = (None, "low", "medium", "high", "xhigh", "max")


@dataclass
class AnthropicPricing(Pricing):
    """Official per-MTok rates for one model, with prompt-caching rates and an optional long-prompt card."""

    cache_write_5m_per_mtok: float | None = None
    cache_write_1h_per_mtok: float | None = None
    cache_read_per_mtok: float | None = None
    long_threshold_tokens: int | None = None  # prompts ABOVE this many input tokens use the long card
    long: dict | None = None
    chars_per_token_worst_case: float = 2.0
    caching: str = "off"

    def card(self, prompt_tokens: int) -> dict:
        base = {"input": self.input_per_mtok, "output": self.output_per_mtok, "cache_write_5m": self.cache_write_5m_per_mtok,
                "cache_write_1h": self.cache_write_1h_per_mtok, "cache_read": self.cache_read_per_mtok}
        if self.long_threshold_tokens is not None and prompt_tokens > self.long_threshold_tokens:
            return {**base, **(self.long or {})}
        return base

    def cost_usage(self, input_tokens: int, output_tokens: int, cache_write_5m: int = 0, cache_write_1h: int = 0,
                   cache_read: int = 0) -> float:
        """Cost of one request. The rate card is chosen by the request's whole prompt (uncached + cache writes + reads)."""
        c = self.card(input_tokens + cache_write_5m + cache_write_1h + cache_read)
        return (input_tokens * c["input"] + output_tokens * c["output"] + cache_write_5m * c["cache_write_5m"]
                + cache_write_1h * c["cache_write_1h"] + cache_read * c["cache_read"]) / 1e6

    def cost(self, tin: int, tout: int) -> float | None:
        return self.cost_usage(tin, tout)

    def worst_case_input_tokens(self, prompt_text: str) -> int:
        # deliberately pessimistic (~2 chars/token, i.e. well above the newer tokenizer's count for English/JSON)
        return math.ceil(len(prompt_text) / self.chars_per_token_worst_case) + 64

    def worst_case(self, prompt_text: str, max_output_tokens: int) -> float:
        tin = self.worst_case_input_tokens(prompt_text)
        c = self.card(tin)
        rate_in = max(c["input"], c["cache_write_1h"] if self.caching == "1h" else
                      c["cache_write_5m"] if self.caching == "5m" else c["input"])
        return (tin * rate_in + max_output_tokens * c["output"]) / 1e6


def pricing_from_config(cfg: dict) -> AnthropicPricing:
    long = cfg.get("long_context") or None
    if long:
        long = {"input": long["input_per_mtok_usd"], "output": long["output_per_mtok_usd"],
                "cache_write_5m": long["cache_write_5m_per_mtok_usd"], "cache_write_1h": long["cache_write_1h_per_mtok_usd"],
                "cache_read": long["cache_read_per_mtok_usd"]}
    return AnthropicPricing(cfg["input_per_mtok_usd"], cfg["output_per_mtok_usd"], cfg.get("pricing_source") or "",
                            cfg["cache_write_5m_per_mtok_usd"], cfg["cache_write_1h_per_mtok_usd"],
                            cfg["cache_read_per_mtok_usd"], cfg.get("long_context_threshold_tokens"), long,
                            float(cfg.get("worst_case_chars_per_token") or 2.0), cfg.get("prompt_cache") or "off")


def parse_json_text(text: str) -> dict | None:
    """JSON object from a text reply: plain, fenced (```json ... ```), or the outermost {...} span."""
    s = text.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", s, flags=re.S)
    if m:
        s = m.group(1)
    for candidate in (s, s[s.find("{"): s.rfind("}") + 1] if "{" in s and "}" in s else ""):
        if not candidate:
            continue
        try:
            d = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return d if isinstance(d, dict) else None
    return None


class AnthropicProvider:
    name = "anthropic"
    accepts_run_id = True

    def __init__(self, cfg: dict, store, scope: str, client=None, environ=None, http_client=None):
        """``http_client`` (an ``httpx2.Client``) is for offline tests with a mock transport."""
        from .budget import SpendGuard  # noqa: PLC0415
        env = os.environ if environ is None else environ
        if cfg.get("provider") != "anthropic":
            raise NotApproved("not an Anthropic LLM config")
        if not cfg.get("approved"):
            raise NotApproved("Anthropic provider is not approved: set approved = true in the LLM config after review")
        for k in ("base_url", "model", "api_key_env", "input_per_mtok_usd", "output_per_mtok_usd",
                  "cache_write_5m_per_mtok_usd", "cache_write_1h_per_mtok_usd", "cache_read_per_mtok_usd",
                  "max_spend_usd_total", "max_spend_usd_per_run"):
            if cfg.get(k) in (None, ""):
                raise NotApproved(f"LLM config missing {k!r}")
        model, thinking, effort = cfg["model"], cfg.get("thinking"), cfg.get("effort")
        if model in THINKING_RULES and thinking is not None and thinking not in THINKING_RULES[model]:
            raise NotApproved(f"thinking={thinking!r} is rejected by {model}; allowed: {sorted(THINKING_RULES[model])}")
        if thinking == "disabled" and effort in ("xhigh", "max"):
            raise NotApproved("thinking 'disabled' is only accepted at effort low/medium/high")
        if effort not in EFFORTS:
            raise NotApproved(f"unknown effort {effort!r}")
        self._key = env.get(cfg["api_key_env"])
        if not self._key:
            raise NotApproved(f"environment variable {cfg['api_key_env']} is not set")
        self.cfg, self.model = cfg, model
        self.pricing = pricing_from_config(cfg)
        self.guard = SpendGuard(store, self.name, model, self.pricing, float(cfg["max_spend_usd_total"]),
                                float(cfg["max_spend_usd_per_run"]), scope)
        self.retries = min(int(cfg.get("retries", 1)), 2)
        # request-shaping settings are part of the prompt-cache key: changing them never reuses old answers
        self.cache_salt = json.dumps({"thinking": thinking, "effort": effort, "prompt_cache": cfg.get("prompt_cache")},
                                     sort_keys=True)
        if client is None:
            try:
                import anthropic  # noqa: PLC0415
            except ImportError as exc:
                raise NotApproved("the official 'anthropic' SDK is not installed: pip install -e '.[anthropic]'") from exc
            client = anthropic.Anthropic(api_key=self._key, base_url=cfg["base_url"], max_retries=0,
                                         timeout=float(cfg.get("timeout_seconds") or 120),
                                         **({"http_client": http_client} if http_client is not None else {}))
        self.client = client

    def _redact(self, s: str) -> str:
        return s.replace(self._key, "[REDACTED]") if self._key else s

    def request_kwargs(self, system: str, user: str, max_output_tokens: int) -> dict:
        kw = {"model": self.model, "max_tokens": max_output_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]}
        if self.cfg.get("thinking"):
            kw["thinking"] = {"type": self.cfg["thinking"]}
        if self.cfg.get("effort"):
            kw["output_config"] = {"effort": self.cfg["effort"]}
        pc = self.cfg.get("prompt_cache") or "off"
        if pc != "off":
            kw["cache_control"] = {"type": "ephemeral", **({"ttl": "1h"} if pc == "1h" else {})}
        return kw

    def complete(self, system: str, user: str, *, role: str, max_output_tokens: int | None = None,
                 run_id: str = "unscoped") -> LLMResponse:
        if "json" not in (system + user).lower():
            raise ValueError("Halibut agent prompts must ask for JSON")
        max_out = int(max_output_tokens or (self.cfg.get("max_output_tokens") or {}).get(role, 1200))
        kw = self.request_kwargs(system, user, max_out)
        ledger_run = run_id if run_id.startswith(self.guard.scope) else f"{self.guard.scope}:{run_id}"
        last = None
        for attempt in range(self.retries + 1):
            entry, worst = self.guard.reserve(system + user, max_out, ledger_run)
            t0 = time.perf_counter()
            try:
                resp = self.client.messages.create(**kw)
            except Exception as exc:  # billing for failed attempts is not assumed to be zero: keep reservation charged
                status = getattr(exc, "status_code", None)
                kind = type(exc).__name__
                self.guard.charge_failed(entry, f"attempt {attempt}: {kind}" + (f" HTTP {status}" if status else ""))
                last = RuntimeError(self._redact(f"Anthropic request failed ({kind}{f' HTTP {status}' if status else ''}): "
                                                 f"{str(exc)[:200]}"))
                if status in RETRY_STATUSES or kind in RETRY_EXCEPTIONS:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                raise last from None
            latency = time.perf_counter() - t0
            return self._settle(resp, entry, worst, max_out, latency, system + user)
        raise last

    def _settle(self, resp, entry, worst, max_out, latency, prompt) -> LLMResponse:
        u = getattr(resp, "usage", None)
        tin = getattr(u, "input_tokens", None) if u is not None else None
        tout = getattr(u, "output_tokens", None) if u is not None else None
        cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0) if u is not None else 0
        cr = int(getattr(u, "cache_read_input_tokens", 0) or 0) if u is not None else 0
        split = getattr(u, "cache_creation", None) if u is not None else None
        cw1h = int(getattr(split, "ephemeral_1h_input_tokens", 0) or 0) if split is not None else (
            cw if self.cfg.get("prompt_cache") == "1h" else 0)
        cw5m = cw - cw1h
        stop = getattr(resp, "stop_reason", None)
        actual = self.pricing.cost_usage(tin, tout, cw5m, cw1h, cr) if tin is not None and tout is not None else None
        note = f"stop_reason={stop}; cache_write={cw}; cache_read={cr}; request_id={getattr(resp, '_request_id', None)}"
        self.guard.settle(entry, actual, note)
        text = "".join(getattr(b, "text", "") for b in (getattr(resp, "content", None) or []) if getattr(b, "type", "") == "text")
        data = parse_json_text(text) if stop not in ("refusal",) else None
        total_in = (tin or self.pricing.worst_case_input_tokens(prompt)) + cw + cr
        r = LLMResponse(text, data, total_in, tout if tout is not None else max_out, tin is None or tout is None,
                        getattr(resp, "model", None) or self.model, self.name, latency, estimated_cost_usd=worst,
                        actual_cost_usd=actual)
        r.stop_reason, r.cache_write_tokens, r.cache_read_tokens = stop, cw, cr
        r.request_id = getattr(resp, "_request_id", None)
        details = getattr(resp, "stop_details", None)
        r.refusal_category = getattr(details, "category", None) if stop == "refusal" else None
        return r
