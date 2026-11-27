"""Unified prediction ledger and first-publication resolution (Architecture V2 milestone 1), fully offline.

Later DfT releases are simulated by re-ingesting a modified copy of the VEH0160 fixture through the normal pipeline:
- release 2 (2027-02-20) adds 2026-Q3 and 2026-Q4;
- release 3 (2027-05-20) revises 2026-Q3.

The bitemporal store records each value with its own availability, exactly as a real refresh would.
"""

import csv
import io
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from halibut.data.providers.base import register
from halibut.data.store import Store
from halibut.epistemic import predictions as P

from .test_intel import MARKET, routes
from .test_providers import FakeNet
from .test_r2_loop import MEASUREMENT

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "intel"


def _release(base_csv: bytes, new_cols: dict, revise: dict | None = None) -> bytes:
    """new_cols: {'2026 Q3': factor, ...} (value = 2026 Q2 x factor); revise: {'2026 Q3': factor} applied on top."""
    rows = list(csv.reader(io.StringIO(base_csv.decode("utf-8-sig"))))
    head, body = rows[0], rows[1:]
    q2 = head.index("2026 Q2")
    cols = list(new_cols)
    out = [head[:5] + cols + head[5:]]
    for i, r in enumerate(body):
        v = r[q2].strip()
        vals = []
        for c in cols:
            if v in ("", "[c]", "[x]", "[z]"):
                vals.append(v)
                continue
            f = new_cols[c] * (1 + 0.02 * (i % 5))  # rows grow at different rates, so shares move
            if revise and c in revise:
                f *= revise[c]
            vals.append(str(round(float(v) * f)))
        out.append(r[:5] + vals + r[5:])
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(out)
    return buf.getvalue().encode()


def _content(updated: str) -> bytes:
    d = json.loads((FIX / "dft_content.json").read_text())
    d["public_updated_at"] = updated
    return json.dumps(d).encode()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A store with a PROSPECTIVE loop.v2 investigation registered on the fixture (data through 2026-Q2)."""
    from halibut.intel.workflow import investigate
    from halibut.investigation.loop import run_investigation
    tmp = tmp_path_factory.mktemp("pred")
    (tmp / "markets").mkdir()
    (tmp / "markets" / "m.toml").write_text(MARKET + MEASUREMENT)
    store = Store(tmp / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    investigate(store, tmp / "markets" / "m.toml", tmp / "out", tmp / "raw", log=lambda *_: None, transport=FakeNet(routes()))
    doc = run_investigation(store, tmp / "markets" / "m.toml", contract="loop.v2", measurements="extended", prospective=True,
                            log=lambda *_: None)
    store.close()
    return tmp, doc


def _copy(world, tmp_path):
    src = world[0] / "s.sqlite"
    a, b = sqlite3.connect(src), sqlite3.connect(tmp_path / "s.sqlite")
    a.backup(b)
    a.close(), b.close()
    return Store(tmp_path / "s.sqlite")


def _publish(store, world, csv_bytes, updated):
    """A later DfT release, ingested through the normal DfT provider path (snapshot, revisions, availability)."""
    from halibut.data.providers import dft
    from halibut.intel import workflow as W
    from halibut.intel.market import load_market
    m = load_market(world[0] / "markets" / "m.toml")
    client = W.PoliteClient("dft", W.Budget(max_requests=10, max_bytes=50_000_000, max_seconds=60), store, min_interval=0,
                            retries=0, run_id=f"test-release-{updated}", sleep=lambda s: None,
                            transport=FakeNet({**routes(), "api/content": _content(updated), "df_VEH0160_UK.csv": csv_bytes}))
    dft.ingest(store, client, world[0] / "raw2", {c.dft_make: f"brand:{c.id}" for c in m.competitors if c.dft_make})


def test_sync_registers_immutable_prospective_predictions(world, tmp_path):
    store = _copy(world, tmp_path)
    mk = world[0] / "markets"
    assert P.sync(store, mk) == {"added": 0, "already_registered": 0}  # scripted workforces are skipped by default
    first = P.sync(store, mk, include_scripted=True)
    assert first["added"] > 0 and P.sync(store, mk, include_scripted=True) == {"added": 0, "already_registered": first["added"]}
    rows = P.current(store)
    binary = [x for x in rows if x["kind"] == "binary"]
    assert binary and {x["horizon"] for x in binary} == {"nominal", "future_window"}
    assert all(x["temporal_class"] == "prospective" for x in rows)
    assert all(x["provenance"]["prompt_version"] == "loop.v2:extended" and x["provenance"]["panel_fingerprint"] for x in rows)
    r = store.conn.execute("SELECT * FROM predictions LIMIT 1").fetchone()
    assert json.loads(r["baselines_json"]).keys() >= {"persistence", "momentum"} and r["registered_at"] and r["evidence_as_of"]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.conn.execute("UPDATE predictions SET registered_at='2020-01-01T00:00:00Z'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.conn.execute("DELETE FROM predictions")
    nominal = {x["correlation_group"] for x in binary if x["horizon"] == "nominal"}
    future = {x["correlation_group"] for x in binary if x["horizon"] == "future_window"}
    assert nominal == future  # a claim's windows are one correlation group (effective sample size)
    store.close()


def test_resolution_waits_for_first_publication_then_records_revisions(world, tmp_path):
    store = _copy(world, tmp_path)
    mk = world[0] / "markets"
    P.sync(store, mk, include_scripted=True)
    r = P.resolve(store, mk)  # today: 2026-Q3/Q4 are not published
    assert set(r["status_counts"]) == {"pending"} and r["api_cost_usd"] == 0.0
    base = (FIX / "dft_veh0160_uk_slice.csv").read_bytes()
    rel2 = _release(base, {"2026 Q4": 1.05, "2026 Q3": 1.10})
    _publish(store, world, rel2, "2026-12-20T09:30:00+00:00")  # the next quarterly release
    early = P.resolve(store, mk, now="2026-12-15T00:00:00Z")  # data in the store, but not yet published at 'now'
    assert set(early["status_counts"]) == {"pending"}
    r2 = P.resolve(store, mk, now="2027-01-05T00:00:00Z")
    rows = P.current(store)
    nom = [x for x in rows if x["horizon"] in ("nominal", "standard")]
    fut = [x for x in rows if x["horizon"] == "future_window"]
    assert nom and all(x["status"] in ("resolved", "ambiguous", "unavailable") for x in nom)
    assert fut and all(x["status"] == "pending" for x in fut)  # 2027-Q1/Q2: genuinely future, untouched
    res = [x for x in nom if x["status"] == "resolved"]
    assert res and all(x["availability_basis"] == "first_capture_inferred" for x in res)  # absent before, timely release
    for x in res:
        if x["kind"] == "binary":
            assert x["outcome"] in ("passed", "failed") and "brier" not in x["scores"] and "categorical_brier" not in x["scores"]
            assert "momentum_predicts" in x["scores"] and "informative_vs_baselines" in x["scores"]
        else:
            assert {"abs_error", "sq_error"} <= set(x["scores"]) and "momentum_abs_error" in x["scores"]
    n = store.conn.execute("SELECT COUNT(*) FROM prediction_resolutions").fetchone()[0]
    assert P.resolve(store, mk, now="2027-01-05T00:00:00Z")["new_records"] == 0  # idempotent
    assert store.conn.execute("SELECT COUNT(*) FROM prediction_resolutions").fetchone()[0] == n
    firsts = {x["prediction_id"]: (x["realized"], x["outcome"]) for x in res}
    rel3 = _release(base, {"2026 Q4": 1.05, "2026 Q3": 1.10}, revise={"2026 Q3": 1.40})
    _publish(store, world, rel3, "2027-03-20T09:30:00+00:00")
    P.resolve(store, mk, now="2027-04-01T00:00:00Z")
    after = {x["prediction_id"]: x for x in P.current(store)}
    assert all((after[k]["realized"], after[k]["outcome"]) == v for k, v in firsts.items())  # the first publication stands
    revs = store.conn.execute("SELECT * FROM prediction_resolutions WHERE vintage='revision'").fetchall()
    assert revs and all(v["evidence_as_of"] >= "2027-03-20" for v in revs)
    assert P.resolve(store, mk, now="2027-04-01T00:00:00Z")["new_records"] == 0
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("UPDATE prediction_resolutions SET outcome='passed'")
    ev = store.conn.execute("SELECT COUNT(*) FROM events_log WHERE event_type='predictions.resolution_run'").fetchone()[0]
    assert ev >= 5  # every run is audited
    s = P.summary(store)
    assert any(g["temporal_class"] == "prospective" and g["unique_realised_outcomes"] for g in s["groups"])
    store.close()


def _synthetic(store, base_row, **over):
    r = dict(base_row)
    q = json.loads(r["question_json"])
    q.update(over.pop("question", {}))
    r.update(over, question_json=json.dumps(q, sort_keys=True), question_sha256=P._h(q))
    r["prediction_id"] = "pq:synthetic" + P._h([r["question_sha256"], r["registered_at"]])[:12]
    r["source_id"] = r["prediction_id"]
    store.conn.execute(f"INSERT INTO predictions VALUES ({','.join('?' * len(r))})", tuple(r.values()))
    return r["prediction_id"]


def test_typed_invalid_ambiguous_and_unavailable_outcomes(world, tmp_path):
    store = _copy(world, tmp_path)
    mk = world[0] / "markets"
    P.sync(store, mk, include_scripted=True)
    base = (FIX / "dft_veh0160_uk_slice.csv").read_bytes()
    _publish(store, world, _release(base, {"2026 Q4": 1.05, "2026 Q3": 1.10}), "2026-12-20T09:30:00+00:00")
    P.resolve(store, mk, now="2027-01-05T00:00:00Z")
    done = next(x for x in P.current(store) if x["kind"] == "binary" and x["status"] == "resolved")
    row = dict(store.conn.execute("SELECT * FROM predictions WHERE prediction_id=?", (done["prediction_id"],)).fetchone())
    late = _synthetic(store, row, registered_at="2026-12-25T00:00:00Z")  # 'prospective' after the outcome was published
    tie = _synthetic(store, row, question={"reference_value": done["realized"]}, registered_at="2026-11-28T00:00:00Z")
    gone = _synthetic(store, row, question={"entity": "no_such_brand"}, registered_at="2026-11-29T00:00:00Z")
    store.conn.commit()
    P.resolve(store, mk, now="2027-01-06T00:00:00Z")
    st = {x["prediction_id"]: x["status"] for x in P.current(store)}
    assert st[late] == "invalid" and st[tie] == "ambiguous" and st[gone] == "unavailable"
    changed = tmp_path / "changed"
    shutil.copytree(mk, changed)
    (changed / "m.toml").write_text((mk / "m.toml").read_text() + "\n# edited definition\n")
    P.resolve(store, changed, now="2027-01-07T00:00:00Z")
    hist = store.conn.execute("SELECT status FROM prediction_resolutions WHERE detail LIKE 'market definition changed%'").fetchall()
    assert hist  # never resolved against a different market definition
    store.close()


def test_cli_refuses_future_resolution_time(world, tmp_path, capsys):
    from halibut.cli import main
    _copy(world, tmp_path).close()
    assert main(["predictions", "--db", str(tmp_path / "s.sqlite"), "--markets", str(world[0] / "markets"), "resolve",
                 "--now", "2099-01-01T00:00:00Z"]) == 2
    assert main(["predictions", "--db", str(tmp_path / "s.sqlite"), "--markets", str(world[0] / "markets"), "sync",
                 "--include-scripted"]) == 0
    assert main(["predictions", "--db", str(tmp_path / "s.sqlite"), "--markets", str(world[0] / "markets"), "resolve"]) == 0
    assert '"pending"' in capsys.readouterr().out
    out = tmp_path / "reg"
    assert main(["predictions", "--db", str(tmp_path / "s.sqlite"), "export", "--out", str(out)]) == 0
    a = (out / "ledger.json").read_text()
    assert main(["predictions", "--db", str(tmp_path / "s.sqlite"), "export", "--out", str(out)]) == 0
    assert (out / "ledger.json").read_text() == a and json.loads(a)["predictions"]  # deterministic


def test_scores_are_proper():
    q = {"point": 0.10, "interval80": [0.10 - P.Z80 * 0.01, 0.10 + P.Z80 * 0.01], "sd": 0.01}
    near, far = P.score_numeric(q, 0.10, {}), P.score_numeric(q, 0.13, {})
    assert near["crps_gaussian"] < far["crps_gaussian"] and near["log_score_gaussian"] > far["log_score_gaussian"]
    assert near["in_interval80"] and not far["in_interval80"]
    clipped = P.score_numeric({**q, "interval80": [0.0, 0.11]}, 0.10, {})  # interval clipped at 0: not that Gaussian
    assert "crps_gaussian" not in clipped and "not applied" in clipped["probabilistic_scores"]
    assert "crps_gaussian" not in P.score_numeric({"point": 0.1, "interval80": None, "sd": None}, 0.1, {})
    b = P.score_binary({"probability": 0.8}, True, {})
    assert b["brier"] == pytest.approx(0.04) and b["log_score"] == pytest.approx(-0.2231, abs=1e-4)
    c = P.score_binary({"probability": None}, False, {"momentum": {"predicts": True}, "persistence": {"predicts": None}})
    assert "brier" not in c and "categorical_brier" not in c and "not applied" in c["probabilistic_scores"]
    assert c["momentum_hit"] is False and c["persistence_hit"] is None


def test_a_missed_release_never_passes_as_first_publication(world, tmp_path):
    """If our first capture of a quarter follows an unseen release (gap > one quarterly cycle), the original value may
    differ: the resolution is flagged first_capture_unverified, never presented as a verified first publication."""
    store = _copy(world, tmp_path)
    mk = world[0] / "markets"
    P.sync(store, mk, include_scripted=True)
    base = (FIX / "dft_veh0160_uk_slice.csv").read_bytes()
    _publish(store, world, _release(base, {"2026 Q4": 1.05, "2026 Q3": 1.10}), "2027-06-20T09:30:00+00:00")  # 270 days on
    P.resolve(store, mk, now="2027-07-01T00:00:00Z")
    res = [x for x in P.current(store) if x["status"] == "resolved"]
    assert res and {x["availability_basis"] for x in res} == {"first_capture_unverified"}
    s = P.summary(store)
    assert all(set(g["resolution_basis"]) <= {"first_capture_unverified"} for g in s["groups"] if g["resolution_basis"])
    store.close()


def test_backfilled_history_stays_estimated_and_groups_do_not_inflate_samples(world, tmp_path):
    """Retrospective questions over backfilled history are 'estimated_lag', never verified; predictions resolved by the
    same realised value count once (outcome groups)."""
    from halibut.investigation.loop import run_investigation
    store = _copy(world, tmp_path)
    mk = world[0] / "markets"
    run_investigation(store, mk / "m.toml", as_of="2025-05-23", evaluate_as_of="2026-11-23", contract="loop.v2",
                      measurements="extended", log=lambda *_: None)
    P.sync(store, mk, include_scripted=True)
    P.resolve(store, mk)
    retro = [x for x in P.current(store) if x["temporal_class"] == "retrospective" and x["status"] == "resolved"]
    assert retro and {x["availability_basis"] for x in retro} == {"estimated_lag"}
    groups = {x["outcome_group"] for x in retro}
    assert len(groups) < len(retro)  # e.g. rival hypotheses tested on the same quantity and window
    g = next(g for g in P.summary(store)["groups"] if g["temporal_class"] == "retrospective" and g["kind"] == "binary")
    assert g["unique_realised_outcomes"] <= g["resolved_claim_groups"] <= g["predictions"]
    store.close()


def test_registration_and_resolution_are_reproducible(world, tmp_path):
    """Two independent copies give identical prediction ids and identical resolution ids (timestamps aside)."""
    out = []
    for name in ("a", "b"):
        d = tmp_path / name
        d.mkdir()
        st = _copy(world, d)
        P.sync(st, world[0] / "markets", include_scripted=True)
        P.resolve(st, world[0] / "markets", now="2026-12-01T00:00:00Z")
        out.append((sorted(r[0] for r in st.conn.execute("SELECT prediction_id FROM predictions")),
                    sorted(r[0] for r in st.conn.execute("SELECT resolution_id FROM prediction_resolutions"))))
        st.close()
    assert out[0] == out[1] and out[0][0]


def test_registered_r2_prospective_tests_are_recoverable_from_git():
    """The committed registry alone recovers all 12 R2 prospective tests, their classes and their evidence."""
    import gzip
    import hashlib
    reg = json.loads((ROOT / "registry" / "prospective" / "r2s2b-33f66362ef91.json").read_text())
    body = {k: v for k, v in reg.items() if k != "registry_sha256"}
    assert P._h(body) == reg["registry_sha256"]  # tamper-evident
    t = reg["tests"]
    assert len(t) == 12 and all(P._h(x["spec"]) == x["spec_sha256"] for x in t)
    fut = [x for x in t if x["horizon"] == "future_window"]
    nom = [x for x in t if x["horizon"] == "nominal"]
    assert len(fut) == len(nom) == 6
    assert all(set(x["window_classes"].values()) == {"future"} for x in fut)
    assert all(x["window_classes"] == {"2026-Q3": "unpublished_historical", "2026-Q4": "partially_elapsed"} for x in nom)
    assert all(x["registered_at"].startswith("2026-11-27T12:1") for x in t)
    assert reg["criteria_hash"].startswith("223362694f7d254f")
    for s_ in reg["evidence_snapshots"]:
        raw = gzip.decompress((ROOT / s_["archive"]).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == s_["sha256"]  # the evidence itself is recoverable
