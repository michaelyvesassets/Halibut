"""Typed claim ledger (``claims``, ``claim_links``, ``claim_status_history``; migration 6).

Every significant output is a claim of exactly one type (Master Architecture v1.0 §4). A claim is
validated by the rules of its type before it is recorded; invalid claims are not stored as claims
but as ``validation_records`` (stage 'ledger') plus a ``claim.rejected`` event.

Type rules enforced here (v1):
- forecast          only deterministic producers (never ``agent:``): registered model id/version,
                    point inside a valid 80% interval, a resolution rule resolving AFTER as-of.
- estimate          value + uncertainty + method; agent-produced estimates must be labelled
                    ``candidate = true`` (AI candidates, evaluated separately from forecasts).
- hypothesis        evidence, observable implications, refutation criteria, origin; no point
                    forecast inside a hypothesis.
- explanation       evidence-cited interpretation of the past; no forecast fields.
- simulated_outcome conditional on a scenario with assumptions; never carries a resolution rule
                    (so it can never be scored as a forecast).
- strategic_proposal lawful levers only, no prohibited tactics, approval and compliance review
                    required.
- observation / derived_metric  never produced by agents.
All types: cited evidence must exist and be available at ``as_of`` (when an ``exists`` check is
given); ``available_at >= as_of``; agents may only create 'proposed' claims; rows are immutable.
"""

from __future__ import annotations

import hashlib
import json
import re

from ..data.store import to_timestamp, utc_now
from .events import EventLog

SCHEMA_VERSION = "claim.v1"
CLAIM_TYPES = ("observation", "derived_metric", "estimate", "assumption", "explanation", "hypothesis", "forecast",
               "simulated_outcome", "critique", "strategic_proposal", "experiment_result", "lesson")
INITIAL_STATUS = {"observation": "recorded", "derived_metric": "recorded", "forecast": "open", "simulated_outcome": "recorded",
                  "experiment_result": "recorded"}
STATUSES = ("proposed", "recorded", "open", "tested", "survived", "refuted", "inconclusive", "untested", "validated",
            "rejected", "superseded", "scored")
RELATIONS = ("supports", "contradicts", "derived_from", "tests", "critiques", "supersedes", "duplicates", "explains")
FORECAST_FIELDS = ("point", "interval80", "resolution")
TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class ClaimRejected(ValueError):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def _is_agent(produced_by: str) -> bool:
    return produced_by.startswith("agent:")


def identity(claim_type: str, as_of: str, payload: dict) -> dict:
    """The content that makes two claims 'the same claim' (used for duplicate detection in evaluation)."""
    if claim_type == "forecast":
        return {"as_of": as_of, "model": payload.get("model"), "quantity": (payload.get("resolution") or {}).get("quantity"),
                "point": payload.get("point"), "interval80": payload.get("interval80")}
    return {"as_of": as_of, "payload": payload}


def _check_type(t: str, statement: str, produced_by: str, as_of: str, payload: dict, evidence: list) -> list[str]:
    from ..validation import ALLOWED_LEVERS, PROHIBITED  # noqa: PLC0415
    p = []
    agent = _is_agent(produced_by)
    forecast_like = [k for k in FORECAST_FIELDS if k in payload]
    if t in ("observation", "derived_metric") and agent:
        p.append(f"{t} claims cannot be produced by agents")
    if t == "observation" and not str(payload.get("evidence_id", "")).split(":", 1)[0] in ("obs", "rel", "doc", "ent"):
        p.append("observation claims must reference an obs:/rel:/doc:/ent: evidence id")
    if t == "derived_metric" and not (payload.get("definition") and payload.get("inputs") is not None):
        p.append("derived_metric needs a definition and its input ids")
    if t == "forecast":
        if agent:
            p.append("agents may not issue official forecasts (ADR-004): submit an 'estimate' with candidate = true")
        m = payload.get("model") or {}
        if not (m.get("id") and m.get("version")):
            p.append("forecast needs model.id and model.version (a registered model)")
        pt, iv = payload.get("point"), payload.get("interval80")
        if not isinstance(pt, (int, float)) or not (isinstance(iv, (list, tuple)) and len(iv) == 2
                                                     and all(isinstance(v, (int, float)) for v in iv)):
            p.append("forecast needs a numeric point and interval80 [lo, hi]")
        elif not iv[0] <= pt <= iv[1]:
            p.append("forecast point must lie inside interval80")
        res = payload.get("resolution") or {}
        if not (res.get("quantity") and res.get("resolvable_after")):
            p.append("forecast needs resolution.quantity and resolution.resolvable_after")
        elif to_timestamp(res["resolvable_after"]) <= to_timestamp(as_of):
            p.append("forecast must resolve after its as-of date (no hindsight)")
    if t == "estimate":
        if not isinstance(payload.get("value", payload.get("point")), (int, float)):
            p.append("estimate needs a numeric value")
        if not (payload.get("sd") is not None or payload.get("interval")):
            p.append("estimate needs uncertainty (sd or interval)")
        if not payload.get("method"):
            p.append("estimate needs a method")
        if agent and payload.get("candidate") is not True:
            p.append("agent estimates must be labelled candidate = true (evaluated separately from forecasts)")
    if t == "assumption" and not payload.get("scope"):
        p.append("assumption needs a scope (the model or scenario it belongs to)")
    if t == "explanation":
        if not evidence:
            p.append("explanation must cite evidence")
        if forecast_like:
            p.append(f"explanations interpret observed evidence; forecast fields {forecast_like} belong in a forecast claim")
    if t == "hypothesis":
        if not evidence:
            p.append("hypothesis must cite evidence")
        imps = payload.get("implications")
        if not (isinstance(imps, list) and imps and all(isinstance(i, dict) and i.get("metric") and i.get("direction")
                                                        for i in imps)):
            p.append("hypothesis needs observable implications (metric and direction each)")
        if not (isinstance(payload.get("refutation_criteria"), str) and payload["refutation_criteria"].strip()):
            p.append("hypothesis needs refutation criteria")
        if payload.get("origin") not in ("original", "template_selection", "reference_rule"):
            p.append("hypothesis origin must be 'original', 'template_selection' or 'reference_rule'")
        if forecast_like:
            p.append(f"a hypothesis is not a forecast: move {forecast_like} into a forecast claim")
    if t == "simulated_outcome":
        if not payload.get("scenario"):
            p.append("simulated outcome needs its scenario")
        if not isinstance(payload.get("assumptions"), list) or not payload["assumptions"]:
            p.append("simulated outcome needs its assumptions")
        if payload.get("conditional") is not True:
            p.append("simulated outcomes are conditional on their scenario (conditional = true)")
        if payload.get("calibration") not in ("synthetic_uncalibrated", "uncalibrated", "calibrated"):
            p.append("simulated outcome needs a calibration status")
        if "resolution" in payload:
            p.append("simulated outcomes are never scored as forecasts: no resolution rule")
    if t == "critique":
        if not payload.get("target"):
            p.append("critique needs a target claim")
        if payload.get("severity") not in ("low", "medium", "high", "unrated"):
            p.append("critique severity must be low, medium, high or 'unrated' (critics that do not rate)")
    if t == "strategic_proposal":
        levers = payload.get("levers")
        if not (isinstance(levers, list) and levers):
            p.append("strategic proposal needs at least one lever")
        else:
            bad = [lv for lv in levers if lv not in ALLOWED_LEVERS]
            if bad:
                p.append(f"levers {bad} are not registered lawful levers")
        if PROHIBITED.search(statement + " " + _canon(payload)):
            p.append("prohibited tactic mentioned")
        if payload.get("requires_approval") is not True:
            p.append("strategic proposals require founder approval (requires_approval = true)")
        if not (payload.get("compliance") or {}).get("status"):
            p.append("strategic proposal needs a compliance review status")
        if forecast_like:
            p.append("expected effects of an option are simulated outcomes, not forecast fields")
    if t == "experiment_result" and not (payload.get("protocol_id") and payload.get("measurement")):
        p.append("experiment result needs its pre-registered protocol and measurement provenance")
    if t == "lesson" and not payload.get("evaluations"):
        p.append("a lesson must link the evaluations it generalises")
    return p


class ClaimLedger:
    def __init__(self, store, events: EventLog | None = None):
        self.store, self.conn = store, store.conn
        self.events = events or EventLog(store)

    # ------------------------------------------------------------------ writing
    def check(self, claim_type: str, statement: str, produced_by: str, *, as_of: str, payload: dict, provenance: dict,
              evidence=(), available_at: str | None = None, status: str | None = None, tenant: str = "public",
              exists=None) -> list[str]:
        p = []
        if claim_type not in CLAIM_TYPES:
            return [f"unknown claim type {claim_type!r}"]
        if not (isinstance(statement, str) and statement.strip()):
            p.append("statement must be a non-empty string")
        if not produced_by:
            p.append("produced_by is required")
        if not TENANT.match(tenant):
            p.append(f"invalid tenant {tenant!r}")
        if not isinstance(payload, dict) or not isinstance(provenance, dict):
            return p + ["payload and provenance must be objects"]
        try:
            a, av = to_timestamp(as_of), to_timestamp(available_at or as_of)
            if av < a:
                p.append("a claim cannot be available before it was made (available_at < as_of)")
        except Exception:  # noqa: BLE001
            p.append("as_of / available_at must be dates or timestamps")
            return p
        st = status or INITIAL_STATUS.get(claim_type, "proposed")
        if st not in STATUSES:
            p.append(f"unknown status {st!r}")
        if _is_agent(produced_by) and st != "proposed":
            p.append("agents may only create 'proposed' claims")
        if st == "validated":
            p.append("claims cannot be created as validated")
        ev = list(evidence or [])
        if not all(isinstance(e, str) for e in ev):
            p.append("evidence must be a list of id strings")
        elif exists is not None:
            missing = [e for e in ev if not exists(e)]
            if missing:
                p.append(f"evidence not available at {a}: {missing[:5]}")
        p += _check_type(claim_type, statement, produced_by, a, payload, ev)
        return p

    def submit(self, claim_type: str, statement: str, produced_by: str, *, as_of: str, payload: dict, provenance: dict,
               evidence=(), run_id: str | None = None, available_at: str | None = None, status: str | None = None,
               tenant: str = "public", exists=None, strict: bool = False, commit: bool = True) -> tuple[str | None, list[str]]:
        problems = self.check(claim_type, statement, produced_by, as_of=as_of, payload=payload, provenance=provenance,
                              evidence=evidence, available_at=available_at, status=status, tenant=tenant, exists=exists)
        if problems:
            from .failures import record  # noqa: PLC0415
            record(self.store, run_id=run_id, produced_by=produced_by, subject=claim_type, stage="ledger", outcome="rejected",
                   failure_class=f"ledger.{claim_type}", problems=problems,
                   item={"statement": statement, "payload": payload}, as_of=as_of, tenant=tenant, events=self.events)
            if commit:
                self.conn.commit()
            if strict:
                raise ClaimRejected(problems)
            return None, problems
        a, av = to_timestamp(as_of), to_timestamp(available_at or as_of)
        full_prov = {**provenance, "produced_by": produced_by, "run_id": run_id}
        body = {"t": claim_type, "s": statement, "by": produced_by, "run": run_id, "tenant": tenant, "as_of": a,
                "payload": payload, "evidence": list(evidence)}
        cid = "c:" + hashlib.sha256(_canon(body).encode()).hexdigest()[:24]
        content = hashlib.sha256(_canon({"t": claim_type, **identity(claim_type, a, payload)}).encode()).hexdigest()
        if self.conn.execute("SELECT 1 FROM claims WHERE claim_id=?", (cid,)).fetchone():
            return cid, []  # idempotent: the identical claim is already recorded
        self.conn.execute("""INSERT INTO claims (claim_id, claim_type, statement, produced_by, run_id, tenant, as_of, available_at,
            schema_version, content_hash, provenance_json, payload_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (cid, claim_type, statement, produced_by, run_id, tenant, a, av, SCHEMA_VERSION, content,
                           _canon({**full_prov, "evidence": list(evidence)}), _canon(payload), utc_now()))
        st = status or INITIAL_STATUS.get(claim_type, "proposed")
        self.conn.execute("INSERT INTO claim_status_history VALUES (?,?,?,?,?,?)", (cid, st, av, utc_now(), produced_by, "created"))
        dup = self.conn.execute("""SELECT claim_id FROM claims WHERE content_hash=? AND claim_id<>? AND tenant=?
            AND COALESCE(run_id,'')=COALESCE(?, '') ORDER BY created_at LIMIT 1""", (content, cid, tenant, run_id)).fetchone()
        if dup:
            self._link(cid, "duplicates", dup[0], produced_by)
        self.events.append("claim.accepted", "EI", {"claim_type": claim_type, "status": st, "duplicate_of": dup[0] if dup else None},
                           run_id=run_id, subject_id=cid, provenance=full_prov, tenant=tenant)
        if commit:
            self.conn.commit()
        return cid, []

    def _link(self, from_id, relation, to_id, by):
        if relation not in RELATIONS:
            raise ValueError(f"unknown relation {relation!r}")
        self.conn.execute("INSERT OR IGNORE INTO claim_links VALUES (?,?,?,?,?)", (from_id, relation, to_id, by, utc_now()))

    def link(self, from_id: str, relation: str, to_id: str, produced_by: str, commit: bool = True) -> None:
        self._link(from_id, relation, to_id, produced_by)
        if commit:
            self.conn.commit()

    def set_status(self, claim_id: str, status: str, changed_by: str, *, effective_at: str, reason: str,
                   evaluation_id: str | None = None, commit: bool = True) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        if status == "validated" and (not evaluation_id or _is_agent(changed_by)):
            raise ValueError("'validated' requires an evaluation and a non-agent decision (lessons are never "
                             "validated by being recorded)")
        row = self.conn.execute("SELECT tenant, run_id FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            raise KeyError(claim_id)
        self.conn.execute("INSERT INTO claim_status_history VALUES (?,?,?,?,?,?)",
                          (claim_id, status, to_timestamp(effective_at), utc_now(), changed_by,
                           reason + (f" (evaluation {evaluation_id})" if evaluation_id else "")))
        self.events.append("claim.status_changed", "EI", {"status": status, "reason": reason, "evaluation_id": evaluation_id},
                           run_id=row["run_id"], subject_id=claim_id, provenance={"changed_by": changed_by},
                           tenant=row["tenant"])
        if commit:
            self.conn.commit()

    # ------------------------------------------------------------------ reading
    def _row(self, r, as_of: str | None) -> dict:
        q = "SELECT status FROM claim_status_history WHERE claim_id=?"
        args = [r["claim_id"]]
        if as_of:
            q += " AND effective_at<=?"
            args.append(to_timestamp(as_of))
        st = self.conn.execute(q + " ORDER BY effective_at DESC, recorded_at DESC LIMIT 1", args).fetchone()
        return {**{k: r[k] for k in r.keys() if not k.endswith("_json")}, "payload": json.loads(r["payload_json"]),
                "provenance": json.loads(r["provenance_json"]), "status": st[0] if st else None}

    def get(self, claim_id: str, *, as_of: str | None = None, tenant: str = "public") -> dict | None:
        r = self.conn.execute("SELECT * FROM claims WHERE claim_id=? AND tenant IN (?, 'public')", (claim_id, tenant)).fetchone()
        if r is None or (as_of and r["available_at"] > to_timestamp(as_of)):
            return None
        return self._row(r, as_of)

    def as_of(self, t: str, *, claim_type: str | None = None, tenant: str = "public", run_id: str | None = None) -> list[dict]:
        """Claims knowable at ``t`` (available_at <= t), visible to ``tenant`` (own + public), with status as of ``t``."""
        q, args = "SELECT * FROM claims WHERE available_at<=? AND tenant IN (?, 'public')", [to_timestamp(t), tenant]
        if claim_type:
            q += " AND claim_type=?"
            args.append(claim_type)
        if run_id:
            q += " AND run_id=?"
            args.append(run_id)
        return [self._row(r, t) for r in self.conn.execute(q + " ORDER BY available_at, claim_id", args)]

    def scorable(self, claim: dict) -> bool:
        """Only forecasts are scored as forecasts; explanations, hypotheses and simulated outcomes never are."""
        return claim["claim_type"] == "forecast" and "resolution" in claim["payload"]

    def counts(self, run_prefix: str | None = None) -> dict:
        q, args = "SELECT claim_type, COUNT(*) FROM claims", []
        if run_prefix:
            q += " WHERE run_id LIKE ?"
            args.append(run_prefix + "%")
        return dict(self.conn.execute(q + " GROUP BY claim_type", args).fetchall())
