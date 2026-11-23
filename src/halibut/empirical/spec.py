"""Pilot specification files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError, _Table, load_raw
from ..params import Parameter, resolve_relative

TARGET_KINDS = ("intercept", "trend", "config", "coefficient")
TRANSFORMS = ("log_deviation_from_trailing_mean", "level")


@dataclass(frozen=True)
class ProductSpec:
    id: str
    series: tuple[str, ...]


@dataclass(frozen=True)
class CovariateSpec:
    name: str
    series: str
    transform: str
    window: int
    coefficient: str
    loadings: dict


@dataclass(frozen=True)
class FitParameter:
    param: Parameter  # unit-cube transform
    kind: str  # intercept | trend | config | coefficient
    ref: str  # product id, config path, or coefficient name


@dataclass(frozen=True)
class PilotSpec:
    path: Path
    name: str
    description: str
    store: Path
    catalogue: Path
    template: Path
    geo: str
    start_period: str
    target_product: str
    products: tuple[ProductSpec, ...]
    annual_from: int
    recent_months: int
    moment_products: tuple[str, ...]
    se_annual: float
    se_month: float
    covariates: tuple[CovariateSpec, ...]
    parameters: tuple[FitParameter, ...]
    calib_seeds: tuple[int, ...]
    popsize: int
    maxiter: int
    optimizer_seed: int
    nm_maxfev: int
    forecast_seeds: tuple[int, ...]
    origins: tuple[str, ...]
    horizon_months: int
    change_windows: tuple[tuple[str, str], ...]
    time_limit_seconds: float

    @property
    def product_ids(self) -> tuple[str, ...]:
        return tuple(p.id for p in self.products)

    @property
    def parameter_names(self) -> list[str]:
        return [fp.param.name for fp in self.parameters]


def load_pilot(path: str | Path) -> PilotSpec:
    path = Path(path)
    root = _Table(load_raw(path), "")
    if root.string("schema_version") != "1.0":
        raise ConfigError("pilot schema_version must be '1.0'")
    pt = root.table("pilot")
    products = []
    for t in pt.tables("products"):
        series = t.raw("series")
        if not isinstance(series, list) or not series:
            raise ConfigError(f"{t.at('series')}: expected a non-empty list")
        products.append(ProductSpec(t.string("id"), tuple(series)))
        t.done()
    ids = [p.id for p in products]
    spec_kw = dict(
        name=pt.string("name"), description=pt.raw("description", "") or "",
        store=Path(pt.string("store")), catalogue=Path(pt.string("catalogue")),
        template=resolve_relative(path, pt.string("template")), geo=pt.string("geo"),
        start_period=pt.string("start_period"), target_product=pt.string("target_product", choices=ids),
    )
    pt.done()
    m = root.table("measurement")
    mp = tuple(m.raw("moment_products"))
    if not set(mp) <= set(ids):
        raise ConfigError("measurement.moment_products must be pilot products")
    meas = dict(annual_from=m.number("annual_from", integer=True), recent_months=m.number("recent_months", integer=True, lo=0),
                moment_products=mp, se_annual=m.number("se_annual", lo=1e-6), se_month=m.number("se_month", lo=1e-6))
    m.done()
    covs = []
    for t in root.tables("covariates", []):
        loadings = t.raw("loadings")
        if not isinstance(loadings, dict) or not set(loadings) <= set(ids):
            raise ConfigError(f"{t.at('loadings')}: keys must be pilot products")
        covs.append(CovariateSpec(t.string("name"), t.string("series"), t.string("transform", choices=TRANSFORMS),
                                  t.number("window", 36, integer=True, lo=1), t.string("coefficient"),
                                  {k: float(v) for k, v in loadings.items()}))
        t.done()
    params = []
    for t in root.tables("parameters"):
        name, target = t.string("name"), t.string("target")
        rng = t.raw("range")
        kind, _, ref = target.partition(":")
        if kind not in TARGET_KINDS or not ref:
            raise ConfigError(f"{t.at('target')}: expected one of {TARGET_KINDS} followed by ':'")
        if kind in ("intercept", "trend") and ref not in ids:
            raise ConfigError(f"{t.at('target')}: unknown product {ref!r}")
        if kind == "config" and "temperature" in ref:
            raise ConfigError(f"{t.at('target')}: decision temperature is fixed at 1 for empirical calibration")
        if kind == "coefficient" and ref not in {c.coefficient for c in covs}:
            raise ConfigError(f"{t.at('target')}: no covariate uses coefficient {ref!r}")
        params.append(FitParameter(Parameter(name, (target,), float(rng[0]), float(rng[1]),
                                             t.string("scale", "linear", choices=("linear", "log"))), kind, ref))
        t.done()
    c = root.table("calibration")
    calib = dict(calib_seeds=tuple(c.raw("seeds")), popsize=c.number("popsize", integer=True, lo=2),
                 maxiter=c.number("maxiter", integer=True, lo=0), optimizer_seed=c.number("optimizer_seed", 0, integer=True),
                 nm_maxfev=c.number("nelder_mead_maxfev", 0, integer=True, lo=0),
                 forecast_seeds=tuple(c.raw("forecast_seeds")))
    c.done()
    b = root.table("backtest")
    bt = dict(origins=tuple(b.raw("origins")), horizon_months=b.number("horizon_months", integer=True, lo=1),
              change_windows=tuple(tuple(w) for w in b.raw("change_windows", []) or ()),
              time_limit_seconds=b.number("time_limit_minutes", 30.0, lo=0) * 60)
    b.done()
    root.done()
    return PilotSpec(path=path, products=tuple(products), covariates=tuple(covs), parameters=tuple(params),
                     **spec_kw, **meas, **calib, **bt)
