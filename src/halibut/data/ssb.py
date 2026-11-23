"""Statistics Norway (SSB) PxWeb adapter: fetch -> raw snapshot -> parse -> load.

Fetching and parsing are separate, so parsing is tested offline on fixtures.
Ingestion is resumable and idempotent: the raw response is stored (content-
addressed) before parsing, and re-ingesting identical bytes adds nothing.
"""

from __future__ import annotations

import itertools
import json
import time
import urllib.error
import urllib.request

from ..version import ENGINE_VERSION
from .catalogue import Catalogue, Dataset
from .periods import parse_period
from .store import Observation, SeriesRecord, Store, to_timestamp, utc_now

USER_AGENT = f"halibut-research-client/{ENGINE_VERSION}"


class FetchError(RuntimeError):
    pass


class Fetcher:
    """Polite HTTP client: minimum interval between requests, bounded retries."""

    def __init__(self, min_interval: float = 2.5, retries: int = 3, timeout: float = 90.0, sleep=time.sleep):
        self.min_interval, self.retries, self.timeout, self._sleep = min_interval, retries, timeout, sleep
        self._last = 0.0

    def post_json(self, url: str, body: dict) -> bytes:
        data = json.dumps(body).encode()
        last_error = None
        for attempt in range(self.retries):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            req = urllib.request.Request(url, data=data, method="POST", headers={
                "Content-Type": "application/json", "User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self._last = time.monotonic()
                    return resp.read()
            except urllib.error.HTTPError as exc:
                self._last = time.monotonic()
                if exc.code in (400, 403, 404):  # not retryable: bad query or too large
                    raise FetchError(f"{url}: HTTP {exc.code}: {exc.read()[:300]!r}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                self._last = time.monotonic()
                last_error = exc
            self._sleep(5.0 * (attempt + 1))
        raise FetchError(f"{url}: failed after {self.retries} attempts: {last_error}")


def build_query(dataset: Dataset) -> dict:
    return {
        "query": [
            {"code": q.code, "selection": {"filter": "all" if q.values == ("*",) else "item", "values": list(q.values)}}
            for q in dataset.query
        ],
        "response": {"format": "json-stat2"},
    }


def geo_for_code(code: str) -> tuple[str, str, str, str, str]:
    """(geo_id, name placeholder, level, scheme, code) for an SSB region code."""
    if code == "0":
        return ("NO", "Norway", "country", "ISO3166-1", "NO")
    level = {2: "county", 4: "municipality"}.get(len(code), "region")
    if code == "9999":
        level = "unknown"
    return (f"NO-SSB-{code}", code, level, "SSB-region", code)


def parse_jsonstat(raw: bytes, dataset: Dataset) -> dict:
    """Parse a json-stat2 response into series, geographies and observations."""
    d = json.loads(raw)
    dims, sizes = d["id"], d["size"]
    cats = {}
    for dim in dims:
        cat = d["dimension"][dim]["category"]
        index = cat["index"]
        codes = sorted(index, key=lambda k: index[k]) if isinstance(index, dict) else list(index)
        cats[dim] = (codes, cat.get("label", {}))
    for needed in dataset.series_dims + ((dataset.geo_dim,) if dataset.geo_dim else ()) + (dataset.time_dim,):
        if needed not in dims:
            raise ValueError(f"{dataset.id}: response lacks dimension {needed!r} (has {dims})")
    values = d["value"]
    status = d.get("status") or {}
    if isinstance(status, list):
        status = {i: s for i, s in enumerate(status) if s}
    elif isinstance(status, dict):
        status = {int(k): v for k, v in status.items()}

    series, geos, observations = {}, {}, []
    fixed_labels = {dim: cats[dim][1].get(cats[dim][0][0], cats[dim][0][0]) for dim in dims if len(cats[dim][0]) == 1}
    for flat, combo in enumerate(itertools.product(*(cats[dim][0] for dim in dims))):
        cell = dict(zip(dims, combo))
        sdims = {k: cell[k] for k in dataset.series_dims}
        sid = dataset.series_id(sdims)
        if sid not in series:
            labels = {k: cats[k][1].get(cell[k], cell[k]) for k in dataset.series_dims}
            labels.update({f"fixed:{k}": v for k, v in fixed_labels.items() if k not in dataset.series_dims})
            series[sid] = SeriesRecord(sid, dataset.id, dataset.universe_key(sdims), sdims, labels)
        if dataset.geo_dim:
            g = geo_for_code(cell[dataset.geo_dim])
            g = (g[0], cats[dataset.geo_dim][1].get(cell[dataset.geo_dim], g[1]), *g[2:])
        else:
            g = ("NO", "Norway", "country", "ISO3166-1", "NO")
        geos[g[0]] = g
        period, start, end = parse_period(cell[dataset.time_dim], dataset.frequency)
        v = values[flat] if isinstance(values, list) else values.get(str(flat))
        observations.append(Observation(sid, g[0], period, start, end,
                                        None if v is None else float(v), status.get(flat)))
    expected = 1
    for s in sizes:
        expected *= s
    if len(observations) != expected or (isinstance(values, list) and len(values) != expected):
        raise ValueError(f"{dataset.id}: cell count mismatch ({len(observations)} cells, expected {expected})")
    return {"series": list(series.values()), "geographies": list(geos.values()),
            "observations": observations, "updated": d.get("updated"), "dimensions": dims, "sizes": sizes}


def ingest(store: Store, catalogue: Catalogue, dataset_id: str, raw_dir, fetcher: Fetcher | None = None,
           raw: bytes | None = None, retrieved_at: str | None = None) -> dict:
    """Fetch (unless ``raw`` is given), snapshot, parse and load one dataset."""
    dataset = catalogue.dataset(dataset_id)
    source = catalogue.sources[dataset.source]
    query = build_query(dataset)
    started = utc_now()
    cur = store.conn.execute("INSERT INTO ingest_runs (dataset_id, started_at, status) VALUES (?,?,?)",
                             (dataset_id, started, "running"))
    store.conn.commit()
    run_id = cur.lastrowid
    try:
        if raw is None:
            raw = (fetcher or Fetcher()).post_json(source.api.rstrip("/") + "/" + dataset.table, query)
        parsed = parse_jsonstat(raw, dataset)
        snapshot_id, is_new = store.add_snapshot(dataset_id, raw, {"table": dataset.table, **query}, raw_dir,
                                                 retrieved_at=retrieved_at, publisher_updated=parsed["updated"])
        store.upsert_geographies(parsed["geographies"])
        store.upsert_series(parsed["series"])
        counts = store.load_observations(snapshot_id, parsed["observations"], dataset.publication_lag_days)
        store.conn.execute("""UPDATE ingest_runs SET finished_at=?, status=?, snapshot_id=?, n_new=?, n_revised=?,
            n_unchanged=?, message=? WHERE run_id=?""",
                           (utc_now(), "ok", snapshot_id, counts["new"], counts["revised"], counts["unchanged"],
                            "new snapshot" if is_new else "identical to an existing snapshot", run_id))
        store.conn.commit()
        return {"dataset": dataset_id, "snapshot_id": snapshot_id, "new_snapshot": is_new,
                "publisher_updated": to_timestamp(parsed["updated"]) if parsed["updated"] else None,
                "series": len(parsed["series"]), "cells": len(parsed["observations"]), **counts}
    except Exception as exc:
        store.conn.execute("UPDATE ingest_runs SET finished_at=?, status=?, message=? WHERE run_id=?",
                           (utc_now(), "failed", str(exc)[:500], run_id))
        store.conn.commit()
        raise


def rebuild(catalogue: Catalogue, raw_dir, into) -> list[dict]:
    """Recreate a store purely from raw snapshots (chronological per dataset).

    File names carry the retrieval timestamp (``YYYYMMDDTHHMMSSZ_<sha>.json``),
    so availability is reconstructed exactly as at first ingestion, under the
    *current* catalogue (e.g. corrected publication lags).
    """
    from pathlib import Path  # noqa: PLC0415
    store = Store(into)
    store.register_catalogue(catalogue)
    out = []
    try:
        for ds in sorted(catalogue.datasets):
            d = Path(raw_dir) / ds
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                stamp = f.name.split("_")[0]
                retrieved = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z"
                out.append(ingest(store, catalogue, ds, raw_dir, raw=f.read_bytes(),
                                  retrieved_at=retrieved) | {"file": f.name})
    finally:
        store.close()
    return out
