"""Append-only, hash-chained event log (``events_log``, migration 6).

Each event's ``hash`` is sha256 over its canonical content plus the previous event's hash, so any
edit, deletion or reordering of history is detectable with ``verify()``. Triggers additionally
forbid UPDATE and DELETE. Events are written in the caller's transaction (outbox pattern): commit
state and event together.
"""

from __future__ import annotations

import hashlib
import json

from ..data.store import utc_now

GENESIS = "0" * 64
LAYERS = ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "EI")


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def event_hash(prev_hash: str, body: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(body)).encode()).hexdigest()


class EventLog:
    def __init__(self, store):
        self.conn = store.conn

    def last_hash(self) -> str:
        r = self.conn.execute("SELECT hash FROM events_log ORDER BY seq DESC LIMIT 1").fetchone()
        return r[0] if r else GENESIS

    def append(self, event_type: str, layer: str, payload: dict, *, run_id: str | None = None, subject_id: str | None = None,
               provenance: dict | None = None, tenant: str = "public", commit: bool = False) -> str:
        if layer not in LAYERS:
            raise ValueError(f"unknown layer {layer!r}")
        if not event_type or "." not in event_type:
            raise ValueError("event_type must be dotted, e.g. 'claim.accepted'")
        body = {"event_type": event_type, "layer": layer, "run_id": run_id, "tenant": tenant, "subject_id": subject_id,
                "payload": payload, "provenance": provenance or {}, "at": utc_now()}
        prev = self.last_hash()
        h = event_hash(prev, body)
        self.conn.execute("""INSERT INTO events_log (event_type, layer, run_id, tenant, subject_id, payload_json,
            provenance_json, at, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                          (event_type, layer, run_id, tenant, subject_id, _canonical(payload), _canonical(provenance or {}),
                           body["at"], prev, h))
        if commit:
            self.conn.commit()
        return h

    def read(self, event_type: str | None = None, run_id: str | None = None, after_seq: int = 0) -> list[dict]:
        q, args = "SELECT * FROM events_log WHERE seq > ?", [after_seq]
        if event_type:
            q += " AND event_type = ?"
            args.append(event_type)
        if run_id:
            q += " AND run_id = ?"
            args.append(run_id)
        rows = self.conn.execute(q + " ORDER BY seq", args).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload_json"]), "provenance": json.loads(r["provenance_json"])}
                for r in rows]

    def verify(self) -> dict:
        """Recompute the chain. Returns {'ok', 'events', 'first_bad_seq'}."""
        prev, n = GENESIS, 0
        for r in self.conn.execute("SELECT * FROM events_log ORDER BY seq"):
            body = {"event_type": r["event_type"], "layer": r["layer"], "run_id": r["run_id"], "tenant": r["tenant"],
                    "subject_id": r["subject_id"], "payload": json.loads(r["payload_json"]),
                    "provenance": json.loads(r["provenance_json"]), "at": r["at"]}
            if r["prev_hash"] != prev or event_hash(prev, body) != r["hash"]:
                return {"ok": False, "events": n, "first_bad_seq": r["seq"]}
            prev, n = r["hash"], n + 1
        return {"ok": True, "events": n, "first_bad_seq": None}
