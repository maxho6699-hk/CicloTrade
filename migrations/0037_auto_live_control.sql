-- Server-side fail-closed auto-live control plane.
-- Identifiers in this bounded context are opaque public identifiers.  Broker
-- external account ids and credentials never belong in a public projection.

CREATE TABLE IF NOT EXISTS auto_live_broker_refs (
    public_id TEXT PRIMARY KEY CHECK(length(public_id) BETWEEN 16 AND 128),
    user_id INTEGER NOT NULL,
    broker_account_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id,broker_account_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (broker_account_id) REFERENCES broker_accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_auto_live_broker_refs_owner
    ON auto_live_broker_refs(user_id,created_at,public_id);

CREATE TABLE IF NOT EXISTS auto_live_strategy_risk_contracts (
    strategy_version TEXT NOT NULL CHECK(length(strategy_version) BETWEEN 1 AND 128),
    risk_version TEXT NOT NULL CHECK(length(risk_version) BETWEEN 1 AND 128),
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256)=64),
    approved_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    PRIMARY KEY(strategy_version,risk_version),
    CHECK(valid_until > approved_at)
);

CREATE TRIGGER IF NOT EXISTS trg_auto_live_strategy_risk_contract_snapshot_immutable
BEFORE UPDATE ON auto_live_strategy_risk_contracts
WHEN OLD.strategy_version <> NEW.strategy_version
  OR OLD.risk_version <> NEW.risk_version
  OR OLD.snapshot_json <> NEW.snapshot_json
  OR OLD.snapshot_sha256 <> NEW.snapshot_sha256
  OR OLD.approved_at <> NEW.approved_at
BEGIN
    SELECT RAISE(ABORT, 'auto live strategy risk contract snapshot is immutable');
END;

CREATE TABLE IF NOT EXISTS auto_live_mandates (
    public_id TEXT PRIMARY KEY CHECK(length(public_id) BETWEEN 16 AND 128),
    user_id INTEGER NOT NULL,
    broker_account_id INTEGER NOT NULL,
    strategy_version TEXT NOT NULL CHECK(length(strategy_version) BETWEEN 1 AND 128),
    risk_version TEXT NOT NULL CHECK(length(risk_version) BETWEEN 1 AND 128),
    capital_limit_minor INTEGER NOT NULL CHECK(capital_limit_minor > 0),
    frequency_limit INTEGER NOT NULL CHECK(frequency_limit > 0),
    valid_from TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'draft','pending_confirmation','active','paused','blocked','expired','revoked'
    )),
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256)=64),
    confirmation_digest TEXT,
    confirmed_at TEXT,
    fencing_epoch INTEGER NOT NULL DEFAULT 0 CHECK(fencing_epoch >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (broker_account_id) REFERENCES broker_accounts(id),
    CHECK(valid_until > valid_from),
    CHECK((state='active') = (confirmed_at IS NOT NULL AND confirmation_digest IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_auto_live_mandates_user_state
    ON auto_live_mandates(user_id,state,updated_at);
CREATE INDEX IF NOT EXISTS idx_auto_live_mandates_broker_state
    ON auto_live_mandates(broker_account_id,state,updated_at);

CREATE TRIGGER IF NOT EXISTS trg_auto_live_mandates_snapshot_immutable
BEFORE UPDATE ON auto_live_mandates
WHEN OLD.snapshot_json <> NEW.snapshot_json
  OR OLD.snapshot_sha256 <> NEW.snapshot_sha256
  OR OLD.broker_account_id <> NEW.broker_account_id
  OR OLD.strategy_version <> NEW.strategy_version
  OR OLD.risk_version <> NEW.risk_version
  OR OLD.capital_limit_minor <> NEW.capital_limit_minor
  OR OLD.frequency_limit <> NEW.frequency_limit
  OR OLD.valid_from <> NEW.valid_from
  OR OLD.valid_until <> NEW.valid_until
BEGIN
    SELECT RAISE(ABORT, 'auto live mandate snapshot is immutable');
END;

CREATE TABLE IF NOT EXISTS auto_live_mandate_events (
    event_id TEXT PRIMARY KEY CHECK(length(event_id) BETWEEN 16 AND 128),
    mandate_public_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK(length(event_type) BETWEEN 1 AND 64),
    from_state TEXT,
    to_state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_auto_live_mandate_events_mandate
    ON auto_live_mandate_events(mandate_public_id,created_at,event_id);

CREATE TABLE IF NOT EXISTS auto_live_pause_requests (
    public_id TEXT PRIMARY KEY CHECK(length(public_id) BETWEEN 16 AND 128),
    user_id INTEGER NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('aggregate','broker','mandate')),
    broker_account_id INTEGER,
    mandate_public_id TEXT,
    idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
    status TEXT NOT NULL CHECK(status IN ('pausing','paused','partial','failed')),
    confirmed INTEGER NOT NULL CHECK(confirmed >= 0),
    total INTEGER NOT NULL CHECK(total >= 0),
    unconfirmed_json TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256)=64),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (broker_account_id) REFERENCES broker_accounts(id),
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id),
    UNIQUE(user_id,idempotency_key),
    CHECK(confirmed <= total),
    CHECK(
        (scope='aggregate' AND broker_account_id IS NULL AND mandate_public_id IS NULL)
        OR (scope='broker' AND broker_account_id IS NOT NULL AND mandate_public_id IS NULL)
        OR (scope='mandate' AND broker_account_id IS NULL AND mandate_public_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS auto_live_pause_request_targets (
    request_public_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('broker','mandate','aggregate')),
    target_public_id TEXT NOT NULL,
    confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('paused','failed','pending')),
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
    detail TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(request_public_id,target_type,target_public_id),
    FOREIGN KEY (request_public_id) REFERENCES auto_live_pause_requests(public_id),
    CHECK(
        (target_type='aggregate' AND target_public_id='aggregate')
        OR (target_type IN ('broker','mandate') AND length(target_public_id) BETWEEN 16 AND 128)
    )
);

CREATE TABLE IF NOT EXISTS auto_live_start_requests (
    public_id TEXT PRIMARY KEY CHECK(length(public_id) BETWEEN 16 AND 128),
    user_id INTEGER NOT NULL,
    mandate_public_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
    expected_fencing_epoch INTEGER NOT NULL CHECK(expected_fencing_epoch >= 0),
    status TEXT NOT NULL CHECK(status IN ('starting','blocked')),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id),
    UNIQUE(user_id,idempotency_key)
);

CREATE TABLE IF NOT EXISTS auto_live_runtime_projections (
    mandate_public_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('stopped','starting','running','pausing','paused','blocked','unknown')),
    can_reduce_exposure INTEGER NOT NULL CHECK(can_reduce_exposure IN (0,1)),
    fencing_epoch INTEGER NOT NULL DEFAULT 0 CHECK(fencing_epoch >= 0),
    last_error_code TEXT,
    observed_at TEXT NOT NULL,
    projection_sha256 TEXT NOT NULL CHECK(length(projection_sha256)=64),
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id)
);

CREATE TABLE IF NOT EXISTS auto_live_heartbeat_projections (
    mandate_public_id TEXT PRIMARY KEY,
    heartbeat_state TEXT NOT NULL CHECK(heartbeat_state IN ('fresh','stale','missing','unknown')),
    heartbeat_at TEXT,
    fencing_epoch INTEGER NOT NULL DEFAULT 0 CHECK(fencing_epoch >= 0),
    observed_at TEXT NOT NULL,
    projection_sha256 TEXT NOT NULL CHECK(length(projection_sha256)=64),
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id)
);

CREATE TABLE IF NOT EXISTS auto_live_order_receipt_projections (
    public_id TEXT PRIMARY KEY CHECK(length(public_id) BETWEEN 16 AND 128),
    mandate_public_id TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    submission_state TEXT NOT NULL CHECK(submission_state IN ('accepted','rejected','submission_unknown','cancelled')),
    broker_order_id TEXT,
    observed_at TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256)=64),
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id)
);

CREATE TABLE IF NOT EXISTS auto_live_pause_receipts (
    receipt_id TEXT PRIMARY KEY CHECK(length(receipt_id) BETWEEN 16 AND 128),
    request_public_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('paused','partial','failed')),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (request_public_id) REFERENCES auto_live_pause_requests(public_id)
);

CREATE TABLE IF NOT EXISTS auto_live_runtime_receipts (
    receipt_id TEXT PRIMARY KEY CHECK(length(receipt_id) BETWEEN 16 AND 128),
    mandate_public_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('initialized','start_requested','running_ack','pause_requested','terminalized')),
    state TEXT NOT NULL CHECK(state IN ('stopped','starting','running','pausing','paused','blocked','unknown')),
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id)
);

-- The mandate and event ledgers are append-only.  Corrections are represented
-- by a new event, never by rewriting history.
CREATE TRIGGER IF NOT EXISTS trg_auto_live_mandate_events_no_update
BEFORE UPDATE ON auto_live_mandate_events BEGIN
    SELECT RAISE(ABORT, 'auto live mandate events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_mandate_events_no_delete
BEFORE DELETE ON auto_live_mandate_events BEGIN
    SELECT RAISE(ABORT, 'auto live mandate events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_mandate_events_no_replace
BEFORE INSERT ON auto_live_mandate_events
WHEN EXISTS(SELECT 1 FROM auto_live_mandate_events WHERE event_id=NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'auto live mandate events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_targets_no_update
BEFORE UPDATE ON auto_live_pause_request_targets BEGIN
    SELECT RAISE(ABORT, 'auto live pause request targets are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_targets_no_delete
BEFORE DELETE ON auto_live_pause_request_targets BEGIN
    SELECT RAISE(ABORT, 'auto live pause request targets are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_targets_no_replace
BEFORE INSERT ON auto_live_pause_request_targets
WHEN EXISTS(
    SELECT 1 FROM auto_live_pause_request_targets
    WHERE request_public_id=NEW.request_public_id
      AND target_type=NEW.target_type
      AND target_public_id=NEW.target_public_id
)
BEGIN
    SELECT RAISE(ABORT, 'auto live pause request targets are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_receipts_no_update
BEFORE UPDATE ON auto_live_pause_receipts BEGIN
    SELECT RAISE(ABORT, 'auto live pause receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_receipts_no_delete
BEFORE DELETE ON auto_live_pause_receipts BEGIN
    SELECT RAISE(ABORT, 'auto live pause receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_receipts_no_replace
BEFORE INSERT ON auto_live_pause_receipts
WHEN EXISTS(
    SELECT 1 FROM auto_live_pause_receipts
    WHERE receipt_id=NEW.receipt_id OR request_public_id=NEW.request_public_id
)
BEGIN
    SELECT RAISE(ABORT, 'auto live pause receipts are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_start_requests_no_update
BEFORE UPDATE ON auto_live_start_requests BEGIN
    SELECT RAISE(ABORT, 'auto live start requests are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_start_requests_no_delete
BEFORE DELETE ON auto_live_start_requests BEGIN
    SELECT RAISE(ABORT, 'auto live start requests are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_start_requests_no_replace
BEFORE INSERT ON auto_live_start_requests
WHEN EXISTS(
    SELECT 1 FROM auto_live_start_requests
    WHERE public_id=NEW.public_id
       OR (user_id=NEW.user_id AND idempotency_key=NEW.idempotency_key)
)
BEGIN
    SELECT RAISE(ABORT, 'auto live start requests are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_runtime_receipts_no_update
BEFORE UPDATE ON auto_live_runtime_receipts BEGIN
    SELECT RAISE(ABORT, 'auto live runtime receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_runtime_receipts_no_delete
BEFORE DELETE ON auto_live_runtime_receipts BEGIN
    SELECT RAISE(ABORT, 'auto live runtime receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_runtime_receipts_no_replace
BEFORE INSERT ON auto_live_runtime_receipts
WHEN EXISTS(SELECT 1 FROM auto_live_runtime_receipts WHERE receipt_id=NEW.receipt_id)
BEGIN
    SELECT RAISE(ABORT, 'auto live runtime receipts are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_receipts_no_update
BEFORE UPDATE ON auto_live_order_receipt_projections BEGIN
    SELECT RAISE(ABORT, 'auto live order receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_receipts_no_delete
BEFORE DELETE ON auto_live_order_receipt_projections BEGIN
    SELECT RAISE(ABORT, 'auto live order receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_receipts_no_replace
BEFORE INSERT ON auto_live_order_receipt_projections
WHEN EXISTS(SELECT 1 FROM auto_live_order_receipt_projections WHERE public_id=NEW.public_id)
BEGIN
    SELECT RAISE(ABORT, 'auto live order receipts are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_mandates_owner_consistency
BEFORE INSERT ON auto_live_mandates
WHEN (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) IS NULL
  OR (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) <> NEW.user_id
BEGIN
    SELECT RAISE(ABORT, 'auto live mandate owner mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_mandates_owner_consistency_update
BEFORE UPDATE ON auto_live_mandates
WHEN (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) IS NULL
  OR (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) <> NEW.user_id
BEGIN
    SELECT RAISE(ABORT, 'auto live mandate owner mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_broker_refs_owner_consistency
BEFORE INSERT ON auto_live_broker_refs
WHEN (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) IS NULL
  OR (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) <> NEW.user_id
BEGIN
    SELECT RAISE(ABORT, 'auto live broker ref owner mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_broker_refs_owner_consistency_update
BEFORE UPDATE ON auto_live_broker_refs
WHEN (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) IS NULL
  OR (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) <> NEW.user_id
BEGIN
    SELECT RAISE(ABORT, 'auto live broker ref owner mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_requests_owner_consistency
BEFORE INSERT ON auto_live_pause_requests
WHEN (NEW.broker_account_id IS NOT NULL AND
      ((SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) IS NULL
       OR (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) <> NEW.user_id))
  OR (NEW.mandate_public_id IS NOT NULL AND
      ((SELECT user_id FROM auto_live_mandates WHERE public_id=NEW.mandate_public_id) IS NULL
       OR (SELECT user_id FROM auto_live_mandates WHERE public_id=NEW.mandate_public_id) <> NEW.user_id))
BEGIN
    SELECT RAISE(ABORT, 'auto live pause request owner mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_requests_owner_consistency_update
BEFORE UPDATE ON auto_live_pause_requests
WHEN (NEW.broker_account_id IS NOT NULL AND
      ((SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) IS NULL
       OR (SELECT user_id FROM broker_accounts WHERE id=NEW.broker_account_id) <> NEW.user_id))
  OR (NEW.mandate_public_id IS NOT NULL AND
      ((SELECT user_id FROM auto_live_mandates WHERE public_id=NEW.mandate_public_id) IS NULL
       OR (SELECT user_id FROM auto_live_mandates WHERE public_id=NEW.mandate_public_id) <> NEW.user_id))
BEGIN
    SELECT RAISE(ABORT, 'auto live pause request owner mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_requests_identity_immutable
BEFORE UPDATE ON auto_live_pause_requests
WHEN OLD.public_id <> NEW.public_id
  OR OLD.user_id <> NEW.user_id
  OR OLD.scope <> NEW.scope
  OR OLD.broker_account_id IS NOT NEW.broker_account_id
  OR OLD.mandate_public_id IS NOT NEW.mandate_public_id
  OR OLD.idempotency_key <> NEW.idempotency_key
  OR OLD.request_fingerprint <> NEW.request_fingerprint
  OR OLD.created_at <> NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'auto live pause request identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_start_requests_owner_consistency
BEFORE INSERT ON auto_live_start_requests
WHEN (SELECT user_id FROM auto_live_mandates WHERE public_id=NEW.mandate_public_id) IS NULL
  OR (SELECT user_id FROM auto_live_mandates WHERE public_id=NEW.mandate_public_id) <> NEW.user_id
BEGIN
    SELECT RAISE(ABORT, 'auto live start request owner mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_pause_targets_owner_consistency
BEFORE INSERT ON auto_live_pause_request_targets
WHEN (SELECT q.user_id FROM auto_live_pause_requests q WHERE q.public_id=NEW.request_public_id) IS NULL
  OR (NEW.target_type='aggregate' AND
      (NEW.target_public_id <> 'aggregate'
       OR (SELECT q.scope FROM auto_live_pause_requests q WHERE q.public_id=NEW.request_public_id) <> 'aggregate'))
  OR (NEW.target_type='broker' AND
      (SELECT q.scope FROM auto_live_pause_requests q WHERE q.public_id=NEW.request_public_id) <> 'broker')
  OR (NEW.target_type='mandate' AND
      ((SELECT m.user_id FROM auto_live_mandates m WHERE m.public_id=NEW.target_public_id) IS NULL
       OR (SELECT m.user_id FROM auto_live_mandates m WHERE m.public_id=NEW.target_public_id) <>
       (SELECT q.user_id FROM auto_live_pause_requests q WHERE q.public_id=NEW.request_public_id)))
  OR (NEW.target_type='broker' AND
      ((SELECT r.user_id FROM auto_live_broker_refs r WHERE r.public_id=NEW.target_public_id) IS NULL
       OR (SELECT r.user_id FROM auto_live_broker_refs r WHERE r.public_id=NEW.target_public_id) <>
       (SELECT q.user_id FROM auto_live_pause_requests q WHERE q.public_id=NEW.request_public_id)))
BEGIN
    SELECT RAISE(ABORT, 'auto live pause target owner mismatch');
END;
