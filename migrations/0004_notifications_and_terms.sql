ALTER TABLE subscription_orders ADD COLUMN terms_version TEXT;
ALTER TABLE subscription_orders ADD COLUMN terms_accepted_at TEXT;

CREATE TABLE IF NOT EXISTS telegram_group_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    group_name TEXT NOT NULL CHECK(group_name IN ('advanced','professional')),
    chat_id TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE(event_id,group_name,instrument_type,symbol),
    FOREIGN KEY (event_id) REFERENCES quant_events(id)
);

CREATE INDEX IF NOT EXISTS idx_tg_group_delivery_pending
    ON telegram_group_deliveries(status,next_attempt_at,id);
