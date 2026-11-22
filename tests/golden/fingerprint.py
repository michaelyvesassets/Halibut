import hashlib
import json


def fingerprint(rows: list[dict], keys: list[str]) -> str:
    """SHA-256 of the Phase 1 metric columns, so added metrics don't break it."""
    canon = [{k: r[k] for k in keys} for r in rows]
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()
