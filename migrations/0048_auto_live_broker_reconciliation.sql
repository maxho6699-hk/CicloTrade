-- Immutable broker observations used to reconcile auto-live order submission.
-- This ledger is read-only evidence: it grants no broker send capability.
CREATE TABLE IF NOT EXISTS auto_live_broker_reconciliation_receipts (
    receipt_id TEXT PRIMARY KEY CHECK(length(receipt_id) BETWEEN 16 AND 128),
    intent_public_id TEXT NOT NULL,
    mandate_public_id TEXT NOT NULL,
    client_order_id TEXT NOT NULL CHECK(length(client_order_id) BETWEEN 8 AND 128),
    provider TEXT NOT NULL CHECK(provider IN ('futu_moomoo','tiger','ibkr','webull','longbridge')),
    broker_account_sha256 TEXT NOT NULL CHECK(length(broker_account_sha256)=64),
    submission_state TEXT NOT NULL CHECK(submission_state IN ('accepted','rejected','submission_unknown','cancelled')),
    broker_order_id TEXT,
    broker_status TEXT NOT NULL CHECK(length(broker_status) BETWEEN 1 AND 128),
    observed_at TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (intent_public_id) REFERENCES auto_live_order_intents(public_id),
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id),
    UNIQUE(intent_public_id,evidence_sha256)
);

CREATE INDEX IF NOT EXISTS idx_auto_live_broker_receipts_intent_observed
ON auto_live_broker_reconciliation_receipts(intent_public_id,observed_at);

CREATE INDEX IF NOT EXISTS idx_auto_live_broker_receipts_mandate_client
ON auto_live_broker_reconciliation_receipts(mandate_public_id,client_order_id,observed_at);

CREATE TRIGGER IF NOT EXISTS trg_auto_live_broker_receipts_no_update
BEFORE UPDATE ON auto_live_broker_reconciliation_receipts BEGIN
    SELECT RAISE(ABORT, 'auto live broker reconciliation receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_broker_receipts_no_delete
BEFORE DELETE ON auto_live_broker_reconciliation_receipts BEGIN
    SELECT RAISE(ABORT, 'auto live broker reconciliation receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_broker_receipts_no_replace
BEFORE INSERT ON auto_live_broker_reconciliation_receipts
WHEN EXISTS(SELECT 1 FROM auto_live_broker_reconciliation_receipts WHERE receipt_id=NEW.receipt_id)
BEGIN
    SELECT RAISE(ABORT, 'auto live broker reconciliation receipts are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_broker_receipts_intent_consistency
BEFORE INSERT ON auto_live_broker_reconciliation_receipts
WHEN (SELECT mandate_public_id FROM auto_live_order_intents WHERE public_id=NEW.intent_public_id) IS NULL
  OR (SELECT mandate_public_id FROM auto_live_order_intents WHERE public_id=NEW.intent_public_id) <> NEW.mandate_public_id
  OR (SELECT client_order_id FROM auto_live_order_intents WHERE public_id=NEW.intent_public_id) <> NEW.client_order_id
BEGIN
    SELECT RAISE(ABORT, 'auto live broker receipt intent mismatch');
END;
