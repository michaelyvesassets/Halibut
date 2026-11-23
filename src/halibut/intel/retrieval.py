"""Evidence retrieval (implements interfaces.EvidenceRetriever).

Read-only and date-bounded: every query returns only records available on or
before ``as_of``, with stable evidence ids:
  obs:<id>  observation       rel:<id>  relationship      doc:<id>  document
  ent:<id>  entity            k:<id>    learning-memory knowledge item
"""

from __future__ import annotations

import json

from ..data.store import Store, to_timestamp


class StoreEvidenceRetriever:
    def __init__(self, store: Store, as_of: str):
        self.store, self.as_of = store, to_timestamp(as_of)
        self._cache: dict = {}

    def observations(self, series_id: str, geo: str) -> list[dict]:
        key = (series_id, geo)
        if key not in self._cache:
            self._cache[key] = self._observations(series_id, geo)
        return self._cache[key]

    def _observations(self, series_id: str, geo: str) -> list[dict]:
        rows = self.store.conn.execute("""
            SELECT o.obs_id, o.period, o.value, o.available_at FROM observations o
            JOIN (SELECT period, MAX(revision) rev FROM observations WHERE series_id=? AND geo_id=? AND available_at<=?
                  GROUP BY period) l ON l.period=o.period AND l.rev=o.revision
            WHERE o.series_id=? AND o.geo_id=? ORDER BY o.period_start""",
                                       (series_id, geo, self.as_of, series_id, geo)).fetchall()
        return [{"id": f"obs:{r['obs_id']}", "period": r["period"], "value": r["value"], "available_at": r["available_at"]}
                for r in rows]

    def series_ids(self, universe_key: str) -> list[str]:
        return [r[0] for r in self.store.conn.execute("SELECT series_id FROM series WHERE universe_key=? ORDER BY series_id",
                                                      (universe_key,))]

    def relationships(self, subject: str | None = None, predicate: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM relationships WHERE available_at<=?", [self.as_of]
        if subject:
            q, args = q + " AND subject_id=?", args + [subject]
        if predicate:
            q, args = q + " AND predicate=?", args + [predicate]
        return [{"id": r["rel_id"], "subject": r["subject_id"], "predicate": r["predicate"], "object": r["object_id"],
                 "status": r["epistemic_status"], "produced_by": r["produced_by"]}
                for r in self.store.conn.execute(q + " ORDER BY rel_id", args)]

    def entity(self, entity_id: str) -> dict | None:
        r = self.store.conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
        return None if r is None else {"id": f"ent:{r['entity_id']}", "entity_id": r["entity_id"], "kind": r["kind"],
                                       "name": r["name"], "status": r["epistemic_status"], "record": json.loads(r["record_json"])}

    def documents(self, entity_id: str, start: str, end: str) -> list[dict]:
        rows = self.store.conn.execute("""SELECT d.doc_id, d.url, d.source_domain, d.published_at, d.tone FROM documents d
            JOIN document_entities de ON de.doc_id=d.doc_id WHERE de.entity_id=? AND d.available_at<=?
            AND d.published_at>=? AND d.published_at<=? ORDER BY d.published_at""",
                                       (entity_id, self.as_of, to_timestamp(start), to_timestamp(end))).fetchall()
        return [{"id": r["doc_id"], "url": r["url"], "source": r["source_domain"], "published_at": r["published_at"],
                 "tone": r["tone"]} for r in rows]

    def exists(self, evidence_id: str) -> bool:
        """True if the evidence id refers to a record available at ``as_of``."""
        kind, _, key = evidence_id.partition(":")
        if kind == "obs":
            return self.store.conn.execute("SELECT 1 FROM observations WHERE obs_id=? AND available_at<=?",
                                           (int(key), self.as_of)).fetchone() is not None
        if kind == "rel":
            return self.store.conn.execute("SELECT 1 FROM relationships WHERE rel_id=? AND available_at<=?",
                                           (evidence_id, self.as_of)).fetchone() is not None
        if kind == "doc":
            return self.store.conn.execute("SELECT 1 FROM documents WHERE doc_id=? AND available_at<=?",
                                           (evidence_id, self.as_of)).fetchone() is not None
        if kind == "ent":
            return self.store.conn.execute("SELECT 1 FROM entities WHERE entity_id=?", (key,)).fetchone() is not None
        if kind == "k":
            return self.store.conn.execute("SELECT 1 FROM knowledge WHERE knowledge_id=?", (evidence_id,)).fetchone() is not None
        return False
