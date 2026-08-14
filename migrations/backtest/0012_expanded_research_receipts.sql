-- Isolated 97-symbol research receipts in the backtest/research database.
-- This ledger is not connected to legacy product DB, orders, Telegram, broker,
-- official paper, or live state.
CREATE TABLE expanded_research_receipts (
    receipt_key TEXT PRIMARY KEY,
    result_id TEXT NOT NULL UNIQUE,
    worker_id TEXT NOT NULL,
    fencing_epoch INTEGER NOT NULL CHECK (fencing_epoch >= 1),
    universe_version TEXT NOT NULL,
    universe_sha256 TEXT NOT NULL CHECK (length(universe_sha256) = 64),
    symbol TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('A','C')),
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    received_at TEXT NOT NULL
);
CREATE INDEX idx_expanded_research_received
ON expanded_research_receipts(received_at DESC, receipt_key DESC);
CREATE INDEX idx_expanded_research_symbol
ON expanded_research_receipts(symbol, tier, received_at DESC);

CREATE TABLE expanded_research_worker_fences (
    worker_id TEXT PRIMARY KEY,
    highest_epoch INTEGER NOT NULL CHECK (highest_epoch >= 1),
    updated_at TEXT NOT NULL
);

CREATE TRIGGER trg_expanded_research_receipts_no_update
BEFORE UPDATE ON expanded_research_receipts
BEGIN SELECT RAISE(ABORT, 'expanded research receipts are append-only'); END;
CREATE TRIGGER trg_expanded_research_receipts_no_delete
BEFORE DELETE ON expanded_research_receipts
BEGIN SELECT RAISE(ABORT, 'expanded research receipts are append-only'); END;
CREATE TRIGGER trg_expanded_research_fences_no_delete
BEFORE DELETE ON expanded_research_worker_fences
BEGIN SELECT RAISE(ABORT, 'expanded research fences are append-only'); END;
