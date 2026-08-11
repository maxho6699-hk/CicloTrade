-- Canonical, research-only backtest worker queue.  It deliberately contains
-- no approval, activation, publication, or live-trading state.
CREATE TABLE IF NOT EXISTS backtest_jobs (
    id TEXT PRIMARY KEY,
    owner_id INTEGER,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('user','system')),
    job_type TEXT NOT NULL CHECK(job_type IN ('backtest.run.v1','backtest.optimize.v1','candidate.evaluate.v1','catalog.evaluate.v1','saved.refresh.v1')),
    status TEXT NOT NULL CHECK(status IN ('queued','preparing','running','completed','failed','cancelled','superseded')),
    idempotency_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
    manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    result_sha256 TEXT CHECK(result_sha256 IS NULL OR (length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
    worker_id TEXT,
    lease_token_sha256 TEXT CHECK(lease_token_sha256 IS NULL OR (length(lease_token_sha256)=64 AND lease_token_sha256 NOT GLOB '*[^0-9a-f]*')),
    lease_seconds INTEGER NOT NULL DEFAULT 60 CHECK(lease_seconds BETWEEN 10 AND 600),
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    fencing_epoch INTEGER NOT NULL DEFAULT 0 CHECK(fencing_epoch >= 0),
    progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 1),
    progress_stage TEXT NOT NULL DEFAULT 'queued' CHECK(progress_stage IN ('queued','loading','executing','finalizing')),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    available_at TEXT NOT NULL,
    deadline_at TEXT,
    priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN -100 AND 100),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(idempotency_scope,idempotency_key),
    CHECK((owner_scope='user' AND owner_id IS NOT NULL) OR (owner_scope='system' AND owner_id IS NULL))
);

CREATE TABLE IF NOT EXISTS backtest_job_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    worker_id TEXT,
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 1),
    lease_token_sha256 TEXT NOT NULL CHECK(length(lease_token_sha256)=64 AND lease_token_sha256 NOT GLOB '*[^0-9a-f]*'),
    status TEXT NOT NULL CHECK(status IN ('claimed','expired','completed','failed','cancelled')),
    claimed_at TEXT NOT NULL,
    finished_at TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id,attempt_no),
    FOREIGN KEY(job_id) REFERENCES backtest_jobs(id)
);

CREATE TABLE IF NOT EXISTS backtest_job_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 0 CHECK(attempt_no >= 0),
    direction TEXT NOT NULL CHECK(direction IN ('input','output')),
    artifact_key TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    bytes INTEGER NOT NULL CHECK(bytes >= 0),
    row_count INTEGER CHECK(row_count IS NULL OR row_count >= 0),
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream' CHECK(length(media_type) BETWEEN 1 AND 128),
    state TEXT NOT NULL DEFAULT 'verified' CHECK(state IN ('pending','verified','rejected')),
    storage_key TEXT NOT NULL,
    verified_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(job_id,attempt_no,direction,artifact_key),
    UNIQUE(storage_key),
    FOREIGN KEY(job_id) REFERENCES backtest_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_backtest_jobs_claim
    ON backtest_jobs(status, cancel_requested, available_at, deadline_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_backtest_jobs_owner ON backtest_jobs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_jobs_running ON backtest_jobs(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_backtest_attempts_job ON backtest_job_attempts(job_id, attempt_no DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_artifacts_ready
    ON backtest_job_artifacts(job_id, direction, state, artifact_key);
