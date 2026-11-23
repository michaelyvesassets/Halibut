"""Cross-industry market coverage contract (Master Architecture v1.0, R1).

A market is any category x geography x period x competitor set. The contract states what every
market specification must declare, in industry-neutral terms, and checks it against the provider
coverage registry (``configs/data/provider_coverage.toml``):

  market       id, name, category, geography (ISO 3166-1 alpha-2), as_of, mode
  measurement  category_key, universe, unit, period (month|quarter|year), share_basis
  competitors  at least two, unique ids; each with the identifier every requested provider needs
  evidence     each requested evidence class maps to a registered provider that covers the market's
               geography and category

``check_contract`` validates the declaration (structure only). ``coverage_report`` measures, as of
a date, how much evidence actually exists per competitor and evidence class. A market whose category
no transactions provider covers is VALID but reported with a gap ("share forecasting unavailable"):
the architecture is not vehicle-specific, and missing coverage is shown, never hidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_raw
from ..data.store import to_timestamp

CONTRACT_VERSION = "market_coverage.v1"
REGISTRY = Path(__file__).resolve().parents[3] / "configs" / "data" / "provider_coverage.toml"
PERIODS = ("month", "quarter", "year")
ISO2 = re.compile(r"^[A-Z]{2}$")
EVIDENCE_KEYS = {"transactions": "transactions", "footprint": "footprint", "media": "media"}


@dataclass
class ContractReport:
    market_id: str
    version: str = CONTRACT_VERSION
    violations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    providers: dict = field(default_factory=dict)  # evidence_class -> provider id (or None)
    capabilities: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict:
        return {"market_id": self.market_id, "contract": self.version, "valid": self.valid, "violations": self.violations,
                "warnings": self.warnings, "providers": self.providers, "capabilities": self.capabilities}


def load_registry(path=REGISTRY) -> dict:
    raw = load_raw(path)
    if raw.get("schema_version") != "1.0":
        raise ValueError("provider coverage registry: schema_version must be '1.0'")
    out = {}
    for p in raw.get("providers", []):
        for k in ("id", "evidence_class", "identifier", "geographies", "categories"):
            if k not in p:
                raise ValueError(f"provider coverage registry: {p.get('id', '?')} missing {k!r}")
        out[p["id"]] = p
    return out


def _covers(p: dict, geo: str, cat: str | None) -> bool:
    return ("*" in p["geographies"] or geo in p["geographies"]) and ("*" in p["categories"] or (cat in p["categories"]))


def _identifier(comp, scheme: str):
    if scheme == "":
        return True
    if scheme == "aliases":
        return bool(comp.aliases)
    if scheme == "search":
        return bool(comp.search)
    return comp.identifiers.get(scheme)


def check_contract(market, registry: dict | None = None) -> ContractReport:
    reg = registry if registry is not None else load_registry()
    rep = ContractReport(market.id)
    if not ISO2.match(market.geography or ""):
        rep.violations.append(f"market.geography must be an ISO 3166-1 alpha-2 code (got {market.geography!r})")
    meas = market.measurement
    cat = None
    if not meas:
        rep.violations.append("[measurement] is undeclared: category_key, universe, unit, period and share_basis are required "
                              "(legacy market spec)")
    else:
        for k in ("category_key", "universe", "unit", "period", "share_basis"):
            if not meas.get(k):
                rep.violations.append(f"measurement.{k} is required")
        if meas.get("period") and meas["period"] not in PERIODS:
            rep.violations.append(f"measurement.period must be one of {list(PERIODS)}")
        cat = meas.get("category_key")
    if len(market.competitors) < 2:
        rep.violations.append("at least two competitors are required")
    requested = {"macro": "worldbank" if market.worldbank_indicators else None,
                 **{cls: (getattr(market, key) or {}).get("provider") for cls, key in EVIDENCE_KEYS.items()}}
    for cls, pid in requested.items():
        if not pid:
            rep.providers[cls] = None
            continue
        p = reg.get(pid)
        if p is None:
            rep.violations.append(f"evidence.{cls}: provider {pid!r} is not in the provider coverage registry")
            continue
        if p["evidence_class"] != cls:
            rep.violations.append(f"evidence.{cls}: provider {pid!r} supplies {p['evidence_class']!r}, not {cls!r}")
            continue
        if not _covers(p, market.geography, cat):
            rep.violations.append(f"evidence.{cls}: provider {pid!r} does not cover geography {market.geography!r} "
                                  f"and category {cat!r} (covers {p['geographies']} x {p['categories']})")
            continue
        missing = [c.id for c in market.competitors if not _identifier(c, p["identifier"])]
        if missing:
            rep.warnings.append(f"evidence.{cls}: competitors without a {p['identifier']!r} identifier get no {cls} "
                                f"evidence: {missing}")
        rep.providers[cls] = pid
    rep.capabilities = {
        "share_forecasting": "available" if rep.providers.get("transactions") else
        f"unavailable: no transactions provider covers {cat!r} in {market.geography}",
        "competitive_hypotheses": "available" if rep.providers.get("transactions") else "limited to non-transaction evidence",
        "footprint": "available" if rep.providers.get("footprint") else "unavailable",
        "media_proxy": "available" if rep.providers.get("media") else "unavailable",
        "macro_context": "available" if rep.providers.get("macro") else "unavailable"}
    return rep


def coverage_report(store, market, as_of: str | None = None, registry: dict | None = None) -> dict:
    """Evidence actually available AS OF ``as_of`` per competitor and evidence class (counts of records)."""
    t = to_timestamp(as_of or market.as_of)
    contract = check_contract(market, registry)
    rows, gaps = {}, []
    for c in market.competitors:
        b = f"brand:{c.id}"
        r = {"transactions": store.conn.execute(
                 """SELECT COUNT(*) FROM observations o JOIN entity_observations eo ON eo.series_id=o.series_id
                    WHERE eo.entity_id=? AND o.available_at<=?""", (b, t)).fetchone()[0],
             "footprint": store.conn.execute(
                 "SELECT COUNT(*) FROM relationships WHERE subject_id=? AND predicate='located_at' AND available_at<=?",
                 (b, t)).fetchone()[0],
             "media": store.conn.execute(
                 """SELECT COUNT(*) FROM document_entities de JOIN documents d ON d.doc_id=de.doc_id
                    WHERE de.entity_id=? AND d.available_at<=?""", (b, t)).fetchone()[0]}
        rows[c.id] = r
        for cls, n in r.items():
            if contract.providers.get(cls) and n == 0:
                gaps.append(f"{c.id}: no {cls} evidence available as of {t}")
    covered = sum(1 for r in rows.values() if r["transactions"] > 0)
    return {"contract": contract.as_dict(), "as_of": t, "per_competitor": rows,
            "competitors_with_transactions": covered, "gaps": gaps,
            "status": ("uncovered" if not contract.providers.get("transactions") or covered == 0
                       else "covered" if covered == len(rows) else "partial")}
