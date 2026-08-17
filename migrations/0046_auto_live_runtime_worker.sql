-- Auto-live runtime worker lease and append-only lease audit.
CREATE TABLE IF NOT EXISTS auto_live_runtime_leases (
    mandate_public_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 3 AND 128),
    lease_token_sha256 TEXT NOT NULL CHECK(length(lease_token_sha256)=64),
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
    lease_expires_at TEXT NOT NULL,
    heartbeat_at TEXT,
    observed_at TEXT NOT NULL,
    projection_sha256 TEXT NOT NULL CHECK(length(projection_sha256)=64),
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id)
);

CREATE TABLE IF NOT EXISTS auto_live_runtime_lease_events (
    event_id TEXT PRIMARY KEY CHECK(length(event_id) BETWEEN 16 AND 128),
    mandate_public_id TEXT NOT NULL,
    worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 3 AND 128),
    event_type TEXT NOT NULL CHECK(event_type IN ('claimed','reclaimed','running_ack','heartbeat')),
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
    lease_token_sha256 TEXT NOT NULL CHECK(length(lease_token_sha256)=64),
    lease_expires_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id)
);

CREATE TRIGGER IF NOT EXISTS trg_auto_live_runtime_lease_events_no_update
BEFORE UPDATE ON auto_live_runtime_lease_events BEGIN
    SELECT RAISE(ABORT, 'auto live runtime lease events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_runtime_lease_events_no_delete
BEFORE DELETE ON auto_live_runtime_lease_events BEGIN
    SELECT RAISE(ABORT, 'auto live runtime lease events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_runtime_lease_events_no_replace
BEFORE INSERT ON auto_live_runtime_lease_events
WHEN EXISTS(SELECT 1 FROM auto_live_runtime_lease_events WHERE event_id=NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'auto live runtime lease events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_runtime_leases_mandate_immutable
BEFORE UPDATE ON auto_live_runtime_leases
WHEN OLD.mandate_public_id <> NEW.mandate_public_id
BEGIN
    SELECT RAISE(ABORT, 'auto live runtime lease mandate is immutable');
END;
