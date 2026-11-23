"""Polite, budgeted HTTP client shared by all intelligence providers.

Every request is:
- identified with Halibut's project User-Agent (no personal contact data);
- spaced by the provider's minimum interval;
- checked against hard budgets (requests, bytes, wall-clock seconds) that
  raise ``BudgetExhausted`` *before* exceeding them;
- logged to the store's ``fetch_log`` (url, status, bytes, seconds, outcome).

A ``transport`` callable can replace the network for offline tests.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from ..version import ENGINE_VERSION
from .store import utc_now

PROJECT_URL = "https://github.com/michaelyvesassets/Halibut"
USER_AGENT = f"halibut-research-client/{ENGINE_VERSION} (+{PROJECT_URL})"


class BudgetExhausted(RuntimeError):
    pass


class ProviderError(RuntimeError):
    pass


@dataclass
class Budget:
    max_requests: int = 50
    max_bytes: int = 50_000_000
    max_seconds: float = 600.0
    requests: int = 0
    bytes: int = 0
    started: float = field(default_factory=time.monotonic)

    def check(self, expected_bytes: int = 0) -> None:
        if self.requests >= self.max_requests:
            raise BudgetExhausted(f"request budget exhausted ({self.max_requests})")
        if self.bytes + expected_bytes > self.max_bytes:
            raise BudgetExhausted(f"byte budget exhausted ({self.bytes}/{self.max_bytes})")
        if time.monotonic() - self.started > self.max_seconds:
            raise BudgetExhausted(f"time budget exhausted ({self.max_seconds:.0f}s)")

    def as_dict(self) -> dict:
        return {"requests": self.requests, "max_requests": self.max_requests, "bytes": self.bytes,
                "max_bytes": self.max_bytes, "seconds": round(time.monotonic() - self.started, 1),
                "max_seconds": self.max_seconds}


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    headers: dict


def urllib_transport(method: str, url: str, data: bytes | None, headers: dict, timeout: float) -> Response:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(r.geturl(), r.status, r.read(), dict(r.headers))
    except urllib.error.HTTPError as exc:
        return Response(url, exc.code, exc.read() or b"", dict(exc.headers or {}))


class PoliteClient:
    RETRY_STATUSES = (429, 500, 502, 503, 504)

    def __init__(self, provider: str, budget: Budget, store=None, min_interval: float = 1.0,
                 retries: int = 2, timeout: float = 120.0, transport=None, sleep=time.sleep, run_id=None):
        self.provider, self.budget, self.store = provider, budget, store
        self.min_interval, self.retries, self.timeout = min_interval, retries, timeout
        self.transport = transport or urllib_transport
        self._sleep, self._last, self.run_id = sleep, 0.0, run_id

    def _log(self, method, url, status, nbytes, seconds, outcome):
        if self.store is not None:
            self.store.conn.execute(
                "INSERT INTO fetch_log (provider, method, url, status, bytes, seconds, at, outcome, run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (self.provider, method, url[:2000], status, nbytes, seconds, utc_now(), outcome, self.run_id))
            self.store.conn.commit()

    def request(self, method: str, url: str, params: dict | None = None, data: bytes | None = None,
                headers: dict | None = None, expected_bytes: int = 0, ok=(200,)) -> Response:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        last = None
        for attempt in range(self.retries + 1):
            self.budget.check(expected_bytes)
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            t0 = time.monotonic()
            try:
                resp = self.transport(method, url, data, hdrs, self.timeout)
            except Exception as exc:  # network failure
                self._last = time.monotonic()
                self.budget.requests += 1
                self._log(method, url, None, 0, time.monotonic() - t0, f"error: {exc}"[:300])
                last = ProviderError(f"{self.provider}: {exc}")
                self._sleep(self.min_interval * (2 ** attempt))
                continue
            self._last = time.monotonic()
            self.budget.requests += 1
            self.budget.bytes += len(resp.body)
            outcome = "ok" if resp.status in ok else f"http {resp.status}"
            self._log(method, url, resp.status, len(resp.body), time.monotonic() - t0, outcome)
            if resp.status in ok:
                return resp
            last = ProviderError(f"{self.provider}: HTTP {resp.status} for {url[:160]}: {resp.body[:200]!r}")
            if resp.status not in self.RETRY_STATUSES:
                break
            self._sleep(max(self.min_interval, 5.0) * (2 ** attempt))
        raise last

    def get(self, url, **kw) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url, data: bytes, **kw) -> Response:
        return self.request("POST", url, data=data, **kw)
