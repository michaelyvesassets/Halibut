"""Approvals record and authorization checks (L8 control plane, R2).

An approval is a REQUEST (``approvals``) plus append-only DECISIONS (``approval_decisions``). Rules:

- Only a named person decides: ``decided_by`` must not be empty, ``agent:...`` or ``engine:...``, and must
  differ from the requester, EXCEPT under the sole-operator policy (``configs/governance/approvals.toml``,
  ADR-019): the configured operator may self-approve a ``paid_run`` up to the policy ceiling, with a written
  attestation; the decision records the policy version and is flagged ``self_approved_under_policy``.
- Agents may not even REQUEST a paid run or an external action. A generated strategy therefore can never
  lead to a paid request or an external action without a person asking for it and a person granting it.
- Granting an ``experiment`` or ``external_action`` needs a legal/compliance review reference and
  measurable outcomes in the decision evidence. Granting a ``promotion`` needs an evaluation id.
  Granting a ``paid_run`` needs the request to state ``max_usd``.
- A ``paid_run`` approval is SINGLE-USE: ``claim_use()`` binds it to one spend scope (one experiment or one
  investigation) in the append-only ``approval_uses`` table; a second use is refused, and ``require(use_id=...)``
  refuses any other scope. The spend guard then caps all attempts (smoke, retries) under that one scope.
- ``require()`` is the authorization check used at the point of action: the latest decision must be
  'granted', unexpired, for the same kind and subject, and the cost must not exceed ``max_usd``.
  Halibut itself has no code path that executes external actions; 'external_action' approvals exist so
  that a human-executed action is recorded with its authorization.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from ..data.store import to_timestamp, utc_now
from ..epistemic.events import EventLog

KINDS = ("paid_run", "experiment", "external_action", "promotion", "new_source")
POLICY = Path(__file__).resolve().parents[3] / "configs" / "governance" / "approvals.toml"


def load_policy(path=POLICY) -> dict:
    from ..config import load_raw  # noqa: PLC0415
    raw = load_raw(path)
    if raw.get("schema_version") != "1.0" or not raw.get("policy_version"):
        raise ValueError("approvals policy: schema_version '1.0' and policy_version are required")
    so = raw.get("sole_operator", {})
    return {"policy_version": raw["policy_version"], "sole_operator": {
        "enabled": bool(so.get("enabled", False)), "operator": so.get("operator", ""),
        "allowed_kinds": tuple(so.get("allowed_kinds", ())), "max_usd_per_approval": float(so.get("max_usd_per_approval", 0)),
        "attestation_min_chars": int(so.get("attestation_min_chars", 40))}}
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
    u = store.conn.execute("SELECT use_id, purpose, used_at FROM approval_uses WHERE approval_id=?", (approval_id,)).fetchone()
    return {**{k: r[k] for k in r.keys() if k != "payload_json"}, "payload": json.loads(r["payload_json"]), "status": status,
            "used_by": u["use_id"] if u else None, "used_for": u["purpose"] if u else None, "used_at": u["used_at"] if u else None,
            "decided_by": d["decided_by"] if d else None, "decided_at": d["decided_at"] if d else None,
            "decision_evidence": json.loads(d["evidence_json"]) if d else None}


def decide(store, approval_id: str, decision: str, decided_by: str, *, note: str = "", evidence: dict | None = None,
           policy: dict | None = None) -> dict:
    a = get(store, approval_id)
    if a is None:
        raise KeyError(approval_id)
    if decision not in ("granted", "denied", "revoked"):
        raise ValueError("decision must be granted, denied or revoked")
    if not _is_person(decided_by):
        raise AuthorizationError("approval decisions require a named person (not an agent or the engine)")
    ev = dict(evidence or {})
    if decided_by == a["requested_by"] and decision == "granted":
        pol = policy or load_policy()
        so = pol["sole_operator"]
        why = None
        if not so["enabled"]:
            why = "sole-operator mode is disabled"
        elif decided_by != so["operator"]:
            why = f"only the configured sole operator ({so['operator']!r}) may self-approve"
        elif a["kind"] not in so["allowed_kinds"]:
            why = f"self-approval is not allowed for {a['kind']}"
        elif a["max_usd"] is None or a["max_usd"] > so["max_usd_per_approval"]:
            why = f"self-approval is limited to ${so['max_usd_per_approval']} (requested ${a['max_usd']})"
        elif len((ev.get("attestation") or "").strip()) < so["attestation_min_chars"]:
            why = f"self-approval needs a written attestation of at least {so['attestation_min_chars']} characters"
        if why:
            raise AuthorizationError(f"the requester cannot approve their own request: {why}")
        ev.update({"self_approved_under_policy": True, "policy_version": pol["policy_version"]})
    elif decided_by == a["requested_by"] and decision != "revoked":
        raise AuthorizationError("the requester cannot decide their own request")
    if decision == "granted":
        if a["kind"] in ("experiment", "external_action"):
            if (a["payload"] or {}).get("eligible_for_execution") is not True:  # absent counts as not eligible
                raise AuthorizationError(f"this {a['kind']} is not eligible for execution: "
                                         f"{(a['payload'] or {}).get('eligibility_reasons') or 'no eligibility assessment'}")
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
    EventLog(store).append(f"approval.{decision}", "L8", {"kind": a["kind"], "subject": a["subject"], "note": note,
                                                         "self_approved_under_policy": bool(ev.get("self_approved_under_policy")),
                                                         "policy_version": ev.get("policy_version")},
                           subject_id=approval_id, provenance={"decided_by": decided_by}, tenant=a["tenant"])
    store.conn.commit()
    return get(store, approval_id)


def claim_use(store, approval_id: str, use_id: str, purpose: str) -> dict:
    """Bind a granted paid_run approval to its one use (append-only). Raises if it was already used."""
    import sqlite3  # noqa: PLC0415
    a = get(store, approval_id)
    if a is None or a["status"] != "granted":
        raise AuthorizationError(f"approval {approval_id} is not granted")
    try:
        store.conn.execute("INSERT INTO approval_uses VALUES (?,?,?,?)", (approval_id, use_id, purpose, utc_now()))
    except sqlite3.IntegrityError:
        raise AuthorizationError(f"approval {approval_id} was already used by {a['used_by']} ({a['used_for']}); "
                                 "approvals are single-use") from None
    EventLog(store).append("approval.used", "L8", {"use_id": use_id, "purpose": purpose}, subject_id=approval_id)
    store.conn.commit()
    return get(store, approval_id)


def require(store, approval_id: str | None, kind: str, subject: str, cost_usd: float | None = None,
            use_id: str | None = None) -> dict:
    """Authorization check at the point of action. Raises AuthorizationError unless properly granted. With ``use_id``,
    an approval already bound to a different use is refused (single use)."""
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
    if use_id is not None and a["used_by"] is not None and a["used_by"] != use_id:
        raise AuthorizationError(f"approval {approval_id} is already bound to {a['used_by']}; approvals are single-use")
    return a


def list_all(store, status: str | None = None) -> list[dict]:
    out = [get(store, r[0]) for r in store.conn.execute("SELECT approval_id FROM approvals ORDER BY requested_at")]
    return [a for a in out if status is None or a["status"] == status]
