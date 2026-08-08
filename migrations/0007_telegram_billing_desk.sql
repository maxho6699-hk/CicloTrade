ALTER TABLE subscription_orders ADD COLUMN source TEXT NOT NULL DEFAULT 'web';
ALTER TABLE subscription_orders ADD COLUMN idempotency_key TEXT;
ALTER TABLE subscription_orders ADD COLUMN request_fingerprint TEXT;
ALTER TABLE subscription_orders ADD COLUMN amount_minor INTEGER;
ALTER TABLE subscription_orders ADD COLUMN expires_at TEXT;

UPDATE subscription_orders
SET amount_minor=CAST(ROUND(amount * 100) AS INTEGER)
WHERE amount_minor IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_orders_user_idempotency
    ON subscription_orders(user_id,idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_subscription_orders_telegram_pending
    ON subscription_orders(user_id,source,status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_subscription_orders_pending_expiry
    ON subscription_orders(status,expires_at);

CREATE TABLE IF NOT EXISTS telegram_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    chat_id TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

WITH eligible AS (
    SELECT user_id,
           CAST(json_extract(settings_json,'$.telegram.chat_id') AS TEXT) chat_id,
           updated_at
    FROM user_settings
    WHERE json_extract(settings_json,'$.telegram.verified')=1
      AND json_extract(settings_json,'$.telegram.consent')=1
      AND CAST(json_extract(settings_json,'$.telegram.chat_id') AS TEXT) NOT GLOB '*[^0-9]*'
      AND length(CAST(json_extract(settings_json,'$.telegram.chat_id') AS TEXT)) BETWEEN 1 AND 20
), unique_chats AS (
    SELECT chat_id FROM eligible GROUP BY chat_id HAVING COUNT(*)=1
)
INSERT INTO telegram_accounts
    (user_id,chat_id,is_active,revoked_at,created_at,updated_at)
SELECT e.user_id,e.chat_id,1,NULL,e.updated_at,e.updated_at
FROM eligible e JOIN unique_chats u ON u.chat_id=e.chat_id;

CREATE TABLE IF NOT EXISTS manual_payment_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK(status IN ('submitted','approved','rejected')),
    evidence_file_id TEXT,
    evidence_file_unique_id TEXT,
    evidence_message_id TEXT,
    source_update_id TEXT,
    settlement_reference TEXT,
    rejection_reason TEXT,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_no) REFERENCES subscription_orders(order_no),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (reviewed_by) REFERENCES users(id),
    CHECK(evidence_file_id IS NULL OR length(evidence_file_id) BETWEEN 1 AND 256),
    CHECK(evidence_file_unique_id IS NULL OR length(evidence_file_unique_id) BETWEEN 1 AND 256),
    CHECK(evidence_message_id IS NULL OR length(evidence_message_id) BETWEEN 1 AND 64),
    CHECK(settlement_reference IS NULL OR length(settlement_reference) BETWEEN 6 AND 64)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_payment_claims_submitted_order
    ON manual_payment_claims(order_no)
    WHERE status='submitted';
CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_payment_claims_settlement_reference
    ON manual_payment_claims(settlement_reference)
    WHERE status='approved';
CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_payment_claims_source_update
    ON manual_payment_claims(source_update_id)
    WHERE source_update_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_manual_payment_claims_user_created
    ON manual_payment_claims(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_manual_payment_claims_status_created
    ON manual_payment_claims(status,created_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_manual_payment_claims_attempt_immutable
BEFORE UPDATE OF attempt ON manual_payment_claims
BEGIN
    SELECT RAISE(ABORT, 'manual payment claim attempt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_manual_payment_claims_status_transition
BEFORE UPDATE OF status ON manual_payment_claims
WHEN NOT (OLD.status='submitted' AND NEW.status IN ('approved','rejected'))
BEGIN
    SELECT RAISE(ABORT, 'manual payment claim status is immutable');
END;

CREATE TABLE IF NOT EXISTS telegram_callback_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id TEXT NOT NULL UNIQUE,
    user_id INTEGER,
    chat_id TEXT,
    claim_id INTEGER,
    payload_fingerprint TEXT NOT NULL,
    received_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (claim_id) REFERENCES manual_payment_claims(id)
);

CREATE INDEX IF NOT EXISTS idx_telegram_callback_receipts_received
    ON telegram_callback_receipts(received_at DESC);
