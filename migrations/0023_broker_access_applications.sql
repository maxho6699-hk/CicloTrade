CREATE TABLE IF NOT EXISTS broker_access_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL CHECK(provider IN ('futu_moomoo','tiger','ibkr','webull','longbridge')),
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK(status IN ('submitted','approved','rejected','withdrawn','revoked','expired')),
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    request_reason TEXT,
    decision_reason TEXT,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    withdrawn_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (reviewed_by) REFERENCES users(id),
    UNIQUE(user_id,idempotency_key),
    CHECK(length(public_id) BETWEEN 16 AND 64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    CHECK(length(request_fingerprint)=64),
    CHECK(request_reason IS NULL OR length(request_reason) BETWEEN 1 AND 500),
    CHECK(decision_reason IS NULL OR length(decision_reason) BETWEEN 1 AND 500)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_broker_access_one_active
ON broker_access_applications(user_id,provider)
WHERE status IN ('submitted','approved');

CREATE INDEX IF NOT EXISTS idx_broker_access_user_created
ON broker_access_applications(user_id,created_at DESC,id DESC);

CREATE INDEX IF NOT EXISTS idx_broker_access_admin_queue
ON broker_access_applications(status,created_at,id);

CREATE TRIGGER IF NOT EXISTS trg_broker_access_identity_immutable
BEFORE UPDATE OF public_id,user_id,provider,idempotency_key,request_fingerprint,request_reason,created_at
ON broker_access_applications
BEGIN
    SELECT RAISE(ABORT, 'broker access application identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_broker_access_status_transition
BEFORE UPDATE OF status ON broker_access_applications
WHEN NOT (
    (OLD.status='submitted' AND NEW.status IN ('approved','rejected','withdrawn','expired')) OR
    (OLD.status='approved' AND NEW.status='revoked')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid broker access application status transition');
END;
