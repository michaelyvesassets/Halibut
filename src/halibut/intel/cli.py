"""``halibut intel`` commands.

    halibut intel smoke                       # one small live call per provider (connectivity report)
    halibut intel investigate MARKET.toml     # end-to-end investigation of a competitive market
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from ..config import ConfigError

DEFAULT_DB = "data/halibut.sqlite"


def add_intel_commands(sub) -> None:
    p = sub.add_parser("intel", help="market intelligence: providers, competitor graph, hypotheses")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--raw", default="data/raw")
    isub = p.add_subparsers(dest="intel_command", required=True)
    s = isub.add_parser("smoke", help="live connectivity check: one small request per provider")
    s.add_argument("--out", default="results/intel")
    inv = isub.add_parser("investigate", help="investigate a competitive market (network unless --offline)")
    inv.add_argument("market")
    inv.add_argument("--out", default="results/intel")
    inv.add_argument("--offline", action="store_true", help="use only evidence already in the store")
    inv.add_argument("--yes", action="store_true", help="proceed even if the estimate exceeds the market's limits")
    inv.add_argument("--dry-run", action="store_true", help="print the plan and budget, then exit")
    p.set_defaults(handler=_handle)


def _handle(args) -> int:
    from ..data.store import Store  # noqa: PLC0415
    from ..data.providers.base import register  # noqa: PLC0415
    try:
        store = Store(args.db)
        register(store)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.intel_command == "smoke":
            return smoke(store, args)
        from .workflow import investigate_cli  # noqa: PLC0415
        return investigate_cli(store, args)
    finally:
        store.close()


def smoke(store, args) -> int:
    from ..data.http import Budget, PoliteClient  # noqa: PLC0415
    from ..data.providers import dft, gdelt, worldbank  # noqa: PLC0415
    from ..data.store import utc_now  # noqa: PLC0415
    results = {}
    raw = Path(args.raw)

    def run(name, fn):
        t0 = time.monotonic()
        try:
            detail = fn()
            results[name] = {"status": "ok", "seconds": round(time.monotonic() - t0, 1), "detail": detail}
        except Exception as exc:  # report, never hide
            results[name] = {"status": "failed", "seconds": round(time.monotonic() - t0, 1), "error": str(exc)[:300]}
        print(f"  {name:<10} {results[name]['status']:<7} {results[name]['seconds']:>6}s  "
              f"{json.dumps(results[name].get('detail', results[name].get('error')))[:150]}")

    mk = lambda prov, interval, **b: PoliteClient(prov, Budget(**b), store, min_interval=interval, retries=1)  # noqa: E731
    run("worldbank", lambda: worldbank.fetch_indicators(store, mk("worldbank", 1, max_requests=3, max_bytes=200_000), raw,
                                                         "GB", ["NY.GDP.PCAP.CD"], (2020, 2025))["indicators"])

    def wd():
        c = mk("wikidata", 2, max_requests=3, max_bytes=200_000)
        r = c.get("https://www.wikidata.org/w/api.php", params={"action": "wbsearchentities", "search": "Tesla",
                                                                "language": "en", "type": "item", "limit": 3, "format": "json"})
        return [(x["id"], x.get("description")) for x in json.loads(r.body)["search"]]
    run("wikidata", wd)

    def osm():
        c = mk("osm", 10, max_requests=3, max_bytes=500_000)
        status = c.get("https://overpass-api.de/api/status").body.decode()[:200]
        q = '[out:json][timeout:25];node["amenity"="cafe"](51.505,-0.13,51.515,-0.11);out count;'
        import urllib.parse  # noqa: PLC0415
        r = c.post("https://overpass-api.de/api/interpreter", data=("data=" + urllib.parse.quote(q)).encode(),
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        d = json.loads(r.body)
        return {"status_line": status.splitlines()[3] if len(status.splitlines()) > 3 else status,
                "count": d["elements"][0]["tags"]["total"], "osm_timestamp": d["osm3s"]["timestamp_osm_base"]}
    run("overpass", osm)

    def gd():
        c = mk("gdelt", 6, max_requests=2, max_bytes=8_000_000)
        out = gdelt.fetch_sample(store, c, raw, ["20261102120000"], {}, (), max_files=1)
        return {"bytes": out["bytes"], "records": out["files"][0]["records"]}
    run("gdelt", gd)

    def df():
        url, updated = dft.locate(mk("dft", 1, max_requests=2, max_bytes=200_000))
        return {"file": url.rsplit("/", 1)[-1], "publisher_updated": updated}
    run("dft", df)

    def rdw():
        import socket  # noqa: PLC0415
        try:
            return {"resolved": socket.gethostbyname("opendata.rdw.nl")}
        except OSError as exc:
            raise RuntimeError(f"opendata.rdw.nl does not resolve: {exc}") from exc
    run("rdw", rdw)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"smoke_{utc_now()[:10]}.json"
    path.write_text(json.dumps({"checked_at": utc_now(), "results": results}, indent=2) + "\n")
    print(f"report: {path}")
    return 0 if all(r["status"] == "ok" for k, r in results.items() if k != "rdw") else 1
