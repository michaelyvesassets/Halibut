"""Experiment lineage: code, engine, configuration, data and randomness."""

from __future__ import annotations

import hashlib
import json
import subprocess

from ..version import CONFIG_SCHEMA_VERSION, ENGINE_VERSION


def code_commit() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                                    text=True, timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = None, None
    return {"commit": commit or None, "dirty": dirty}


def snapshot_hashes(store, dataset_ids: list[str] | None = None) -> list[str]:
    q = "SELECT dataset_id, sha256 FROM snapshots" + (" WHERE dataset_id IN (%s)" % ",".join("?" * len(dataset_ids)) if dataset_ids else "")
    return sorted(f"{d}:{s}" for d, s in store.conn.execute(q, dataset_ids or ()))


def lineage(store, *, config: dict | None = None, parameters: dict | None = None, seeds=None,
            datasets: list[str] | None = None, as_of: str | None = None, extra: dict | None = None) -> dict:
    cfg_hash = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest() if config is not None else None
    return {"code": code_commit(), "engine_version": ENGINE_VERSION, "config_schema": CONFIG_SCHEMA_VERSION,
            "config_hash": cfg_hash, "config": config, "parameters": parameters, "seeds": list(seeds) if seeds else None,
            "data_snapshots": snapshot_hashes(store, datasets), "as_of": as_of, **(extra or {})}


def diff(baseline: dict, candidate: dict, prefix: str = "") -> dict:
    """What changed between two configurations or parameter dicts (path -> (old, new))."""
    out = {}
    for k in sorted(set(baseline) | set(candidate)):
        a, b = baseline.get(k), candidate.get(k)
        path = f"{prefix}{k}"
        if isinstance(a, dict) and isinstance(b, dict):
            out.update(diff(a, b, path + "."))
        elif a != b:
            out[path] = (a, b)
    return out
