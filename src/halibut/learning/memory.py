"""Shared learning memory: experiments, knowledge, links, evaluations, promotions.

Trust rules (enforced here, not by convention):
- Anything produced by an agent enters as 'proposed' (or 'question'/'critique').
- Status 'validated' requires an evaluation with decision 'eligible_for_promotion'
  whose candidate experiment is linked to the knowledge item.
- Knowledge never becomes an observation: this module cannot write to the
  observations table.
- Disagreement is preserved: contradicting items coexist, linked by
  'contradicts'; nothing is deleted, superseded items stay retrievable.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from ..config import ConfigError
from ..data.store import Store, utc_now

KNOWLEDGE_KINDS = ("hypothesis", "prediction", "outcome", "lesson", "critique", "question", "proposal")
STATUSES = ("proposed", "tested", "validated", "rejected", "inconclusive", "superseded")
AGENT_ALLOWED_INITIAL = ("proposed",)


def _id(prefix: str, *parts) -> str:
    return f"{prefix}:" + hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


class TrustViolation(ConfigError):
    pass


@dataclass
class LearningMemory:
    store: Store

    # ------------------------------------------------------------------ experiments
    def record_experiment(self, kind: str, title: str, produced_by: str, lineage: dict, *, baseline_id: str | None = None,
                          rationale: str | None = None, expected_improvement: dict | None = None,
                          change: dict | None = None, status: str = "running") -> str:
        eid = _id("exp", kind, title, produced_by, lineage, baseline_id, change, uuid.uuid4().hex)  # events are unique
        self.store.conn.execute(
            """INSERT INTO experiments (experiment_id, kind, title, status, produced_by, baseline_id, rationale,
               expected_improvement_json, change_json, lineage_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, kind, title, status, produced_by, baseline_id, rationale,
             json.dumps(expected_improvement) if expected_improvement else None,
             json.dumps(change) if change else None, json.dumps(lineage, sort_keys=True), utc_now()))
        self.store.conn.commit()
        return eid

    def finish_experiment(self, eid: str, metrics: dict, status: str = "completed") -> None:
        """Failed experiments are first-class: record them with status 'failed' and their error."""
        self.store.conn.execute("UPDATE experiments SET status=?, metrics_json=?, finished_at=? WHERE experiment_id=?",
                                (status, json.dumps(metrics, sort_keys=True, default=float), utc_now(), eid))
        self.store.conn.commit()

    def experiment(self, eid: str) -> dict:
        r = self.store.conn.execute("SELECT * FROM experiments WHERE experiment_id=?", (eid,)).fetchone()
        if r is None:
            raise KeyError(eid)
        d = dict(r)
        for k in ("lineage_json", "metrics_json", "change_json", "expected_improvement_json"):
            d[k[:-5]] = json.loads(d.pop(k)) if d.get(k) else None
        return d

    # ------------------------------------------------------------------ knowledge
    def submit(self, kind: str, statement: str, produced_by: str, scope: dict, evidence: list[str], *,
               experiment_id: str | None = None, confidence: str | None = None, record: dict | None = None,
               status: str = "proposed", available_at: str | None = None) -> str:
        if kind not in KNOWLEDGE_KINDS:
            raise ConfigError(f"unknown knowledge kind {kind!r}")
        if produced_by.startswith("agent:") and status not in AGENT_ALLOWED_INITIAL:
            raise TrustViolation(f"agents may only submit 'proposed' knowledge (got {status!r})")
        if status == "validated":
            raise TrustViolation("knowledge cannot be created as validated; use apply_evaluation()")
        kid = _id("k", kind, statement, produced_by, scope, experiment_id)
        now = utc_now()
        self.store.conn.execute(
            """INSERT OR IGNORE INTO knowledge (knowledge_id, kind, statement, status, produced_by, experiment_id,
               scope_json, evidence_json, confidence, created_at, updated_at, record_json, available_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (kid, kind, statement, status, produced_by, experiment_id, json.dumps(scope, sort_keys=True),
             json.dumps(sorted(evidence)), confidence, now, now, json.dumps(record or {}, sort_keys=True, default=str),
             available_at))
        if self.store.conn.execute("SELECT 1 FROM knowledge_status_history WHERE knowledge_id=?", (kid,)).fetchone() is None:
            self.store.conn.execute("INSERT INTO knowledge_status_history VALUES (?,?,?,?,?,?,?)",
                                    (kid, status, available_at or now, now, produced_by, None, "created"))
        self.store.conn.commit()
        return kid

    def link(self, from_id: str, relation: str, to_id: str, produced_by: str, note: str | None = None) -> None:
        self.store.conn.execute("INSERT OR IGNORE INTO knowledge_links VALUES (?,?,?,?,?,?)",
                                (from_id, relation, to_id, produced_by, note, utc_now()))
        self.store.conn.commit()

    def set_status(self, kid: str, status: str, by: str, evaluation_id: str | None = None,
                   effective_at: str | None = None) -> None:
        """Change status. ``effective_at`` is when the change became knowable in data time (e.g. the
        publication date of the outcome that tested it); it defaults to now. Replays see status as of
        their simulated date via the status history."""
        if status not in STATUSES:
            raise ConfigError(f"unknown status {status!r}")
        if status == "validated":
            if not evaluation_id:
                raise TrustViolation("validation requires an evaluation")
            ev = self.store.conn.execute("SELECT decision, candidate_id FROM evaluations WHERE evaluation_id=?",
                                         (evaluation_id,)).fetchone()
            k = self.store.conn.execute("SELECT experiment_id FROM knowledge WHERE knowledge_id=?", (kid,)).fetchone()
            if ev is None or ev["decision"] != "eligible_for_promotion":
                raise TrustViolation("only an evaluation with decision 'eligible_for_promotion' can validate knowledge")
            if k is None or k["experiment_id"] != ev["candidate_id"]:
                raise TrustViolation("the evaluation must concern the experiment the knowledge belongs to")
        from ..data.store import to_timestamp  # noqa: PLC0415
        self.store.conn.execute("UPDATE knowledge SET status=?, evaluation_id=COALESCE(?, evaluation_id), updated_at=? WHERE knowledge_id=?",
                                (status, evaluation_id, utc_now(), kid))
        self.store.conn.execute("INSERT INTO knowledge_status_history VALUES (?,?,?,?,?,?,?)",
                                (kid, status, to_timestamp(effective_at) if effective_at else utc_now(), utc_now(), by,
                                 evaluation_id, "explicit" if effective_at else "recorded_now"))
        self.link(kid, "tests" if evaluation_id else "derived_from", evaluation_id or kid, by, f"status -> {status}")
        self.store.conn.commit()

    def retrieve(self, *, tags: list[str] | None = None, kinds: list[str] | None = None,
                 statuses: list[str] | None = None, limit: int = 50, as_of: str | None = None,
                 exclude_tags: list[str] | None = None) -> list[dict]:
        """Lessons and other knowledge whose scope tags overlap ``tags``. Includes
        contradicting and superseded items, with their links, so disagreement is visible.

        ``as_of`` (historical replay): only knowledge whose ``available_at`` (the
        availability of the data it rests on; else its creation time) is on or
        before ``as_of``. Status changes are not time-stamped per as-of yet, so
        replays use the status recorded with the item's availability."""
        from ..data.store import to_timestamp  # noqa: PLC0415
        rows = self.store.conn.execute("SELECT * FROM knowledge ORDER BY created_at DESC").fetchall()
        cutoff = to_timestamp(as_of) if as_of else None
        out = []
        for r in rows:
            if cutoff and (r["available_at"] or r["created_at"]) > cutoff:
                continue
            status_now = r["status"]
            if cutoff:
                h = self.store.conn.execute("""SELECT status FROM knowledge_status_history WHERE knowledge_id=? AND
                    effective_at<=? ORDER BY effective_at DESC, rowid DESC LIMIT 1""", (r["knowledge_id"], cutoff)).fetchone()
                status_now = h["status"] if h else "proposed"
            scope = json.loads(r["scope_json"])
            if tags and not set(tags) & set(scope.get("tags", [])):
                continue
            if exclude_tags and set(exclude_tags) & set(scope.get("tags", [])):
                continue
            if kinds and r["kind"] not in kinds:
                continue
            if statuses and status_now not in statuses:
                continue
            d = dict(r)
            d["status"] = status_now
            if cutoff and status_now != r["status"]:
                d["status_now"] = r["status"]
            d["scope"], d["evidence"] = scope, json.loads(r["evidence_json"])
            d["record"] = json.loads(r["record_json"])
            d["links"] = [dict(x) for x in self.store.conn.execute(
                "SELECT * FROM knowledge_links WHERE from_id=? OR to_id=?", (r["knowledge_id"], r["knowledge_id"]))]
            for k in ("scope_json", "evidence_json", "record_json"):
                d.pop(k)
            out.append(d)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ evaluations & promotion
    def record_evaluation(self, candidate_id: str, baseline_id: str, rule: dict, result: str, decision: str,
                          details: dict, decided_by: str) -> str:
        evid = _id("eval", candidate_id, baseline_id, rule, uuid.uuid4().hex)
        self.store.conn.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?)",
                                (evid, candidate_id, baseline_id, json.dumps(rule, sort_keys=True), result, decision,
                                 json.dumps(details, sort_keys=True, default=float), utc_now(), decided_by))
        self.store.conn.commit()
        return evid

    def promote(self, component: str, evaluation_id: str, approved_by: str, note: str = "") -> int:
        """Activate a candidate for ``component``. Requires an eligible evaluation
        and an explicit approver; never called automatically by the engine."""
        if not approved_by or approved_by.startswith("agent:") or approved_by.startswith("engine:"):
            raise TrustViolation("promotion requires explicit approval by a person (approved_by)")
        ev = self.store.conn.execute("SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
        if ev is None or ev["decision"] != "eligible_for_promotion":
            raise TrustViolation("evaluation is not eligible for promotion")
        prev = self.active(component)
        if prev:
            self.store.conn.execute("UPDATE promotions SET status='rolled_back' WHERE promotion_id=?", (prev["promotion_id"],))
        cur = self.store.conn.execute(
            "INSERT INTO promotions (component, experiment_id, evaluation_id, status, approved_by, previous_promotion_id, at, note) VALUES (?,?,?,?,?,?,?,?)",
            (component, ev["candidate_id"], evaluation_id, "active", approved_by, prev["promotion_id"] if prev else None, utc_now(), note))
        self.store.conn.commit()
        return int(cur.lastrowid)

    def rollback(self, component: str, approved_by: str) -> dict | None:
        cur = self.active(component)
        if not cur:
            return None
        self.store.conn.execute("UPDATE promotions SET status='rolled_back' WHERE promotion_id=?", (cur["promotion_id"],))
        if cur["previous_promotion_id"]:
            self.store.conn.execute("UPDATE promotions SET status='active' WHERE promotion_id=?", (cur["previous_promotion_id"],))
        self.store.conn.execute(
            "INSERT INTO knowledge_links VALUES (?,?,?,?,?,?)",
            (f"promotion:{cur['promotion_id']}", "supersedes", f"promotion:{cur['previous_promotion_id']}", approved_by,
             "rollback", utc_now()))
        self.store.conn.commit()
        return self.active(component)

    def active(self, component: str) -> dict | None:
        r = self.store.conn.execute("SELECT * FROM promotions WHERE component=? AND status='active' ORDER BY promotion_id DESC LIMIT 1",
                                    (component,)).fetchone()
        return dict(r) if r else None
