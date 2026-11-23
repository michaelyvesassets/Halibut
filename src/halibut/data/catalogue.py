"""Source and dataset catalogue (TOML), with provenance and licensing.

A *source* is a publisher (licence, attribution, access). A *dataset* is one
reproducible request against a source; it yields many *series*, identified by
the values of ``series_dims``. ``universe_dims`` define the population inside
which shares may be computed: two series whose universe values differ (e.g.
new vs second-hand imported vehicles) can never enter the same share.

Every dataset declares an ``evidence_class``; proxies (attention, media) are
kept distinct from transactions, trade, prices and stocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, _Table, load_raw

EVIDENCE_CLASSES = (
    "transaction",  # purchases / sales / registrations of the good itself
    "trade",  # shipments across borders
    "production",
    "consumption",  # derived balances (production + imports - exports - stocks)
    "stock",  # installed base / fleet
    "price",
    "survey",
    "policy",
    "infrastructure",
    "macro",
    "proxy_attention",  # search interest, page views
    "proxy_media",  # news coverage
)
PROXY_CLASSES = ("proxy_attention", "proxy_media")
FREQUENCIES = ("M", "Q", "A")
CATALOGUE_SCHEMA = "1.0"


@dataclass(frozen=True)
class Source:
    id: str
    publisher: str
    title: str
    url: str
    api: str
    licence: str
    licence_url: str
    attribution: str
    redistribution_allowed: bool
    access: str
    verified: str
    notes: str = ""


@dataclass(frozen=True)
class QueryDim:
    code: str
    values: tuple[str, ...]  # ("*",) = all


@dataclass(frozen=True)
class Dataset:
    id: str
    source: str
    adapter: str
    table: str
    title: str
    frequency: str
    evidence_class: str
    unit: str
    measure: str
    definition: str
    publication_lag_days: int
    query: tuple[QueryDim, ...]
    series_dims: tuple[str, ...]
    universe_dims: tuple[str, ...]
    time_dim: str
    geo_dim: str | None
    default_geo: str
    caveats: tuple[str, ...] = field(default_factory=tuple)

    def series_id(self, dims: dict[str, str]) -> str:
        return self.id + "".join(f":{d}={dims[d]}" for d in self.series_dims)

    def universe_key(self, dims: dict[str, str]) -> str:
        return self.id + "".join(f":{d}={dims[d]}" for d in self.universe_dims)


@dataclass(frozen=True)
class Catalogue:
    sources: dict[str, Source]
    datasets: dict[str, Dataset]
    path: Path | None = None

    def dataset(self, dataset_id: str) -> Dataset:
        try:
            return self.datasets[dataset_id]
        except KeyError:
            raise ConfigError(f"unknown dataset {dataset_id!r}; known: {sorted(self.datasets)}") from None


def load_catalogue(path: str | Path) -> Catalogue:
    root = _Table(load_raw(path), "")
    if root.string("schema_version") != CATALOGUE_SCHEMA:
        raise ConfigError(f"catalogue schema_version must be {CATALOGUE_SCHEMA!r}")
    sources = {}
    for t in root.tables("sources"):
        s = Source(
            id=t.string("id"), publisher=t.string("publisher"), title=t.string("title"),
            url=t.string("url"), api=t.string("api"), licence=t.string("licence"),
            licence_url=t.string("licence_url"), attribution=t.string("attribution"),
            redistribution_allowed=t.boolean("redistribution_allowed"), access=t.string("access"),
            verified=t.string("verified"), notes=t.raw("notes", "") or "",
        )
        t.done()
        if s.id in sources:
            raise ConfigError(f"duplicate source {s.id!r}")
        sources[s.id] = s
    datasets = {}
    for t in root.tables("datasets"):
        query = []
        for qt in t.tables("query"):
            vals = qt.raw("values")
            if not isinstance(vals, list) or not vals or not all(isinstance(v, str) for v in vals):
                raise ConfigError(f"{qt.at('values')}: expected a non-empty list of strings")
            query.append(QueryDim(qt.string("code"), tuple(vals)))
            qt.done()
        d = Dataset(
            id=t.string("id"), source=t.string("source", choices=tuple(sources)),
            adapter=t.string("adapter", choices=("ssb_pxweb",)), table=t.string("table"),
            title=t.string("title"), frequency=t.string("frequency", choices=FREQUENCIES),
            evidence_class=t.string("evidence_class", choices=EVIDENCE_CLASSES),
            unit=t.string("unit"), measure=t.string("measure"), definition=t.string("definition"),
            publication_lag_days=t.number("publication_lag_days", integer=True, lo=0),
            query=tuple(query), series_dims=tuple(t.raw("series_dims")),
            universe_dims=tuple(t.raw("universe_dims", []) or ()),
            time_dim=t.string("time_dim", "Tid"), geo_dim=t.raw("geo_dim", None),
            default_geo=t.string("default_geo"), caveats=tuple(t.raw("caveats", []) or ()),
        )
        t.done()
        if d.id in datasets:
            raise ConfigError(f"duplicate dataset {d.id!r}")
        qcodes = {q.code for q in d.query}
        for dim in d.series_dims + d.universe_dims:
            if dim not in qcodes:
                raise ConfigError(f"dataset {d.id}: dimension {dim!r} must appear in query")
        if not set(d.universe_dims) <= set(d.series_dims):
            raise ConfigError(f"dataset {d.id}: universe_dims must be a subset of series_dims")
        if d.geo_dim and (d.geo_dim not in qcodes or d.geo_dim in d.series_dims):
            raise ConfigError(f"dataset {d.id}: geo_dim must be in query and not in series_dims "
                              "(geography is stored per observation, not in the series id)")
        datasets[d.id] = d
    root.done()
    return Catalogue(sources, datasets, Path(path))
