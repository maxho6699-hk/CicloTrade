-- Generic completed Compute Gate equity evidence transport and website quarantine ledger.
-- These tables are isolated from recommendations, orders, notifications, and official state.
CREATE TABLE IF NOT EXISTS compute_evidence_spool_workers (
    publisher_id TEXT PRIMARY KEY,
    highest_epoch INTEGER NOT NULL DEFAULT 0 CHECK(highest_epoch >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compute_evidence_spool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL UNIQUE CHECK(length(package_id) BETWEEN 8 AND 128),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    package_sha256 TEXT NOT NULL CHECK(length(package_sha256)=64 AND package_sha256 NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL CHECK(state IN ('pending','claimed','sending','failed','delivered','dead','uncertain')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    publisher_id TEXT,
    fencing_epoch INTEGER CHECK(fencing_epoch IS NULL OR fencing_epoch >= 1),
    lease_token_sha256 TEXT CHECK(lease_token_sha256 IS NULL OR (length(lease_token_sha256)=64 AND lease_token_sha256 NOT GLOB '*[^0-9a-f]*')),
    lease_expires_at TEXT,
    retry_at TEXT NOT NULL,
    last_error TEXT,
    last_http_status INTEGER CHECK(last_http_status IS NULL OR last_http_status BETWEEN 100 AND 599),
    delivery_receipt_json TEXT CHECK(delivery_receipt_json IS NULL OR json_valid(delivery_receipt_json)),
    delivery_receipt_sha256 TEXT CHECK(delivery_receipt_sha256 IS NULL OR (length(delivery_receipt_sha256)=64 AND delivery_receipt_sha256 NOT GLOB '*[^0-9a-f]*')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT,
    terminal_at TEXT,
    uncertain_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_compute_evidence_spool_claim
    ON compute_evidence_spool(state,retry_at,lease_expires_at,id);

CREATE TABLE IF NOT EXISTS compute_evidence_receiver_fences (
    site_id TEXT NOT NULL,
    publisher_id TEXT NOT NULL,
    highest_epoch INTEGER NOT NULL CHECK(highest_epoch >= 1),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(site_id,publisher_id)
);

CREATE TABLE IF NOT EXISTS compute_evidence_receiver_nonces (
    nonce TEXT PRIMARY KEY CHECK(length(nonce) BETWEEN 32 AND 128),
    receipt_key TEXT NOT NULL,
    package_sha256 TEXT NOT NULL CHECK(length(package_sha256)=64 AND package_sha256 NOT GLOB '*[^0-9a-f]*'),
    expires_at TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compute_evidence_receipts (
    receipt_key TEXT PRIMARY KEY CHECK(length(receipt_key) BETWEEN 8 AND 128),
    package_id TEXT NOT NULL UNIQUE CHECK(length(package_id) BETWEEN 8 AND 128),
    site_id TEXT NOT NULL,
    publisher_id TEXT NOT NULL,
    source_worker_id TEXT NOT NULL,
    delivery_fencing_epoch INTEGER NOT NULL CHECK(delivery_fencing_epoch >= 1),
    compute_attempt_no INTEGER NOT NULL CHECK(compute_attempt_no >= 1),
    compute_fencing_epoch INTEGER NOT NULL CHECK(compute_fencing_epoch >= 1),
    manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*'),
    package_sha256 TEXT NOT NULL CHECK(length(package_sha256)=64 AND package_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    publication_state TEXT NOT NULL CHECK(publication_state IN ('quarantine','shadow')),
    received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_compute_evidence_receipts_read
    ON compute_evidence_receipts(publication_state,received_at DESC,receipt_key DESC);

CREATE TRIGGER IF NOT EXISTS trg_compute_evidence_receipts_no_update
BEFORE UPDATE ON compute_evidence_receipts
BEGIN SELECT RAISE(ABORT, 'compute evidence receipts are append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_compute_evidence_receipts_no_delete
BEFORE DELETE ON compute_evidence_receipts
BEGIN SELECT RAISE(ABORT, 'compute evidence receipts are append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_compute_evidence_nonces_no_update
BEFORE UPDATE ON compute_evidence_receiver_nonces
BEGIN SELECT RAISE(ABORT, 'compute evidence nonces are append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_compute_evidence_nonces_no_delete
BEFORE DELETE ON compute_evidence_receiver_nonces
BEGIN SELECT RAISE(ABORT, 'compute evidence nonces are append-only'); END;
