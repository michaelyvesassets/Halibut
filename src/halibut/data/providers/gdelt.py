"""GDELT 2.0 GKG: explicit, reproducible sample of 15-minute bulk files (media proxy).

Sampling plan: ``n_files`` timestamps evenly spaced between start and end at a
fixed time of day (UTC), each a 15-minute GKG file. The plan is recorded with
the run. A record matches a competitor when one of its Organizations equals
an alias exactly (case-insensitive), and optionally when its locations include
one of the required FIPS country codes (GDELT uses FIPS 10-4: the UK is 'UK').

Stored per match: URL, source domain, publication time, tone, matched
entities, a few themes and location codes. Article text is never stored. The
raw zip is not kept (GDELT files are immutable and re-downloadable); its
SHA-256 and size are recorded in the snapshot row via a small manifest.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

from ..store import to_timestamp
from .base import snapshot

BASE = "http://data.gdeltproject.org/gdeltv2/"
DATASET = "gdelt_gkg_sample"
AVG_FILE_BYTES = 5_000_000


def sample_plan(start: str, end: str, n_files: int, time_of_day: str = "120000") -> list[str]:
    a = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    b = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    if n_files < 1 or b <= a:
        return []
    step = (b - a) / max(n_files - 1, 1)
    out = []
    for i in range(n_files):
        d = (a + step * i).date()
        out.append(f"{d:%Y%m%d}{time_of_day}")
    return sorted(set(out))


def parse_gkg(text: str, aliases: dict[str, str], require_fips: tuple[str, ...] = ()) -> list[dict]:
    """``aliases``: lower-case organisation name -> entity id."""
    out = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) < 16:
            continue
        orgs = {o.strip().lower() for o in f[13].split(";") if o.strip()}
        matched = {aliases[o]: o for o in orgs if o in aliases}
        if not matched:
            continue
        fips = {loc.split("#")[2] for loc in f[9].split(";") if loc.count("#") >= 2}
        if require_fips and not (fips & set(require_fips)):
            continue
        try:
            tone = float(f[15].split(",")[0])
        except (ValueError, IndexError):
            tone = None
        ts = datetime.strptime(f[1], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        out.append({"gkg_id": f[0], "published_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": f[3], "url": f[4],
                    "tone": tone, "matched": matched, "fips": sorted(fips)[:10],
                    "themes": [t for t in f[7].split(";") if t][:8]})
    return out


def fetch_sample(store, client, raw_dir, timestamps: list[str], aliases: dict[str, str],
                 require_fips: tuple[str, ...] = (), max_files: int = 24) -> dict:
    if len(timestamps) > max_files:
        raise ValueError(f"sample of {len(timestamps)} files exceeds max_files={max_files}")
    files, matches = [], 0
    for ts in timestamps:
        url = f"{BASE}{ts}.gkg.csv.zip"
        resp = client.get(url, expected_bytes=AVG_FILE_BYTES)
        sha = hashlib.sha256(resp.body).hexdigest()
        with zipfile.ZipFile(io.BytesIO(resp.body)) as z:
            text = z.read(z.namelist()[0]).decode("utf-8", "replace")
        records = parse_gkg(text, aliases, require_fips)
        manifest = {"url": url, "sha256": sha, "bytes": len(resp.body), "records_total": text.count("\n") + 1,
                    "matched": records}
        sid = snapshot(store, DATASET, json.dumps(manifest, sort_keys=True).encode(), {"url": url}, raw_dir,
                       publisher_updated=to_timestamp(datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                                                      + timedelta(minutes=15)))
        for r in records:
            doc_id = "doc:" + hashlib.sha256(r["url"].encode()).hexdigest()[:16]
            store.conn.execute("""INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,?,?,?)""",
                               (doc_id, r["url"], r["source"], r["published_at"], r["published_at"], r["tone"], sid,
                                "proxy_media", json.dumps({k: r[k] for k in ("gkg_id", "fips", "themes")})))
            for ent, alias in r["matched"].items():
                store.conn.execute("INSERT OR IGNORE INTO document_entities VALUES (?,?,?)", (doc_id, ent, alias))
        store.conn.commit()
        files.append({"timestamp": ts, "bytes": len(resp.body), "records": manifest["records_total"], "matched": len(records)})
        matches += len(records)
    return {"files": files, "matched_records": matches, "bytes": sum(f["bytes"] for f in files)}
