"""Fail-closed LLM spend guard backed by the shared store's ``llm_spend_ledger``.

Rules:
- Before EVERY attempt (including retries) a worst-case cost is RESERVED:
  input tokens estimated conservatively (characters / 3, x1.1, +64) plus the
  full ``max_output_tokens`` at the output price.
- A reservation is refused if committed spend + outstanding reservations +
  this reservation would exceed the total cap (all runs, all processes) or the
  scope cap (e.g. one replay). The check-and-insert runs inside one SQLite
  ``BEGIN IMMEDIATE`` transaction, so concurrent processes cannot overspend.
- After a response, the reservation is settled at the cost computed from the
  provider's reported usage. If usage is missing, the reserved worst case is
  kept as the charge.
- A failed or interrupted attempt is kept as charged at its reservation
  (``failed_charged``): billing for failed requests is not assumed to be zero.
- Missing prices or caps -> refuse (fail closed).
"""

from __future__ import annotations

import math

from ..data.store import utc_now
from .llm import Pricing, SpendLimitReached


def conservative_input_tokens(text: str) -> int:
    return math.ceil(len(text) / 3 * 1.1) + 64


class SpendGuard:
    def __init__(self, store, provider: str, model: str, pricing: Pricing, total_cap_usd: float, scope_cap_usd: float,
                 scope: str):
        if pricing.input_per_mtok is None or pricing.output_per_mtok is None:
            raise SpendLimitReached("prices are not configured: refusing (fail closed)")
        if not (total_cap_usd and scope_cap_usd) or total_cap_usd <= 0 or scope_cap_usd <= 0:
            raise SpendLimitReached("spend caps are not configured: refusing (fail closed)")
        self.store, self.provider, self.model, self.pricing = store, provider, model, pricing
        self.total_cap, self.scope_cap, self.scope = float(total_cap_usd), float(scope_cap_usd), scope

    def _committed(self, scope: str | None = None) -> float:
        q = """SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_usd ELSE reserved_usd END), 0)
               FROM llm_spend_ledger WHERE provider=? AND status IN ('reserved','settled','failed_charged')"""
        args = [self.provider]
        if scope:
            q += " AND run_id LIKE ?"
            args.append(scope + "%")
        return float(self.store.conn.execute(q, args).fetchone()[0])

    def committed_total(self) -> float:
        return self._committed()

    def committed_scope(self) -> float:
        return self._committed(self.scope)

    def worst_case(self, prompt_text: str, max_output_tokens: int) -> float:
        return self.pricing.cost(conservative_input_tokens(prompt_text), max_output_tokens)

    def reserve(self, prompt_text: str, max_output_tokens: int, run_id: str) -> tuple[int, float]:
        worst = self.worst_case(prompt_text, max_output_tokens)
        conn = self.store.conn
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            total, scoped = self.committed_total(), self.committed_scope()
            if total + worst > self.total_cap:
                raise SpendLimitReached(f"total cap ${self.total_cap:.2f}: committed ${total:.4f} + worst case ${worst:.4f}")
            if scoped + worst > self.scope_cap:
                raise SpendLimitReached(f"scope cap ${self.scope_cap:.2f} for {self.scope}: committed ${scoped:.4f} + worst case ${worst:.4f}")
            cur = conn.execute("""INSERT INTO llm_spend_ledger (provider, model, run_id, reserved_usd, status, created_at)
                                  VALUES (?,?,?,?, 'reserved', ?)""", (self.provider, self.model, run_id, worst, utc_now()))
            conn.execute("COMMIT")
            return int(cur.lastrowid), worst
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def settle(self, entry_id: int, actual_usd: float | None, note: str = "") -> None:
        if actual_usd is None:  # no usage reported: keep the worst case
            self.store.conn.execute("""UPDATE llm_spend_ledger SET status='settled', actual_usd=reserved_usd, settled_at=?,
                                       note=? WHERE entry_id=?""", (utc_now(), "usage missing: charged at reservation; " + note, entry_id))
        else:
            self.store.conn.execute("UPDATE llm_spend_ledger SET status='settled', actual_usd=?, settled_at=?, note=? WHERE entry_id=?",
                                    (actual_usd, utc_now(), note, entry_id))
        self.store.conn.commit()

    def charge_failed(self, entry_id: int, note: str) -> None:
        self.store.conn.execute("UPDATE llm_spend_ledger SET status='failed_charged', settled_at=?, note=? WHERE entry_id=?",
                                (utc_now(), note[:300], entry_id))
        self.store.conn.commit()

    def summary(self) -> dict:
        return {"total_cap_usd": self.total_cap, "committed_total_usd": round(self.committed_total(), 6),
                "remaining_total_usd": round(self.total_cap - self.committed_total(), 6),
                "scope": self.scope, "scope_cap_usd": self.scope_cap, "committed_scope_usd": round(self.committed_scope(), 6)}
