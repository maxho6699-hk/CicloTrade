-- Local research Compute Gate metadata. No publication or product data lives here.
CREATE TABLE IF NOT EXISTS backtest_source_snapshots (
    snapshot_id TEXT PRIMARY KEY
        CHECK(length(snapshot_id)=64 AND snapshot_id NOT GLOB '*[^0-9a-f]*'),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
    source_kind TEXT NOT NULL CHECK(source_kind='controlled_local_csv'),
    source_name TEXT NOT NULL CHECK(length(source_name) BETWEEN 5 AND 128),
    source_sha256 TEXT NOT NULL
        CHECK(length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'),
    prices_sha256 TEXT NOT NULL
        CHECK(length(prices_sha256)=64 AND prices_sha256 NOT GLOB '*[^0-9a-f]*'),
    imported_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    dataset_end TEXT NOT NULL,
    symbol TEXT NOT NULL CHECK(length(symbol) BETWEEN 1 AND 16),
    canonical_rows INTEGER NOT NULL CHECK(canonical_rows > 0),
    canonical_bytes INTEGER NOT NULL CHECK(canonical_bytes > 0),
    created_at TEXT NOT NULL,
    UNIQUE(source_name,source_sha256,as_of)
);

CREATE TABLE IF NOT EXISTS backtest_operator_actions (
    request_id TEXT PRIMARY KEY CHECK(length(request_id) BETWEEN 8 AND 128),
    job_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action='cancel_system_job'),
    operator_subject TEXT NOT NULL CHECK(length(operator_subject) BETWEEN 1 AND 128),
    reason_code TEXT NOT NULL CHECK(length(reason_code) BETWEEN 1 AND 128),
    manifest_sha256 TEXT NOT NULL
        CHECK(length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    previous_status TEXT NOT NULL,
    resulting_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES backtest_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_backtest_snapshots_created
    ON backtest_source_snapshots(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_operator_actions_job
    ON backtest_operator_actions(job_id,created_at DESC);
