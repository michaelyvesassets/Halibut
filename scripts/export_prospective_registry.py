"""Export a run's prospective (pending) test registrations to a small, tamper-evident JSON in git (registry/prospective/).

Durable record of what was predicted, when, on which evidence and under which frozen rules. It contains no credentials
and no bulky runtime data. Each entry repeats the stored spec and its sha256, so any later edit is detectable
(`verify` recomputes every hash against the store).

Usage: python scripts/export_prospective_registry.py <run_dir/r2s...> [--verify]
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def export(exp_dir: Path, db="data/halibut.sqlite") -> dict:
    rep = json.loads((exp_dir / "report.json").read_text())
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    docs = {d["investigation_id"]: d for d in (json.loads(p.read_text()) for p in (exp_dir / "investigations").glob("*.json"))}
    snaps = [dict(r) for r in c.execute("""SELECT dataset_id, retrieved_at, publisher_updated, sha256 FROM snapshots
        WHERE dataset_id LIKE 'dft_veh0160%' AND retrieved_at <= ? ORDER BY retrieved_at DESC LIMIT 2""",
                                         (rep["publication_check"]["checked_at"],))]
    tests = []
    for p in rep["summary"]["prospective_pending_tests"]:
        row = c.execute("SELECT * FROM hypothesis_tests WHERE test_id=?", (p["test"],)).fetchone()
        spec = json.loads(row["spec_json"])
        assert _h(spec) == row["spec_hash"] == p["spec_hash"], f"spec hash mismatch for {p['test']}"
        d = docs[p["investigation"]]
        h = next(x for x in d["stages"]["hypotheses"] if x["id"] == row["hypothesis_claim_id"])
        tests.append({"test_id": p["test"], "investigation_id": p["investigation"], "hypothesis": h["data"]["ref"],
                      "hypothesis_statement": h["label"], "implication_ref": row["implication_ref"],
                      "horizon": p["horizon"], "window_classes": p["window_classes"], "registered_at": row["registered_at"],
                      "spec": spec, "spec_sha256": row["spec_hash"]})
    body = {"experiment_id": rep["experiment_id"], "criteria_hash": rep["criteria_hash"],
            "prospective_protocol": rep["prospective_protocol"], "publication_check": rep["publication_check"],
            "evidence_snapshots": snaps,
            "investigations": {i: {"content_hash": d["content_hash"], "panel_fingerprint": d["evidence"]["panel"]["fingerprint"],
                                   "as_of": d["objective"]["as_of"], "contract": d["provenance"]["prompt_version"]}
                               for i, d in docs.items() if any(t["investigation_id"] == i for t in tests)},
            "tests": tests}
    body["registry_sha256"] = _h(body)
    return body


if __name__ == "__main__":
    exp = Path(sys.argv[1])
    reg = export(exp)
    out = Path("registry/prospective") / f"{reg['experiment_id']}.json"
    if "--verify" in sys.argv:
        old = json.loads(out.read_text())
        print("VERIFIED" if old == reg else "MISMATCH")
        raise SystemExit(0 if old == reg else 1)
    out.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
    print(f"{out}: {len(reg['tests'])} tests, registry sha256 {reg['registry_sha256'][:16]}")
