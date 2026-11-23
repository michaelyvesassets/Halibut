"""Shared provider registry, snapshot and entity helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ...config import ConfigError, _Table, load_raw
from ..catalogue import EVIDENCE_CLASSES, Source
from ..store import Store, to_timestamp, utc_now

REGISTRY = "configs/data/providers.toml"


@dataclass(frozen=True)
class ProviderDataset:
    id: str
    source: str
    evidence_class: str
    frequency: str
    unit: str
    definition: str
    publication_lag_days: int


def load_registry(path: str | Path = REGISTRY) -> tuple[dict[str, Source], dict[str, ProviderDataset]]:
    root = _Table(load_raw(path), "")
    if root.string("schema_version") != "1.0":
        raise ConfigError("providers schema_version must be '1.0'")
    sources = {}
    for t in root.tables("sources"):
        s = Source(id=t.string("id"), publisher=t.string("publisher"), title=t.string("title"), url=t.string("url"),
                   api=t.string("api"), licence=t.string("licence"), licence_url=t.string("licence_url"),
                   attribution=t.string("attribution"), redistribution_allowed=t.boolean("redistribution_allowed"),
                   access=t.string("access"), verified=t.string("verified"), notes=t.raw("notes", "") or "")
        t.done()
        sources[s.id] = s
    datasets = {}
    for t in root.tables("datasets"):
        d = ProviderDataset(t.string("id"), t.string("source", choices=tuple(sources)),
                            t.string("evidence_class", choices=EVIDENCE_CLASSES),
                            t.string("frequency", choices=("M", "Q", "A")), t.string("unit"),
                            t.string("definition"), t.number("publication_lag_days", integer=True, lo=0))
        t.done()
        datasets[d.id] = d
    root.done()
    return sources, datasets


def register(store: Store, path: str | Path = REGISTRY) -> dict[str, ProviderDataset]:
    from dataclasses import asdict  # noqa: PLC0415
    sources, datasets = load_registry(path)

    class _Cat:  # minimal shape accepted by Store.register_catalogue for sources
        pass
    cat = _Cat()
    cat.sources, cat.datasets = sources, {}
    store.register_catalogue(cat)
    with store.transaction() as c:
        for d in datasets.values():
            c.execute("""INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(dataset_id) DO UPDATE SET
                source_id=excluded.source_id, evidence_class=excluded.evidence_class, frequency=excluded.frequency,
                unit=excluded.unit, definition=excluded.definition, publication_lag_days=excluded.publication_lag_days,
                record_json=excluded.record_json""",
                      (d.id, d.source, d.evidence_class, d.frequency, d.unit, d.definition, d.publication_lag_days,
                       json.dumps(asdict(d))))
    return datasets


def stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- entities

def upsert_entity(store: Store, entity_id: str, kind: str, name: str, status: str, produced_by: str,
                  record: dict | None = None) -> str:
    store.conn.execute("""INSERT INTO entities VALUES (?,?,?,?,?,?) ON CONFLICT(entity_id) DO UPDATE SET
        name=excluded.name, record_json=excluded.record_json""",
                       (entity_id, kind, name, status, produced_by, json.dumps(record or {}, ensure_ascii=False, sort_keys=True)))
    row = store.conn.execute("SELECT kind FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    if row["kind"] != kind:
        raise ConfigError(f"entity {entity_id} already exists with kind {row['kind']!r}, not {kind!r}")
    store.conn.commit()
    return entity_id


def add_identifier(store: Store, entity_id: str, scheme: str, value: str, produced_by: str) -> None:
    store.conn.execute("INSERT OR IGNORE INTO entity_identifiers VALUES (?,?,?,?)", (entity_id, scheme, value, produced_by))
    store.conn.commit()


def add_relationship(store: Store, subject: str, predicate: str, obj: str, status: str, produced_by: str,
                     available_at: str, snapshot_id: int | None = None, record: dict | None = None,
                     valid_from: str | None = None, valid_to: str | None = None) -> str:
    rel_id = "rel:" + stable_id(subject, predicate, obj, produced_by)
    store.conn.execute("""INSERT INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(rel_id) DO UPDATE SET
        record_json=excluded.record_json""",
                       (rel_id, subject, predicate, obj, valid_from, valid_to, status, produced_by, snapshot_id,
                        to_timestamp(available_at), json.dumps(record or {}, ensure_ascii=False, sort_keys=True)))
    store.conn.commit()
    return rel_id


def snapshot(store: Store, dataset_id: str, raw: bytes, request: dict, raw_dir, publisher_updated=None) -> int:
    sid, _ = store.add_snapshot(dataset_id, raw, request, raw_dir, retrieved_at=utc_now(),
                                publisher_updated=publisher_updated)
    return sid
