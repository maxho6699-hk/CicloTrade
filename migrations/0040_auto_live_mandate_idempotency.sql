-- Bind public mandate creation retries to one immutable owner-scoped request.

CREATE TABLE auto_live_mandate_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    mandate_public_id TEXT NOT NULL REFERENCES auto_live_mandates(public_id),
    created_at TEXT NOT NULL,
    UNIQUE(owner_id,idempotency_key),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE INDEX idx_auto_live_mandate_requests_owner
ON auto_live_mandate_requests(owner_id,created_at);

CREATE TRIGGER trg_auto_live_mandate_request_owner
BEFORE INSERT ON auto_live_mandate_requests
WHEN (SELECT user_id FROM auto_live_mandates WHERE public_id=NEW.mandate_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'auto-live mandate request owner mismatch'); END;

-- Idempotency records are the immutable replay contract.  Corrections must
-- create a new mandate/request pair; rewriting or replacing an existing row
-- would let a retry key point at different request content or ownership.
CREATE TRIGGER trg_auto_live_mandate_requests_no_update
BEFORE UPDATE ON auto_live_mandate_requests BEGIN
    SELECT RAISE(ABORT, 'auto-live mandate requests are append-only');
END;

CREATE TRIGGER trg_auto_live_mandate_requests_no_delete
BEFORE DELETE ON auto_live_mandate_requests BEGIN
    SELECT RAISE(ABORT, 'auto-live mandate requests are append-only');
END;

CREATE TRIGGER trg_auto_live_mandate_requests_no_replace
BEFORE INSERT ON auto_live_mandate_requests
WHEN EXISTS(
    SELECT 1 FROM auto_live_mandate_requests
    WHERE public_id=NEW.public_id
       OR (owner_id=NEW.owner_id AND idempotency_key=NEW.idempotency_key)
)
BEGIN
    SELECT RAISE(ABORT, 'auto-live mandate requests are append-only');
END;
