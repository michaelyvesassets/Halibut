"""Bitemporal observation store (SQLite now; Postgres/Supabase-compatible schema).

Every observation has two time axes:

- the *observation period* it describes (``period``, ``period_start``,
  ``period_end``);
- the time it became publicly available (``available_at``), with
  ``availability_basis`` saying how that time is known:

  ``published``      publisher's release timestamp seen at retrieval
  ``estimated_lag``  conservative estimate: period end + documented lag
                     (used for history present in the first retrieval)
  ``retrieval``      only the retrieval time is known

Values are never updated in place. A changed value becomes a new
``revision`` whose ``available_at`` is the release/retrieval that revealed
it, so ``as_of(t)`` reconstructs what was knowable at ``t`` (to the extent
that revisions were captured; revisions made before our first retrieval are
invisible: see ``docs/phase3_data.md``).

All timestamps are UTC ISO-8601 strings ``YYYY-MM-DDTHH:MM:SSZ``; all dates
``YYYY-MM-DD``. Lexicographic order equals time order.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from ..config import ConfigError

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    publisher TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    licence TEXT NOT NULL,
    licence_url TEXT NOT NULL,
    attribution TEXT NOT NULL,
    redistribution_allowed INTEGER NOT NULL,
    access TEXT NOT NULL,
    verified TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE datasets (
    dataset_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    evidence_class TEXT NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency IN ('M','Q','A')),
    unit TEXT NOT NULL,
    definition TEXT NOT NULL,
    publication_lag_days INTEGER NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE series (
    series_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
    universe_key TEXT NOT NULL,
    dims_json TEXT NOT NULL,
    labels_json TEXT NOT NULL
);
CREATE TABLE geographies (
    geo_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    level TEXT NOT NULL,
    scheme TEXT NOT NULL,
    code TEXT NOT NULL
);
CREATE TABLE snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
    retrieved_at TEXT NOT NULL,
    publisher_updated TEXT,
    request_json TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL
);
CREATE TABLE observations (
    obs_id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id TEXT NOT NULL REFERENCES series(series_id),
    geo_id TEXT NOT NULL REFERENCES geographies(geo_id),
    period TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    value REAL,
    status TEXT,
    revision INTEGER NOT NULL,
    available_at TEXT NOT NULL,
    availability_basis TEXT NOT NULL CHECK (availability_basis IN ('published','estimated_lag','retrieval')),
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
    UNIQUE (series_id, geo_id, period, revision)
);
CREATE INDEX obs_lookup ON observations(series_id, geo_id, period, available_at);
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    geo_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    effective_date TEXT,
    announced_date TEXT,
    available_at TEXT NOT NULL,
    verification TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE ingest_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    snapshot_id INTEGER,
    n_new INTEGER, n_revised INTEGER, n_unchanged INTEGER,
    message TEXT
);
"""),
    (2, """
CREATE TABLE fetch_log (
    fetch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER,
    bytes INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    run_id TEXT
);
CREATE TABLE entities (
    entity_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('company','brand','product','product_line','location','place','industry','market','segment')),
    name TEXT NOT NULL,
    epistemic_status TEXT NOT NULL CHECK (epistemic_status IN ('observed','derived','asserted')),
    produced_by TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE entity_identifiers (
    entity_id TEXT NOT NULL REFERENCES entities(entity_id),
    scheme TEXT NOT NULL,
    value TEXT NOT NULL,
    produced_by TEXT NOT NULL,
    PRIMARY KEY (scheme, value, entity_id)
);
CREATE TABLE relationships (
    rel_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES entities(entity_id),
    predicate TEXT NOT NULL,
    object_id TEXT NOT NULL REFERENCES entities(entity_id),
    valid_from TEXT,
    valid_to TEXT,
    epistemic_status TEXT NOT NULL CHECK (epistemic_status IN ('observed','derived','asserted')),
    produced_by TEXT NOT NULL,
    snapshot_id INTEGER REFERENCES snapshots(snapshot_id),
    available_at TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE documents (
    doc_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    source_domain TEXT,
    published_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    tone REAL,
    snapshot_id INTEGER REFERENCES snapshots(snapshot_id),
    evidence_class TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE document_entities (
    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
    entity_id TEXT NOT NULL REFERENCES entities(entity_id),
    matched_alias TEXT NOT NULL,
    PRIMARY KEY (doc_id, entity_id)
);
CREATE TABLE entity_observations (
    series_id TEXT NOT NULL REFERENCES series(series_id),
    entity_id TEXT NOT NULL REFERENCES entities(entity_id),
    role TEXT NOT NULL,
    PRIMARY KEY (series_id, entity_id, role)
);
CREATE TABLE intel_runs (
    run_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE hypotheses (
    hypothesis_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES intel_runs(run_id),
    status TEXT NOT NULL CHECK (status IN ('proposed','rejected','accepted_for_testing','tested_supported','tested_refuted')),
    produced_by TEXT NOT NULL,
    record_json TEXT NOT NULL
);
"""),
    (3, """
CREATE TABLE experiments (
    experiment_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('planned','running','completed','failed')),
    produced_by TEXT NOT NULL,
    baseline_id TEXT REFERENCES experiments(experiment_id),
    rationale TEXT,
    expected_improvement_json TEXT,
    change_json TEXT,
    lineage_json TEXT NOT NULL,
    metrics_json TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE knowledge (
    knowledge_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('hypothesis','prediction','outcome','lesson','critique','question','proposal')),
    statement TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed','tested','validated','rejected','inconclusive','superseded')),
    produced_by TEXT NOT NULL,
    experiment_id TEXT REFERENCES experiments(experiment_id),
    evaluation_id TEXT,
    scope_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    confidence TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE knowledge_links (
    from_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('supports','contradicts','critiques','derived_from','tests','supersedes','considered')),
    to_id TEXT NOT NULL,
    produced_by TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_id, relation, to_id, produced_by)
);
CREATE TABLE evaluations (
    evaluation_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    baseline_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    rule_json TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('improved','degraded','inconclusive')),
    decision TEXT NOT NULL CHECK (decision IN ('eligible_for_promotion','reject','hold')),
    details_json TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    decided_by TEXT NOT NULL
);
CREATE TABLE promotions (
    promotion_id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    evaluation_id TEXT NOT NULL REFERENCES evaluations(evaluation_id),
    status TEXT NOT NULL CHECK (status IN ('active','rolled_back')),
    approved_by TEXT NOT NULL,
    previous_promotion_id INTEGER,
    at TEXT NOT NULL,
    note TEXT
);
"""),
    (4, """
ALTER TABLE knowledge ADD COLUMN available_at TEXT;
CREATE TABLE prediction_scores (
    prediction_id TEXT NOT NULL,
    eval_as_of TEXT NOT NULL,
    realized REAL,
    point REAL,
    error REAL,
    abs_error REAL,
    in_interval80 INTEGER,
    interval_score80 REAL,
    direction_hit INTEGER,
    brier REAL,
    method TEXT NOT NULL,
    produced_by TEXT NOT NULL,
    origin_as_of TEXT NOT NULL,
    outcome_available_at TEXT NOT NULL,
    outcome_id TEXT,
    record_json TEXT NOT NULL,
    PRIMARY KEY (prediction_id, eval_as_of)
);
CREATE TABLE llm_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    response_json TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE agent_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    agent TEXT NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    tokens_estimated INTEGER NOT NULL DEFAULT 1,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    latency_seconds REAL,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    prompt_hash TEXT NOT NULL,
    tool_calls_json TEXT,
    duplicate_tool_calls INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL,
    at TEXT NOT NULL
);
"""),
    (5, """
CREATE TABLE knowledge_status_history (
    knowledge_id TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    evaluation_id TEXT,
    basis TEXT NOT NULL
);
CREATE INDEX ksh_lookup ON knowledge_status_history(knowledge_id, effective_at);
INSERT INTO knowledge_status_history (knowledge_id, status, effective_at, recorded_at, changed_by, evaluation_id, basis)
    SELECT knowledge_id, status, COALESCE(available_at, created_at), updated_at, 'migration_5', evaluation_id, 'backfilled_current_status'
    FROM knowledge;
CREATE TABLE llm_spend_ledger (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    run_id TEXT NOT NULL,
    reserved_usd REAL NOT NULL,
    actual_usd REAL,
    status TEXT NOT NULL CHECK (status IN ('reserved','settled','failed_charged','released')),
    created_at TEXT NOT NULL,
    settled_at TEXT,
    note TEXT
);
"""),
    # 6: Epistemic Integrity (Master Architecture v1.0, R1). Typed claims, validation records and a
    # hash-chained event log. All four tables are append-only (enforced by triggers): status changes
    # are new history rows, corrections are new claims linked with 'supersedes'.
    (6, """
CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY,
    claim_type TEXT NOT NULL CHECK (claim_type IN ('observation','derived_metric','estimate','assumption','explanation',
        'hypothesis','forecast','simulated_outcome','critique','strategic_proposal','experiment_result','lesson')),
    statement TEXT NOT NULL,
    produced_by TEXT NOT NULL,
    run_id TEXT,
    tenant TEXT NOT NULL DEFAULT 'public',
    as_of TEXT NOT NULL,
    available_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX claims_lookup ON claims(claim_type, tenant, available_at);
CREATE INDEX claims_run ON claims(run_id);
CREATE INDEX claims_hash ON claims(content_hash);
CREATE TABLE claim_links (
    from_id TEXT NOT NULL REFERENCES claims(claim_id),
    relation TEXT NOT NULL CHECK (relation IN ('supports','contradicts','derived_from','tests','critiques','supersedes',
        'duplicates','explains')),
    to_id TEXT NOT NULL,
    produced_by TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (from_id, relation, to_id)
);
CREATE TABLE claim_status_history (
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    status TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    reason TEXT
);
CREATE INDEX csh_lookup ON claim_status_history(claim_id, effective_at);
CREATE TABLE validation_records (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    tenant TEXT NOT NULL DEFAULT 'public',
    produced_by TEXT NOT NULL,
    subject TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('call','schema','conversion','validation','ledger')),
    outcome TEXT NOT NULL CHECK (outcome IN ('rejected','dropped','failed')),
    failure_class TEXT NOT NULL,
    problems_json TEXT NOT NULL,
    item_json TEXT NOT NULL,
    as_of TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX vr_run ON validation_records(run_id);
CREATE TABLE events_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    layer TEXT NOT NULL,
    run_id TEXT,
    tenant TEXT NOT NULL DEFAULT 'public',
    subject_id TEXT,
    payload_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    at TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);
CREATE INDEX events_type ON events_log(event_type, seq);
CREATE TRIGGER claims_no_update BEFORE UPDATE ON claims BEGIN SELECT RAISE(ABORT, 'claims are append-only'); END;
CREATE TRIGGER claims_no_delete BEFORE DELETE ON claims BEGIN SELECT RAISE(ABORT, 'claims are append-only'); END;
CREATE TRIGGER csh_no_update BEFORE UPDATE ON claim_status_history BEGIN SELECT RAISE(ABORT, 'claim status history is append-only'); END;
CREATE TRIGGER csh_no_delete BEFORE DELETE ON claim_status_history BEGIN SELECT RAISE(ABORT, 'claim status history is append-only'); END;
CREATE TRIGGER vr_no_update BEFORE UPDATE ON validation_records BEGIN SELECT RAISE(ABORT, 'validation records are append-only'); END;
CREATE TRIGGER vr_no_delete BEFORE DELETE ON validation_records BEGIN SELECT RAISE(ABORT, 'validation records are append-only'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events_log BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events_log BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
ALTER TABLE agent_calls ADD COLUMN response_excerpt TEXT;
"""),
    # 7: R2 intelligence loop. Approvals (requests + decisions), the model registry, pre-registered
    # hypothesis tests and their results, and investigation results. All append-only.
    (7, """
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('paid_run','experiment','external_action','promotion','new_source')),
    subject TEXT NOT NULL,
    tenant TEXT NOT NULL DEFAULT 'public',
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    max_usd REAL,
    expires_at TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE approval_decisions (
    approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
    decision TEXT NOT NULL CHECK (decision IN ('granted','denied','revoked')),
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    note TEXT,
    evidence_json TEXT NOT NULL
);
CREATE TABLE model_versions (
    model_version_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    family_version TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_json TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    as_of TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('validated','unvalidated','rejected')),
    validation_json TEXT NOT NULL,
    registered_by TEXT NOT NULL,
    run_id TEXT,
    registered_at TEXT NOT NULL
);
CREATE TABLE hypothesis_tests (
    test_id TEXT PRIMARY KEY,
    hypothesis_claim_id TEXT NOT NULL,
    implication_ref TEXT NOT NULL,
    run_id TEXT,
    as_of TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('registered','not_testable')),
    spec_json TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    registered_at TEXT NOT NULL
);
CREATE TABLE test_results (
    result_id TEXT PRIMARY KEY,
    test_id TEXT NOT NULL REFERENCES hypothesis_tests(test_id),
    evaluated_as_of TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('passed','failed','pending')),
    available_at TEXT,
    result_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE investigations (
    investigation_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    tenant TEXT NOT NULL DEFAULT 'public',
    as_of TEXT NOT NULL,
    evaluate_as_of TEXT,
    status TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER approvals_no_update BEFORE UPDATE ON approvals BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
CREATE TRIGGER approvals_no_delete BEFORE DELETE ON approvals BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON approval_decisions BEGIN SELECT RAISE(ABORT, 'approval decisions are append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON approval_decisions BEGIN SELECT RAISE(ABORT, 'approval decisions are append-only'); END;
CREATE TRIGGER models_no_update BEFORE UPDATE ON model_versions BEGIN SELECT RAISE(ABORT, 'model versions are append-only'); END;
CREATE TRIGGER models_no_delete BEFORE DELETE ON model_versions BEGIN SELECT RAISE(ABORT, 'model versions are append-only'); END;
CREATE TRIGGER tests_no_update BEFORE UPDATE ON hypothesis_tests BEGIN SELECT RAISE(ABORT, 'registered tests are append-only'); END;
CREATE TRIGGER tests_no_delete BEFORE DELETE ON hypothesis_tests BEGIN SELECT RAISE(ABORT, 'registered tests are append-only'); END;
CREATE TRIGGER results_no_update BEFORE UPDATE ON test_results BEGIN SELECT RAISE(ABORT, 'test results are append-only'); END;
CREATE TRIGGER results_no_delete BEFORE DELETE ON test_results BEGIN SELECT RAISE(ABORT, 'test results are append-only'); END;
CREATE TRIGGER inv_no_update BEFORE UPDATE ON investigations BEGIN SELECT RAISE(ABORT, 'investigations are append-only'); END;
CREATE TRIGGER inv_no_delete BEFORE DELETE ON investigations BEGIN SELECT RAISE(ABORT, 'investigations are append-only'); END;
"""),
    # 8: single-use approvals. A paid_run approval is bound to exactly one use (one experiment or one investigation
    # spend scope); the primary key makes a second use impossible.
    (8, """
CREATE TABLE approval_uses (
    approval_id TEXT PRIMARY KEY REFERENCES approvals(approval_id),
    use_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    used_at TEXT NOT NULL
);
CREATE TRIGGER uses_no_update BEFORE UPDATE ON approval_uses BEGIN SELECT RAISE(ABORT, 'approval uses are append-only'); END;
CREATE TRIGGER uses_no_delete BEFORE DELETE ON approval_uses BEGIN SELECT RAISE(ABORT, 'approval uses are append-only'); END;
"""),
    # 9: unified prediction ledger (Architecture V2, milestone 1). Every registered prediction (hypothesis tests,
    # official forecasts, estimates) under one immutable question record, and an append-only resolution history.
    (9, """
CREATE TABLE predictions (
    prediction_id TEXT PRIMARY KEY,          -- stable: hash of kind, source id and spec hash
    kind TEXT NOT NULL CHECK (kind IN ('binary', 'numeric')),
    source TEXT NOT NULL,                    -- hypothesis_test | forecast | estimate
    source_id TEXT NOT NULL,                 -- test_id or claim id
    investigation_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    question_json TEXT NOT NULL,             -- quantity, target periods, comparator/reference or point/interval/sd, probability
    question_sha256 TEXT NOT NULL,
    resolution_rule TEXT NOT NULL,           -- e.g. first_publication.v1
    horizon TEXT NOT NULL,                   -- nominal | future_window | standard
    temporal_class TEXT NOT NULL,            -- prospective | retrospective
    correlation_group TEXT NOT NULL,         -- same claim and quantity in other windows share a group
    registered_at TEXT NOT NULL,             -- the REAL registration time (never backdated)
    evidence_as_of TEXT NOT NULL,            -- the evidence date the prediction was made on
    evidence_available_at TEXT,              -- latest availability of the evidence it used
    baselines_json TEXT NOT NULL,            -- persistence and momentum, computed from the origin evidence only
    provenance_json TEXT NOT NULL,           -- model, prompt/contract, investigation, panel fingerprint, market spec sha
    cost_json TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    UNIQUE (source, source_id)
);
CREATE TABLE prediction_resolutions (
    resolution_id TEXT PRIMARY KEY,          -- hash of prediction, vintage, evidence time and outcome (idempotent)
    prediction_id TEXT NOT NULL REFERENCES predictions(prediction_id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'resolved', 'invalid', 'ambiguous', 'unavailable')),
    vintage TEXT NOT NULL CHECK (vintage IN ('first_publication', 'revision', 'none')),
    evidence_as_of TEXT,                     -- the vintage used (as-of time of the evidence)
    availability_basis TEXT,                 -- published | estimated_lag | retrieval
    realized REAL,
    outcome TEXT,                            -- binary: passed | failed; numeric: null
    scores_json TEXT NOT NULL,
    detail TEXT NOT NULL,
    resolver_version TEXT NOT NULL,
    resolved_at TEXT NOT NULL
);
CREATE INDEX pres_prediction ON prediction_resolutions(prediction_id);
CREATE TRIGGER pred_no_update BEFORE UPDATE ON predictions BEGIN SELECT RAISE(ABORT, 'predictions are immutable'); END;
CREATE TRIGGER pred_no_delete BEFORE DELETE ON predictions BEGIN SELECT RAISE(ABORT, 'predictions are immutable'); END;
CREATE TRIGGER pres_no_update BEFORE UPDATE ON prediction_resolutions BEGIN SELECT RAISE(ABORT, 'resolutions are append-only'); END;
CREATE TRIGGER pres_no_delete BEFORE DELETE ON prediction_resolutions BEGIN SELECT RAISE(ABORT, 'resolutions are append-only'); END;
"""),
]


def _statements(sql: str) -> list[str]:
    """Split a migration into complete SQL statements (trigger bodies contain ';')."""
    out, buf = [], ""
    for frag in sql.split(";"):
        buf += frag + ";"
        if sqlite3.complete_statement(buf):
            if buf.strip(" \n;"):
                out.append(buf.strip())
            buf = ""
    if buf.strip(" \n;"):
        raise ValueError(f"incomplete SQL statement in migration: {buf[:80]!r}")
    return out


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_timestamp(value: str | date | datetime) -> str:
    """Normalise a date/datetime (or ISO string) to ``YYYY-MM-DDTHH:MM:SSZ`` UTC.

    A bare date means the *end* of that day (conservative for availability).
    """
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return f"{value.isoformat()}T23:59:59Z"
    s = str(value).strip()
    if len(s) == 10:
        date.fromisoformat(s)
        return f"{s}T23:59:59Z"
    return to_timestamp(datetime.fromisoformat(s.replace("Z", "+00:00")))


@dataclass(frozen=True)
class Observation:
    series_id: str
    geo_id: str
    period: str
    period_start: str
    period_end: str
    value: float | None
    status: str | None = None


@dataclass(frozen=True)
class SeriesRecord:
    series_id: str
    dataset_id: str
    universe_key: str
    dims: dict
    labels: dict


class LeakageError(RuntimeError):
    """Raised when code attempts to read data not available at its as-of time."""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = sqlite3.Row
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def migrate(self) -> list[int]:
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        done = {r[0] for r in self.conn.execute("SELECT version FROM schema_migrations")}
        applied = []
        for version, sql in MIGRATIONS:
            if version in done:
                continue
            with self.transaction() as c:
                for stmt in _statements(sql):
                    c.execute(stmt)
                c.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, utc_now()))
            applied.append(version)
        return applied

    # ------------------------------------------------------------------ catalogue

    def register_catalogue(self, cat) -> None:
        from dataclasses import asdict  # noqa: PLC0415
        with self.transaction() as c:
            for s in cat.sources.values():
                c.execute("""INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id) DO UPDATE SET publisher=excluded.publisher, title=excluded.title,
                    url=excluded.url, licence=excluded.licence, licence_url=excluded.licence_url,
                    attribution=excluded.attribution, redistribution_allowed=excluded.redistribution_allowed,
                    access=excluded.access, verified=excluded.verified, record_json=excluded.record_json""",
                          (s.id, s.publisher, s.title, s.url, s.licence, s.licence_url, s.attribution,
                           int(s.redistribution_allowed), s.access, s.verified, json.dumps(asdict(s))))
            for d in cat.datasets.values():
                c.execute("""INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(dataset_id) DO UPDATE SET source_id=excluded.source_id,
                    evidence_class=excluded.evidence_class, frequency=excluded.frequency, unit=excluded.unit,
                    definition=excluded.definition, publication_lag_days=excluded.publication_lag_days,
                    record_json=excluded.record_json""",
                          (d.id, d.source, d.evidence_class, d.frequency, d.unit, d.definition,
                           d.publication_lag_days, json.dumps(asdict(d), default=list)))

    def upsert_series(self, records: Iterable[SeriesRecord]) -> None:
        with self.transaction() as c:
            for r in records:
                row = c.execute("SELECT universe_key, dims_json FROM series WHERE series_id=?", (r.series_id,)).fetchone()
                if row and (row["universe_key"] != r.universe_key or json.loads(row["dims_json"]) != r.dims):
                    raise ConfigError(f"series {r.series_id}: definition changed; refusing to overwrite")
                c.execute("INSERT OR IGNORE INTO series VALUES (?,?,?,?,?)",
                          (r.series_id, r.dataset_id, r.universe_key, json.dumps(r.dims, sort_keys=True),
                           json.dumps(r.labels, sort_keys=True, ensure_ascii=False)))

    def upsert_geographies(self, rows: Iterable[tuple[str, str, str, str, str]]) -> None:
        with self.transaction() as c:
            c.executemany("INSERT OR IGNORE INTO geographies VALUES (?,?,?,?,?)", list(rows))

    # ------------------------------------------------------------------ snapshots & observations

    def add_snapshot(self, dataset_id: str, raw: bytes, request: dict, raw_dir: str | Path,
                     retrieved_at: str | None = None, publisher_updated: str | None = None) -> tuple[int, bool]:
        """Store raw bytes immutably; returns (snapshot_id, is_new). Identical bytes are deduplicated."""
        sha = hashlib.sha256(raw).hexdigest()
        row = self.conn.execute("SELECT snapshot_id FROM snapshots WHERE dataset_id=? AND sha256=?",
                                (dataset_id, sha)).fetchone()
        if row:
            return int(row[0]), False
        retrieved_at = to_timestamp(retrieved_at) if retrieved_at else utc_now()
        stamp = retrieved_at.replace(":", "").replace("-", "")
        path = Path(raw_dir) / dataset_id / f"{stamp}_{sha[:12]}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
        with self.transaction() as c:
            cur = c.execute("INSERT INTO snapshots (dataset_id, retrieved_at, publisher_updated, request_json, raw_path, sha256, bytes) VALUES (?,?,?,?,?,?,?)",
                            (dataset_id, retrieved_at, to_timestamp(publisher_updated) if publisher_updated else None,
                             json.dumps(request, sort_keys=True), str(path), sha, len(raw)))
        return int(cur.lastrowid), True

    def snapshot(self, snapshot_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()

    def load_observations(self, snapshot_id: int, observations: Iterable[Observation], lag_days: int) -> dict:
        """Insert observations from a snapshot, creating revisions for changed values.

        Availability: for the dataset's first snapshot (historical backfill),
        ``period_end + lag_days`` capped at the release/retrieval time; for
        later snapshots, the publisher release time (or retrieval time).
        """
        snap = self.snapshot(snapshot_id)
        dataset_id = snap["dataset_id"]
        first = self.conn.execute("SELECT MIN(snapshot_id) FROM snapshots WHERE dataset_id=?",
                                  (dataset_id,)).fetchone()[0] == snapshot_id
        release = snap["publisher_updated"] or snap["retrieved_at"]
        release_basis = "published" if snap["publisher_updated"] else "retrieval"
        counts = {"new": 0, "revised": 0, "unchanged": 0}
        with self.transaction() as c:
            for o in observations:
                prev = c.execute("""SELECT value, status, revision FROM observations
                    WHERE series_id=? AND geo_id=? AND period=? ORDER BY revision DESC LIMIT 1""",
                                 (o.series_id, o.geo_id, o.period)).fetchone()
                if prev is not None and prev["value"] == o.value and prev["status"] == o.status:
                    counts["unchanged"] += 1
                    continue
                if prev is None and first:
                    est = to_timestamp(date.fromisoformat(o.period_end) + timedelta(days=lag_days))
                    available, basis = (est, "estimated_lag") if est <= release else (release, release_basis)
                    # never earlier than the period itself ends
                    revision = 0
                else:
                    available, basis = release, release_basis
                    revision = 0 if prev is None else prev["revision"] + 1
                counts["new" if prev is None else "revised"] += 1
                c.execute("""INSERT INTO observations (series_id, geo_id, period, period_start, period_end, value,
                    status, revision, available_at, availability_basis, snapshot_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                          (o.series_id, o.geo_id, o.period, o.period_start, o.period_end, o.value, o.status,
                           revision, available, basis, snapshot_id))
        return counts

    # ------------------------------------------------------------------ events

    def upsert_events(self, events: Iterable[dict]) -> None:
        with self.transaction() as c:
            for e in events:
                c.execute("""INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_id) DO UPDATE SET geo_id=excluded.geo_id, category=excluded.category,
                    title=excluded.title, effective_date=excluded.effective_date,
                    announced_date=excluded.announced_date, available_at=excluded.available_at,
                    verification=excluded.verification, record_json=excluded.record_json""",
                          (e["id"], e["geo"], e["category"], e["title"], e.get("effective_date"),
                           e.get("announced_date"), e["available_at"], e["verification"],
                           json.dumps(e, ensure_ascii=False, sort_keys=True)))

    # ------------------------------------------------------------------ queries

    def series_record(self, series_id: str) -> SeriesRecord:
        r = self.conn.execute("SELECT * FROM series WHERE series_id=?", (series_id,)).fetchone()
        if r is None:
            raise KeyError(f"unknown series {series_id!r}")
        return SeriesRecord(r["series_id"], r["dataset_id"], r["universe_key"],
                            json.loads(r["dims_json"]), json.loads(r["labels_json"]))

    def list_series(self, dataset_id: str | None = None) -> list[SeriesRecord]:
        q = "SELECT series_id FROM series" + (" WHERE dataset_id=?" if dataset_id else "") + " ORDER BY series_id"
        return [self.series_record(r[0]) for r in self.conn.execute(q, (dataset_id,) if dataset_id else ())]

    def as_of(self, t: str | date | datetime) -> "AsOfView":
        return AsOfView(self, to_timestamp(t))

    def latest(self) -> "AsOfView":
        """Everything known now. For descriptive reports only: never for backtests."""
        return AsOfView(self, "9999-12-31T23:59:59Z")


class AsOfView:
    """Read-only view of what was publicly available at time ``t``.

    Only the latest revision with ``available_at <= t`` is returned, and only
    events with ``available_at <= t``. ``max_available_at_used`` records the
    newest availability actually read (audited in backtest manifests).
    """

    def __init__(self, store: Store, t: str):
        self._store, self.t = store, t
        self.max_available_at_used: str | None = None

    def _note(self, ts: str | None) -> None:
        if ts and (self.max_available_at_used is None or ts > self.max_available_at_used):
            self.max_available_at_used = ts

    def series(self, series_id: str, geo_id: str) -> list[tuple[str, float | None]]:
        rows = self._store.conn.execute("""
            SELECT o.period, o.value, o.available_at FROM observations o
            JOIN (SELECT period, MAX(revision) AS rev FROM observations
                  WHERE series_id=? AND geo_id=? AND available_at <= ? GROUP BY period) last
              ON o.period = last.period AND o.revision = last.rev
            WHERE o.series_id=? AND o.geo_id=? ORDER BY o.period_start""",
                                        (series_id, geo_id, self.t, series_id, geo_id)).fetchall()
        for r in rows:
            if r["available_at"] > self.t:  # defensive: the SQL must never let this happen
                raise LeakageError(f"{series_id} {r['period']} available {r['available_at']} > {self.t}")
            self._note(r["available_at"])
        return [(r["period"], r["value"]) for r in rows]

    def events(self, geo_id: str | None = None) -> list[dict]:
        q = "SELECT record_json, available_at FROM events WHERE available_at <= ?"
        args: list = [self.t]
        if geo_id:
            q += " AND geo_id = ?"
            args.append(geo_id)
        rows = self._store.conn.execute(q + " ORDER BY COALESCE(effective_date, announced_date)", args).fetchall()
        for r in rows:
            self._note(r["available_at"])
        return [json.loads(r["record_json"]) for r in rows]

    def shares(self, series_ids: list[str], geo_id: str) -> dict[str, dict[str, float]]:
        """Per-period shares of ``series_ids`` in their total.

        Refuses series from different universes (e.g. new vs second-hand
        imported vehicles) or with missing values in a period (that period is
        omitted, never silently treated as zero).
        """
        records = [self._store.series_record(s) for s in series_ids]
        universes = {r.universe_key for r in records}
        if len(universes) != 1:
            raise ConfigError(f"cannot compute shares across universes {sorted(universes)}")
        data = {s: dict(self.series(s, geo_id)) for s in series_ids}
        periods = sorted(set.intersection(*(set(d) for d in data.values())))
        out = {}
        for p in periods:
            vals = [data[s][p] for s in series_ids]
            if any(v is None for v in vals):
                continue
            total = sum(vals)
            if total > 0:
                out[p] = {s: v / total for s, v in zip(series_ids, vals)}
        return out
