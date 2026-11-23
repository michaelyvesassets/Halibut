"""Market specification: the only place an investigation's industry lives.

A market = product category x geography x period x competitor set, plus the
evidence plan (which providers, which series), a strategic mode and hard
budgets. Competitors are BRANDS (market-facing identities); their operating
companies, parents, products and locations are resolved separately.

Industry-neutral declaration (R1 market coverage contract, ``halibut.world.coverage``):
``[measurement]`` (category_key, universe, unit, period, share_basis) and, per competitor, an
``identifiers`` table mapping identifier schemes to values (e.g. ``{ dft_make = "BYD" }``). The legacy
keys ``dft_make`` and ``osm_brand`` are still accepted and are mirrored into ``identifiers``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, _Table, load_raw

MODES = ("enforcement", "continuation", "annihilation")


@dataclass(frozen=True)
class Competitor:
    id: str
    name: str
    search: str
    aliases: tuple[str, ...]
    dft_make: str | None
    osm_brand: tuple[str, ...]
    wikidata: str | None
    identifiers: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class MarketSpec:
    path: Path
    id: str
    name: str
    category: str
    geography: str
    gdelt_fips: tuple[str, ...]
    as_of: str
    mode: str
    focal: str | None
    target: str | None
    competitors: tuple[Competitor, ...]
    type_hints: tuple[str, ...]
    keywords: tuple[str, ...]
    worldbank_indicators: tuple[str, ...]
    transactions: dict
    footprint: dict
    media: dict
    limits: dict
    measurement: dict | None = None

    def competitor(self, cid: str) -> Competitor:
        for c in self.competitors:
            if c.id == cid:
                return c
        raise KeyError(cid)


def load_market(path) -> MarketSpec:
    path = Path(path)
    root = _Table(load_raw(path), "")
    if root.string("schema_version") != "1.0":
        raise ConfigError("market schema_version must be '1.0'")
    m = root.table("market")
    mk = dict(id=m.string("id"), name=m.string("name"), category=m.string("category"),
              geography=m.string("geography"), gdelt_fips=tuple(m.raw("gdelt_fips", []) or ()),
              as_of=m.string("as_of"), mode=m.string("mode", choices=MODES),
              focal=m.raw("focal", None), target=m.raw("target", None))
    m.done()
    comps = []
    for t in root.tables("competitors"):
        cid = t.string("id")
        ids = dict(t.raw("identifiers", {}) or {})
        dft_make = t.raw("dft_make", None) or ids.get("dft_make")
        osm = tuple(t.raw("osm_brand", []) or ids.get("osm_brand") or ())
        for k, v in (("dft_make", dft_make), ("osm_brand", list(osm) or None)):
            if ids.get(k) not in (None, v) and not (k == "osm_brand" and list(ids.get(k) or []) == list(osm)):
                raise ConfigError(f"competitors[{cid}]: {k} given twice with different values")
            if v:
                ids[k] = v
        comps.append(Competitor(cid, t.string("name"), t.string("search", t.raw("name")),
                                tuple(a.lower() for a in t.raw("aliases")), dft_make, osm, t.raw("wikidata", None), ids))
        t.done()
    ids = [c.id for c in comps]
    if len(set(ids)) != len(ids) or len(ids) < 2:
        raise ConfigError("competitors: at least two, with unique ids")
    for role in ("focal", "target"):
        if mk[role] is not None and mk[role] not in ids:
            raise ConfigError(f"market.{role}: {mk[role]!r} is not a competitor")
    if mk["mode"] == "annihilation" and not (mk["focal"] and mk["target"]):
        raise ConfigError("annihilation mode needs both a focal competitor and a target competitor")
    r = root.table("resolution", {})
    res = dict(type_hints=tuple(r.raw("wikidata_type_hints", []) or ()), keywords=tuple(r.raw("keywords", []) or ()))
    r.done()
    ev = root.table("evidence")
    evidence = dict(worldbank_indicators=tuple(ev.raw("worldbank_indicators", []) or ()),
                    transactions=dict(ev.raw("transactions", {}) or {}), footprint=dict(ev.raw("footprint", {}) or {}),
                    media=dict(ev.raw("media", {}) or {}))
    ev.done()
    meas = root.table("measurement", {})
    measurement = {k: meas.raw(k, None) for k in ("category_key", "universe", "unit", "period", "share_basis",
                                                  "category_taxonomy")} if meas.data else None
    meas.done()
    lim = root.table("limits", {})
    limits = dict(max_requests=lim.number("max_requests", 80, integer=True, lo=1),
                  max_megabytes=lim.number("max_megabytes", 80, lo=0.1), max_seconds=lim.number("max_seconds", 900, lo=1))
    lim.done()
    root.done()
    return MarketSpec(path=path, competitors=tuple(comps), limits=limits, measurement=measurement, **mk, **res, **evidence)
