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


# --------------------------------------------------------------------------- checks

import statistics
from datetime import date

from ..config import ConfigError, _Table, load_raw


def _check(name, status, message, **details):
    return {"name": name, "status": status, "message": message, "details": details}


def completeness_checks(store: Store) -> list[dict]:
    """Missing values, negative counts, and gaps in each series' period sequence."""
    out = []
    for d in store.conn.execute("SELECT dataset_id, frequency, unit FROM datasets ORDER BY dataset_id"):
        ds = d["dataset_id"]
        rows = store.conn.execute("""SELECT o.series_id, o.geo_id, o.period, o.value FROM observations o
            JOIN series s ON s.series_id=o.series_id WHERE s.dataset_id=? AND o.geo_id='NO'
            ORDER BY o.series_id, o.period_start""", (ds,)).fetchall()
        if not rows:
            out.append(_check(f"{ds}:present", "warn", "no observations loaded"))
            continue
        missing = sum(r["value"] is None for r in rows)
        negative = sum(r["value"] is not None and r["value"] < 0 for r in rows)
        gaps = 0
        by_series: dict[str, list[str]] = {}
        for r in rows:
            by_series.setdefault(r["series_id"], []).append(r["period"])
        for periods in by_series.values():
            gaps += _count_gaps(periods, d["frequency"])
        status = "fail" if negative and d["unit"] == "vehicles" else ("warn" if missing or gaps else "pass")
        out.append(_check(f"{ds}:completeness", status,
                          f"{len(rows)} national observations, {missing} missing, {negative} negative, {gaps} period gaps",
                          missing=missing, negative=negative, gaps=gaps, series=len(by_series)))
    return out


def _count_gaps(periods: list[str], freq: str) -> int:
    def ordinal(p):
        if freq == "M":
            return int(p[:4]) * 12 + int(p[5:7])
        if freq == "Q":
            return int(p[:4]) * 4 + int(p[-1])
        return int(p)
    o = sorted(ordinal(p) for p in periods)
    return sum(b - a - 1 for a, b in zip(o, o[1:]) if b - a > 1)


def structural_zero_checks(store: Store) -> list[dict]:
    """For regional datasets: how many region codes are entirely zero in a period.

    Codes not valid in a given territorial classification are typically
    reported as 0; they must be treated as missing, not as zero activity.
    """
    out = []
    for d in store.conn.execute("""SELECT DISTINCT s.dataset_id FROM series s JOIN observations o ON o.series_id=s.series_id
            WHERE o.geo_id != 'NO'"""):
        ds = d["dataset_id"]
        rows = store.conn.execute("""SELECT o.period, o.geo_id, g.level, SUM(o.value) tot FROM observations o
            JOIN series s ON s.series_id=o.series_id JOIN geographies g ON g.geo_id=o.geo_id
            WHERE s.dataset_id=? AND g.level IN ('county','municipality') GROUP BY o.period, o.geo_id""", (ds,)).fetchall()
        per = {}
        for r in rows:
            k = (r["period"], r["level"])
            nz, n = per.get(k, (0, 0))
            per[k] = (nz + (1 if (r["tot"] or 0) > 0 else 0), n + 1)
        detail = {f"{p}:{lvl}": f"{nz}/{n} codes non-zero" for (p, lvl), (nz, n) in sorted(per.items())}
        nonzero_counts = {lvl: sorted({nz for (p, l2), (nz, n) in per.items() if l2 == lvl}) for lvl in ("county", "municipality")}
        changes = any(len(v) > 1 for v in nonzero_counts.values())
        out.append(_check(f"{ds}:structural_zeros", "warn" if changes else "pass",
                          "number of populated region codes changes over time (territorial reforms): zeros in invalid codes are structural"
                          if changes else "populated region codes stable over time",
                          populated_codes=detail))
    return out


def lag_checks(store: Store) -> list[dict]:
    """Observed lag between the latest period end and the publisher's release, vs the catalogue's assumption."""
    out = []
    for r in store.conn.execute("""SELECT s.dataset_id, s.snapshot_id, s.publisher_updated, d.publication_lag_days lag,
            MAX(o.period_end) pe FROM snapshots s JOIN datasets d ON d.dataset_id=s.dataset_id
            JOIN observations o ON o.snapshot_id=s.snapshot_id GROUP BY s.snapshot_id"""):
        if not r["publisher_updated"]:
            continue
        observed = (date.fromisoformat(r["publisher_updated"][:10]) - date.fromisoformat(r["pe"])).days
        ok = observed <= r["lag"]
        out.append(_check(f"{r['dataset_id']}:publication_lag", "pass" if ok else "fail",
                          f"latest period {r['pe']} released {r['publisher_updated'][:10]} ({observed} days); "
                          f"catalogue assumes {r['lag']} days" + ("" if ok else ": estimate TOO SHORT (leakage risk)"),
                          observed_days=observed, assumed_days=r["lag"]))
    return out


def revision_checks(store: Store) -> list[dict]:
    out = []
    for r in store.conn.execute("""SELECT s.dataset_id, COUNT(DISTINCT sn.snapshot_id) snaps, SUM(o.revision > 0) revs
            FROM observations o JOIN series s ON s.series_id=o.series_id JOIN snapshots sn ON sn.dataset_id=s.dataset_id
            GROUP BY s.dataset_id"""):
        msg = (f"{r['revs'] or 0} revised observations across {r['snaps']} snapshot(s)"
               + ("; revisions before the first retrieval are unobservable" if r["snaps"] == 1 else ""))
        out.append(_check(f"{r['dataset_id']}:revisions", "info", msg, snapshots=r["snaps"], revised=r["revs"] or 0))
    return out


def outlier_checks(store: Store, z: float = 3.5) -> list[dict]:
    """Robust outliers in national monthly totals per universe (median/MAD within +-12 months)."""
    out = []
    for u in store.conn.execute("""SELECT DISTINCT s.universe_key, s.dataset_id FROM series s JOIN datasets d
            ON d.dataset_id=s.dataset_id WHERE d.frequency='M' AND d.unit='vehicles'"""):
        totals: dict[str, float] = {}
        for r in store.conn.execute("""SELECT o.period, o.value FROM observations o JOIN series s ON s.series_id=o.series_id
                WHERE s.universe_key=? AND o.geo_id='NO' AND o.value IS NOT NULL""", (u["universe_key"],)):
            totals[r["period"]] = totals.get(r["period"], 0.0) + r["value"]
        months = sorted(totals)
        vals = [totals[m] for m in months]
        flagged = []
        for i, m in enumerate(months):
            w = vals[max(0, i - 12): i + 13]
            med = statistics.median(w)
            mad = statistics.median(abs(x - med) for x in w) or 1.0
            score = (vals[i] - med) / (1.4826 * mad)
            if abs(score) > z:
                flagged.append({"period": m, "total": vals[i], "robust_z": round(score, 2)})
        out.append(_check(f"{u['universe_key']}:outliers", "info",
                          f"{len(flagged)} months with |robust z| > {z}: " + ", ".join(f["period"] for f in flagged),
                          flagged=flagged))
    return out


def consistency_checks(store: Store, path) -> list[dict]:
    root = _Table(load_raw(path), "")
    if root.string("schema_version") != "1.0":
        raise ConfigError("checks schema_version must be '1.0'")
    v = store.latest()
    out = []
    for t in root.tables("consistency"):
        name, geo, tol = t.string("name"), t.string("geo"), t.number("tolerance_rel", lo=0.0)
        left, right = t.raw("left"), t.raw("right")
        t.done()

        def annual(ids):
            acc: dict[str, float] = {}
            for sid in ids:
                for p, x in v.series(sid, geo):
                    if x is not None:
                        acc[p[:4]] = acc.get(p[:4], 0.0) + x
            return acc
        a, b = annual(left), annual(right)
        years = sorted(set(a) & set(b))
        # only complete years on the monthly side
        months = {}
        for sid in left[:1]:
            for p, _ in v.series(sid, geo):
                months.setdefault(p[:4], set()).add(p)
        years = [y for y in years if len(months.get(y, ())) in (0, 12)]
        rel = {y: (a[y] - b[y]) / b[y] if b[y] else None for y in years}
        worst = max((abs(x) for x in rel.values() if x is not None), default=0.0)
        out.append(_check(f"consistency:{name}", "pass" if worst <= tol else "warn",
                          f"max relative difference {worst:.4f} over {len(years)} years (tolerance {tol})",
                          relative_difference={y: (round(x, 5) if x is not None else None) for y, x in rel.items()},
                          left_totals={y: a[y] for y in years}, right_totals={y: b[y] for y in years}))
    root.done()
    return out


def run_quality_checks(store: Store, catalogue, checks_file: str | None = "configs/data/checks_norway_ev.toml") -> dict:
    from pathlib import Path  # noqa: PLC0415
    checks = completeness_checks(store) + lag_checks(store) + revision_checks(store)
    checks += structural_zero_checks(store) + outlier_checks(store)
    if checks_file and Path(checks_file).exists():
        checks += consistency_checks(store, checks_file)
    counts = {s: sum(c["status"] == s for c in checks) for s in ("fail", "warn", "info", "pass")}
    return {"status_counts": counts, "checks": checks,
            "attribution": [s["attribution"] for s in store.conn.execute("SELECT attribution FROM sources")]}
