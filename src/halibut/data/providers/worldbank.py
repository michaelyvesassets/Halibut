"""World Bank Indicators API v2 -> annual macro observations (evidence class: macro)."""

from __future__ import annotations

import json

from ..store import Observation, SeriesRecord
from .base import snapshot

API = "https://api.worldbank.org/v2"
DATASET = "worldbank_wdi"


def fetch_indicators(store, client, raw_dir, country_iso2: str, indicators: list[str], years=(2010, 2026)) -> dict:
    """Fetch each indicator for one country; returns counts and per-indicator status."""
    out = {"indicators": {}, "observations": 0}
    lag = store.conn.execute("SELECT publication_lag_days FROM datasets WHERE dataset_id=?", (DATASET,)).fetchone()[0]
    for code in indicators:
        url = f"{API}/country/{country_iso2}/indicator/{code}"
        resp = client.get(url, params={"format": "json", "date": f"{years[0]}:{years[1]}", "per_page": 200})
        data = json.loads(resp.body)
        if not isinstance(data, list) or len(data) < 2 or data[1] is None:
            out["indicators"][code] = f"no data: {str(data)[:120]}"
            continue
        meta, rows = data[0], data[1]
        sid = snapshot(store, DATASET, resp.body, {"url": url, "indicator": code, "country": country_iso2}, raw_dir,
                       publisher_updated=meta.get("lastupdated"))
        series_id = f"worldbank:{code}"
        label = rows[0]["indicator"]["value"] if rows else code
        store.upsert_series([SeriesRecord(series_id, DATASET, series_id, {"indicator": code}, {"indicator": label})])
        store.upsert_geographies([(country_iso2, rows[0]["country"]["value"] if rows else country_iso2,
                                   "country", "ISO3166-1", country_iso2)])
        obs = [Observation(series_id, country_iso2, r["date"], f"{r['date']}-01-01", f"{r['date']}-12-31",
                           None if r["value"] is None else float(r["value"])) for r in rows]
        counts = store.load_observations(sid, obs, lag)
        out["indicators"][code] = {"label": label, "values": sum(o.value is not None for o in obs), **counts}
        out["observations"] += len(obs)
    return out
