-- Immutable auto-live order intents and append-only lifecycle events.
CREATE TABLE IF NOT EXISTS auto_live_order_intents (
    public_id TEXT PRIMARY KEY CHECK(length(public_id) BETWEEN 16 AND 128),
    mandate_public_id TEXT NOT NULL,
    client_order_id TEXT NOT NULL UNIQUE CHECK(length(client_order_id) BETWEEN 8 AND 128),
    execution_mode TEXT NOT NULL CHECK(execution_mode IN ('shadow','paper','live')),
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
    strategy_version TEXT NOT NULL,
    risk_version TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('open','reduce_exposure','close_position')),
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    symbol TEXT NOT NULL CHECK(length(symbol) BETWEEN 1 AND 32),
    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    limit_price REAL NOT NULL CHECK(limit_price > 0),
    currency TEXT NOT NULL CHECK(length(currency)=3),
    quote_at TEXT NOT NULL,
    quote_sha256 TEXT NOT NULL CHECK(length(quote_sha256)=64),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    intent_json TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL CHECK(length(intent_sha256)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id)
);

CREATE TABLE IF NOT EXISTS auto_live_order_intent_events (
    event_id TEXT PRIMARY KEY CHECK(length(event_id) BETWEEN 16 AND 128),
    intent_public_id TEXT NOT NULL,
    mandate_public_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('shadowed','send_claimed','accepted','rejected','submission_unknown','cancelled','reconciled')),
    fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (intent_public_id) REFERENCES auto_live_order_intents(public_id),
    FOREIGN KEY (mandate_public_id) REFERENCES auto_live_mandates(public_id)
);

CREATE INDEX IF NOT EXISTS idx_auto_live_intents_mandate_created
ON auto_live_order_intents(mandate_public_id,created_at);

CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_intents_no_update
BEFORE UPDATE ON auto_live_order_intents BEGIN
    SELECT RAISE(ABORT, 'auto live order intents are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_intents_no_delete
BEFORE DELETE ON auto_live_order_intents BEGIN
    SELECT RAISE(ABORT, 'auto live order intents are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_intents_no_replace
BEFORE INSERT ON auto_live_order_intents
WHEN EXISTS(
    SELECT 1 FROM auto_live_order_intents
    WHERE public_id=NEW.public_id OR client_order_id=NEW.client_order_id
)
BEGIN
    SELECT RAISE(ABORT, 'auto live order intents are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_intent_events_no_update
BEFORE UPDATE ON auto_live_order_intent_events BEGIN
    SELECT RAISE(ABORT, 'auto live order intent events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_intent_events_no_delete
BEFORE DELETE ON auto_live_order_intent_events BEGIN
    SELECT RAISE(ABORT, 'auto live order intent events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_auto_live_order_intent_events_no_replace
BEFORE INSERT ON auto_live_order_intent_events
WHEN EXISTS(SELECT 1 FROM auto_live_order_intent_events WHERE event_id=NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'auto live order intent events are append-only');
END;
