"""Sealed holdout: selection, separation, access control and versioning (Architecture V2, milestone 2).

The holdout is a set of (market, origin) investigations that NO development run may use. They are reserved for one
evaluation per frozen contract version, after the strategist v3 and critic improvements are frozen.

- **Selection.** Every unused quarterly origin (the 23rd of Feb, May, Aug and Nov) from 2023-08-23 to 2025-05-23 for
  each sealed market. Each needs at least 8 quarters of history before it and fully published outcome windows.
  Origins already used by any stored investigation are excluded.
- **Separation.**
  - `registry/holdout/seal.json` stores only salted sha256 entries, never the plain list;
  - the answers (outcomes) stay in the public data and are never computed into any development artefact;
  - perturbed variants (renamed brands, shifted dates) are generated only at evaluation time.
- **Access control.**
  - `guard()` runs at the start of every investigation and refuses a sealed (market, origin);
  - the one exception is a run with the evaluation token whose sha256 the founder records in the seal at freeze time
    (`freeze`);
  - until then no token exists, so every sealed origin is refused.
- **Versioning.**
  - the seal has a version;
  - an evaluation records the frozen hashes (prompts, schemas, criteria) and the seal version;
  - a used holdout entry becomes development data and is retired in the next seal version, so it is never reused;
  - a leak (any development run on a sealed entry) invalidates the entry.

Retrospective holdout windows predate the model's training cutoff and remain leakage-prone, so they are secondary.
The primary evidence is prospective questions registered after the freeze.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

SEAL = Path(__file__).resolve().parents[3] / "registry" / "holdout" / "seal.json"
VERSION = "holdout.v1"
TOKEN_ENV = "HALIBUT_HOLDOUT_EVAL_TOKEN"


class HoldoutSealed(PermissionError):
    pass


def entry(market_id: str, as_of: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{market_id}|{as_of[:10]}".encode()).hexdigest()


def candidates(store, market_ids) -> list[tuple[str, str]]:
    used = {(r[0], r[1][:10]) for r in store.conn.execute("SELECT market_id, as_of FROM investigations")}
    origins = [f"{y}-{m}-23" for y in (2023, 2024, 2025) for m in ("02", "05", "08", "11")]
    origins = [o for o in origins if "2023-08-23" <= o <= "2025-05-23"]
    return [(m, o) for m in market_ids for o in origins if (m, o) not in used]


def write_seal(pairs, salt: str, path: Path = SEAL) -> dict:
    body = {"version": VERSION, "status": "sealed", "salt": salt, "entries": sorted(entry(m, o, salt) for m, o in pairs),
            "n": len(pairs), "eval_token_sha256": None,
            "unseal_condition": "the founder records eval_token_sha256 (freeze) after strategist v3 and critic improvements "
                                "are frozen; one evaluation per frozen version"}
    body["seal_sha256"] = hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != "seal_sha256"},
                                                    sort_keys=True).encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    return body


def guard(market_id: str, as_of: str, path: Path = SEAL) -> None:
    """Refuse a sealed (market, origin) unless the evaluation token matches the token recorded at freeze time."""
    if not path.exists():
        return
    seal = json.loads(path.read_text())
    if entry(market_id, as_of, seal["salt"]) not in set(seal["entries"]):
        return
    tok = os.environ.get(TOKEN_ENV, "")
    if seal.get("eval_token_sha256") and hashlib.sha256(tok.encode()).hexdigest() == seal["eval_token_sha256"]:
        return
    raise HoldoutSealed(f"{market_id} as of {as_of[:10]} is a SEALED holdout ({seal['version']}): not usable for "
                        "development; it opens only for the frozen evaluation")
