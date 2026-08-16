-- Public, owner-scoped deliberation and workflow facts.  These tables are
-- intentionally independent from the research-only backtest queue.
CREATE TABLE IF NOT EXISTS workflow_tasks (
    task_public_id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    source_kind TEXT NOT NULL CHECK(length(source_kind) BETWEEN 2 AND 64),
    source_public_id TEXT NOT NULL CHECK(length(source_public_id) BETWEEN 1 AND 160),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','cancelled','blocked','timed_out')),
    context_json TEXT NOT NULL CHECK(json_valid(context_json)),
    context_sha256 TEXT NOT NULL CHECK(length(context_sha256)=64 AND context_sha256 NOT GLOB '*[^0-9a-f]*'),
    provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
    provenance_sha256 TEXT NOT NULL CHECK(length(provenance_sha256)=64 AND provenance_sha256 NOT GLOB '*[^0-9a-f]*'),
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    result_sha256 TEXT CHECK(result_sha256 IS NULL OR (length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*')),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS workflow_public_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_public_id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK(seq >= 1),
    event_type TEXT NOT NULL CHECK(event_type IN ('created','status','result','cancel_requested','retry')),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','cancelled','blocked','timed_out')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    created_at TEXT NOT NULL,
    UNIQUE(task_public_id, seq),
    FOREIGN KEY(task_public_id) REFERENCES workflow_tasks(task_public_id)
);

CREATE TABLE IF NOT EXISTS deliberation_jobs (
    deliberation_public_id TEXT PRIMARY KEY,
    task_public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL,
    market TEXT NOT NULL CHECK(length(market) BETWEEN 1 AND 32),
    symbol TEXT NOT NULL CHECK(length(symbol) BETWEEN 1 AND 32),
    timeframe TEXT NOT NULL CHECK(length(timeframe) BETWEEN 1 AND 32),
    question TEXT NOT NULL CHECK(length(question) BETWEEN 1 AND 4000),
    source_event_id TEXT NOT NULL CHECK(length(source_event_id) BETWEEN 1 AND 160),
    source_event_version INTEGER NOT NULL CHECK(source_event_version >= 1),
    source_event_sha256 TEXT NOT NULL CHECK(length(source_event_sha256)=64 AND source_event_sha256 NOT GLOB '*[^0-9a-f]*'),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','cancelled','blocked','timed_out')),
    evidence_snapshot_json TEXT,
    evidence_snapshot_sha256 TEXT CHECK(evidence_snapshot_sha256 IS NULL OR (length(evidence_snapshot_sha256)=64 AND evidence_snapshot_sha256 NOT GLOB '*[^0-9a-f]*')),
    method_version TEXT NOT NULL,
    evidence_version TEXT,
    research_version TEXT,
    support_strength REAL CHECK(support_strength IS NULL OR (support_strength >= 0 AND support_strength <= 100)),
    counter_evidence_strength REAL CHECK(counter_evidence_strength IS NULL OR (counter_evidence_strength >= 0 AND counter_evidence_strength <= 100)),
    coverage REAL CHECK(coverage IS NULL OR (coverage >= 0 AND coverage <= 1)),
    missing_json TEXT NOT NULL CHECK(json_valid(missing_json)),
    seats_json TEXT NOT NULL CHECK(json_valid(seats_json)),
    invalidated_reason TEXT,
    observed_at TEXT,
    available_at TEXT,
    as_of TEXT,
    calculated_at TEXT,
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task_public_id) REFERENCES workflow_tasks(task_public_id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_tasks_owner ON workflow_tasks(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_tasks_source ON workflow_tasks(owner_id, source_kind, source_public_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_events_task ON workflow_public_events(task_public_id, seq);
CREATE INDEX IF NOT EXISTS idx_deliberation_owner ON deliberation_jobs(owner_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_workflow_events_no_update
BEFORE UPDATE ON workflow_public_events BEGIN SELECT RAISE(ABORT, 'workflow_public_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_workflow_events_no_delete
BEFORE DELETE ON workflow_public_events BEGIN SELECT RAISE(ABORT, 'workflow_public_events are append-only'); END;
