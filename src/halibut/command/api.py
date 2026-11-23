"""Read-only result API (R2). Functions return plain JSON-serialisable data; nothing here writes.

These functions define the contract a future web frontend (e.g. a Next.js dashboard behind a thin HTTP
layer) consumes. The frontend never touches the database: it reads investigation result documents
(``halibut.investigation_result.v1``) and approval records through these calls, filtered by tenant.

    list_investigations(store, tenant)            -> [{investigation_id, market_id, as_of, evaluate_as_of, status,
                                                       content_hash, created_at}]
    get_investigation(store, investigation_id, tenant) -> the full result document, or None
    get_items(store, investigation_id, item_type, tenant) -> items of one type across stages
    list_approvals(store, status=None)            -> approval records with their latest decision
    schema()                                      -> the JSON Schema of the result document
"""

from __future__ import annotations

import json

from ..control import approvals as _approvals
from ..investigation import artifact as _art


def list_investigations(store, tenant: str = "public") -> list[dict]:
    rows = store.conn.execute("""SELECT investigation_id, market_id, as_of, evaluate_as_of, status, content_hash, created_at
        FROM investigations WHERE tenant IN (?, 'public') ORDER BY created_at, investigation_id""", (tenant,)).fetchall()
    return [dict(r) for r in rows]


def get_investigation(store, investigation_id: str, tenant: str = "public") -> dict | None:
    r = store.conn.execute("SELECT artifact_json FROM investigations WHERE investigation_id=? AND tenant IN (?, 'public')",
                           (investigation_id, tenant)).fetchone()
    return json.loads(r[0]) if r else None


def get_items(store, investigation_id: str, item_type: str, tenant: str = "public") -> list[dict]:
    doc = get_investigation(store, investigation_id, tenant)
    if doc is None:
        return []
    items = [it for st in doc["stages"].values() for it in st if it["type"] == item_type]
    if item_type == "experiment_protocol" and doc.get("experiment_protocol"):
        items.append(doc["experiment_protocol"])
    return items


def list_approvals(store, status: str | None = None) -> list[dict]:
    return _approvals.list_all(store, status)


def schema() -> dict:
    return _art.json_schema_document()
