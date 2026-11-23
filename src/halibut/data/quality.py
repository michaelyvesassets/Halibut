"""Data-quality summaries and checks over the observation store."""

from __future__ import annotations

from .store import Store


def status_rows(store: Store) -> list[dict]:
    rows = []
    for d in store.conn.execute("SELECT dataset_id, evidence_class, frequency FROM datasets ORDER BY dataset_id"):
        ds = d["dataset_id"]
        snap = store.conn.execute("""SELECT COUNT(*) n, MAX(retrieved_at) last, MAX(publisher_updated) upd
            FROM snapshots WHERE dataset_id=?""", (ds,)).fetchone()
        obs = store.conn.execute("""SELECT COUNT(*) n, SUM(o.value IS NULL) missing, MIN(o.period) p0,
            MAX(o.period) p1, COUNT(DISTINCT o.series_id) ns, COUNT(DISTINCT o.geo_id) ng,
            SUM(o.revision > 0) revised
            FROM observations o JOIN series s ON s.series_id = o.series_id WHERE s.dataset_id=?""", (ds,)).fetchone()
        rows.append({"dataset": ds, "class": d["evidence_class"], "freq": d["frequency"],
                     "snapshots": snap["n"], "last_retrieved": snap["last"], "publisher_updated": snap["upd"],
                     "series": obs["ns"], "geos": obs["ng"], "observations": obs["n"],
                     "missing": obs["missing"] or 0, "revisions": obs["revised"] or 0,
                     "periods": f"{obs['p0']}..{obs['p1']}" if obs["n"] else "-"})
    return rows
