"""Approvals record and authorization checks (L8 control plane, R2).

An approval is a REQUEST (``approvals``) plus append-only DECISIONS (``approval_decisions``). Rules:

- Only a named person decides: ``decided_by`` must not be empty, ``agent:...`` or ``engine:...``, and must
  differ from the requester.
- Agents may not even REQUEST a paid run or an external action. A generated strategy therefore can never
  lead to a paid request or an external action without a person asking for it and a person granting it.
- Granting an ``experiment`` or ``external_action`` needs a legal/compliance review reference and
  measurable outcomes in the decision evidence. Granting a ``promotion`` needs an evaluation id.
  Granting a ``paid_run`` needs the request to state ``max_usd``.
- ``require()`` is the authorization check used at the point of action: the latest decision must be
  'granted', unexpired, for the same kind and subject, and the cost must not exceed ``max_usd``.
  Halibut itself has no code path that executes external actions; 'external_action' approvals exist so
  that a human-executed action is recorded with its authorization.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from ..data.store import to_timestamp, utc_now
from ..epistemic.events import EventLog

KINDS = ("paid_run", "experiment", "external_action", "promotion", "new_source")
AGENT_FORBIDDEN_REQUESTS = ("paid_run", "external_action")


class AuthorizationError(PermissionError):
    pass


def _is_person(name: str | None) -> bool:
    return bool(name) and not name.startswith(("agent:", "engine:")) and name.strip() == name and len(name) <= 80


def request(store, kind: str, subject: str, requested_by: str, *, max_usd: float | None = None,
            expires_at: str | None = None, payload: dict | None = None, tenant: str = "public") -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown approval kind {kind!r}")
    if not requested_by:
        raise AuthorizationError("requested_by is required")
    if requested_by.startswith("agent:") and kind in AGENT_FORBIDDEN_REQUESTS:
        raise AuthorizationError(f"agents may not request a {kind} (a generated strategy cannot authorize spending or actions)")
    if kind == "paid_run" and (max_usd is None or max_usd <= 0):
        raise AuthorizationError("a paid_run request must state max_usd > 0")
    now = utc_now()
    aid = "ap:" + hashlib.sha256(json.dumps([kind, subject, requested_by, now, payload or {}, uuid.uuid4().hex],
                                            sort_keys=True, default=str).encode()).hexdigest()[:16]
    store.conn.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?)",
                       (aid, kind, subject, tenant, requested_by, now, max_usd,
                        to_timestamp(expires_at) if expires_at else None, json.dumps(payload or {}, sort_keys=True, default=str)))
    EventLog(store).append("approval.requested", "L8", {"kind": kind, "subject": subject, "max_usd": max_usd},
                           subject_id=aid, provenance={"requested_by": requested_by}, tenant=tenant)
    store.conn.commit()
    return aid


def get(store, approval_id: str) -> dict | None:
    r = store.conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    if r is None:
        return None
    d = store.conn.execute("SELECT * FROM approval_decisions WHERE approval_id=? ORDER BY rowid DESC LIMIT 1",
                           (approval_id,)).fetchone()
    status = d["decision"] if d else "requested"
    if status == "granted" and r["expires_at"] and r["expires_at"] < utc_now():
        status = "expired"
    return {**{k: r[k] for k in r.keys() if k != "payload_json"}, "payload": json.loads(r["payload_json"]), "status": status,
            "decided_by": d["decided_by"] if d else None, "decided_at": d["decided_at"] if d else None,
            "decision_evidence": json.loads(d["evidence_json"]) if d else None}


def decide(store, approval_id: str, decision: str, decided_by: str, *, note: str = "", evidence: dict | None = None) -> dict:
    a = get(store, approval_id)
    if a is None:
        raise KeyError(approval_id)
    if decision not in ("granted", "denied", "revoked"):
        raise ValueError("decision must be granted, denied or revoked")
    if not _is_person(decided_by):
        raise AuthorizationError("approval decisions require a named person (not an agent or the engine)")
    if decided_by == a["requested_by"]:
        raise AuthorizationError("the requester cannot approve their own request")
    ev = evidence or {}
    if decision == "granted":
        if a["kind"] in ("experiment", "external_action"):
            if not ev.get("legal_review_ref"):
                raise AuthorizationError(f"granting a {a['kind']} requires a legal/compliance review reference")
            if not ev.get("measurable_outcomes"):
                raise AuthorizationError(f"granting a {a['kind']} requires measurable outcomes")
        if a["kind"] == "promotion" and not ev.get("evaluation_id"):
            raise AuthorizationError("granting a promotion requires the eligible evaluation id")
        if a["status"] == "granted":
            raise AuthorizationError("already granted")
    store.conn.execute("INSERT INTO approval_decisions VALUES (?,?,?,?,?,?)",
                       (approval_id, decision, decided_by, utc_now(), note, json.dumps(ev, sort_keys=True, default=str)))
    EventLog(store).append(f"approval.{decision}", "L8", {"kind": a["kind"], "subject": a["subject"], "note": note},
                           subject_id=approval_id, provenance={"decided_by": decided_by}, tenant=a["tenant"])
    store.conn.commit()
    return get(store, approval_id)


def require(store, approval_id: str | None, kind: str, subject: str, cost_usd: float | None = None) -> dict:
    """Authorization check at the point of action. Raises AuthorizationError unless properly granted."""
    if not approval_id:
        raise AuthorizationError(f"a granted {kind} approval is required (none given)")
    a = get(store, approval_id)
    if a is None:
        raise AuthorizationError(f"approval {approval_id} does not exist")
    if a["kind"] != kind or a["subject"] != subject:
        raise AuthorizationError(f"approval {approval_id} is for {a['kind']} {a['subject']!r}, not {kind} {subject!r}")
    if a["status"] != "granted":
        raise AuthorizationError(f"approval {approval_id} is {a['status']}")
    if cost_usd is not None and (a["max_usd"] is None or cost_usd > a["max_usd"] + 1e-12):
        raise AuthorizationError(f"cost ${cost_usd:.4f} exceeds the approved ${a['max_usd']}")
    return a


def list_all(store, status: str | None = None) -> list[dict]:
    out = [get(store, r[0]) for r in store.conn.execute("SELECT approval_id FROM approvals ORDER BY requested_at")]
    return [a for a in out if status is None or a["status"] == status]
