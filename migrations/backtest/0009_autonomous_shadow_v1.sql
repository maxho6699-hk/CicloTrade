-- Autonomous US-equity research records are append-only and shadow-only.
CREATE TABLE IF NOT EXISTS backtest_us_equity_universe_snapshots (
    snapshot_sha256 TEXT PRIMARY KEY CHECK(length(snapshot_sha256)=64 AND snapshot_sha256 NOT GLOB '*[^0-9a-f]*'),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    as_of_date TEXT NOT NULL,
    members_json TEXT NOT NULL CHECK(json_valid(members_json))
        CHECK(json_extract(members_json,'$.schema_version')=schema_version)
        CHECK(json_extract(members_json,'$.as_of')=as_of_date),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_autonomous_candidates (
    record_sha256 TEXT PRIMARY KEY CHECK(length(record_sha256)=64 AND record_sha256 NOT GLOB '*[^0-9a-f]*'),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    candidate_id TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
        CHECK(json_extract(record_json,'$.schema_version')=schema_version)
        CHECK(json_extract(record_json,'$.candidate_id')=candidate_id)
        CHECK(json_extract(record_json,'$.candidate_version')=candidate_version)
        CHECK(json_extract(record_json,'$.status')=status),
    status TEXT NOT NULL CHECK(status IN ('rejected','quarantine','shadow')),
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id,candidate_version)
);

CREATE INDEX IF NOT EXISTS idx_backtest_autonomous_candidates_shadow
    ON backtest_autonomous_candidates(candidate_id,status,created_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_backtest_autonomous_candidates_no_update
BEFORE UPDATE ON backtest_autonomous_candidates
BEGIN SELECT RAISE(ABORT, 'autonomous candidate records are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_backtest_autonomous_candidates_no_delete
BEFORE DELETE ON backtest_autonomous_candidates
BEGIN SELECT RAISE(ABORT, 'autonomous candidate records are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_backtest_us_equity_universe_no_update
BEFORE UPDATE ON backtest_us_equity_universe_snapshots
BEGIN SELECT RAISE(ABORT, 'universe snapshots are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_backtest_us_equity_universe_no_delete
BEFORE DELETE ON backtest_us_equity_universe_snapshots
BEGIN SELECT RAISE(ABORT, 'universe snapshots are immutable'); END;
