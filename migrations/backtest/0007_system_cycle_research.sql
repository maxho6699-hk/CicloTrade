-- Dedicated shadow research spool and website receiver ledger.
-- These tables have no foreign keys or triggers into product/notification ledgers.
CREATE TABLE IF NOT EXISTS system_cycle_research_spool_workers (
    worker_id TEXT PRIMARY KEY,
    highest_epoch INTEGER NOT NULL DEFAULT 0 CHECK(highest_epoch >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_cycle_research_spool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL CHECK(state IN ('pending','claimed','failed','delivered')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    worker_id TEXT,
    fencing_epoch INTEGER CHECK(fencing_epoch IS NULL OR fencing_epoch >= 1),
    lease_token_sha256 TEXT CHECK(lease_token_sha256 IS NULL OR (length(lease_token_sha256)=64 AND lease_token_sha256 NOT GLOB '*[^0-9a-f]*')),
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    retry_at TEXT NOT NULL,
    last_error TEXT,
    delivery_receipt_json TEXT CHECK(delivery_receipt_json IS NULL OR json_valid(delivery_receipt_json)),
    delivery_receipt_sha256 TEXT CHECK(delivery_receipt_sha256 IS NULL OR (length(delivery_receipt_sha256)=64 AND delivery_receipt_sha256 NOT GLOB '*[^0-9a-f]*')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_system_cycle_research_spool_claim
    ON system_cycle_research_spool(state,retry_at,lease_expires_at,id);

CREATE TABLE IF NOT EXISTS system_cycle_research_worker_fences (
    worker_id TEXT PRIMARY KEY,
    highest_epoch INTEGER NOT NULL CHECK(highest_epoch >= 1),
    last_heartbeat_at TEXT,
    last_result_sha256 TEXT CHECK(last_result_sha256 IS NULL OR (length(last_result_sha256)=64 AND last_result_sha256 NOT GLOB '*[^0-9a-f]*')),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_cycle_research_receipts (
    receipt_key TEXT PRIMARY KEY CHECK(length(receipt_key) BETWEEN 8 AND 128),
    worker_id TEXT NOT NULL,
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 1),
    result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    cycle_id TEXT NOT NULL,
    universe_sha256 TEXT NOT NULL CHECK(length(universe_sha256)=64 AND universe_sha256 NOT GLOB '*[^0-9a-f]*'),
    received_at TEXT NOT NULL,
    UNIQUE(worker_id,cycle_id,result_sha256)
);

CREATE TABLE IF NOT EXISTS system_cycle_research_heartbeats (
    heartbeat_key TEXT PRIMARY KEY CHECK(length(heartbeat_key) BETWEEN 8 AND 128),
    worker_id TEXT NOT NULL,
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 1),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    heartbeat_at TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_system_cycle_research_receipts_no_update
BEFORE UPDATE ON system_cycle_research_receipts
BEGIN SELECT RAISE(ABORT, 'system cycle research receipts are append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_system_cycle_research_receipts_no_delete
BEFORE DELETE ON system_cycle_research_receipts
BEGIN SELECT RAISE(ABORT, 'system cycle research receipts are append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_system_cycle_research_heartbeats_no_update
BEFORE UPDATE ON system_cycle_research_heartbeats
BEGIN SELECT RAISE(ABORT, 'system cycle research heartbeats are append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_system_cycle_research_heartbeats_no_delete
BEFORE DELETE ON system_cycle_research_heartbeats
BEGIN SELECT RAISE(ABORT, 'system cycle research heartbeats are append-only'); END;
