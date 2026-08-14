-- Append-only tombstones for invalidated 97-symbol research results.
-- Strictly follows 0012_expanded_research_receipts.sql.
CREATE INDEX idx_expanded_research_latest_symbol
ON expanded_research_receipts(symbol, received_at DESC, receipt_key DESC);
CREATE INDEX idx_expanded_research_dataset_cycle
ON expanded_research_receipts(json_extract(payload_json, '$.dataset_end') DESC, received_at DESC, receipt_key DESC);

CREATE TABLE expanded_research_invalidations (
    invalidation_key TEXT PRIMARY KEY,
    invalidation_id TEXT NOT NULL UNIQUE,
    worker_id TEXT NOT NULL,
    fencing_epoch INTEGER NOT NULL CHECK (fencing_epoch >= 1),
    target_result_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    reason TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    universe_sha256 TEXT NOT NULL CHECK (length(universe_sha256) = 64),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    invalidated_at TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX idx_expanded_research_invalidations_target
ON expanded_research_invalidations(target_result_id, received_at DESC);

CREATE TRIGGER trg_expanded_research_invalidations_no_update
BEFORE UPDATE ON expanded_research_invalidations
BEGIN SELECT RAISE(ABORT, 'expanded research invalidations are append-only'); END;
CREATE TRIGGER trg_expanded_research_invalidations_no_delete
BEFORE DELETE ON expanded_research_invalidations
BEGIN SELECT RAISE(ABORT, 'expanded research invalidations are append-only'); END;
