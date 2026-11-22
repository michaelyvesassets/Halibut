"""Shared loading for analysis specification files (suite, sensitivity, calibration).

Every analysis spec names a base experiment config (path relative to the spec
file), optional ``[overrides]`` applied to it (e.g. a smaller analysis
population), seeds, and a runtime limit above which ``--yes`` is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, ExperimentConfig, _parse_seeds, _Table, load_raw, parse_config
from .params import parse_overrides, resolve_relative, set_paths

SPEC_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class BaseSpec:
    spec_path: Path
    base_config_path: Path
    raw: dict  # base config with overrides applied
    config: ExperimentConfig
    seeds: list[int]
    time_limit_seconds: float
    overrides: dict


def open_spec(path: str | Path) -> _Table:
    root = _Table(load_raw(path), "")
    version = root.string("schema_version")
    if version != SPEC_SCHEMA_VERSION:
        raise ConfigError(f"schema_version: analysis specs support {SPEC_SCHEMA_VERSION!r}, got {version!r}")
    return root


def load_base(root: _Table, section: _Table, spec_path: str | Path) -> BaseSpec:
    base_path = resolve_relative(spec_path, section.string("base_config"))
    overrides = parse_overrides(root.table("overrides") if "overrides" in root.data else None)
    root.used.add("overrides")
    raw = set_paths(load_raw(base_path), overrides)
    cfg = parse_config(raw)
    if any(k in section.data for k in ("seeds", "replicates", "base_seed")):
        seeds = _parse_seeds(section)
    else:
        seeds = list(cfg.seeds)
    limit = section.number("time_limit_minutes", 15.0, lo=0.0) * 60
    return BaseSpec(Path(spec_path), base_path, raw, cfg, seeds, limit, overrides)
