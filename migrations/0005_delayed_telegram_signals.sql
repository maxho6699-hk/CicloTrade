CREATE TABLE IF NOT EXISTS telegram_delayed_group_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    delay_minutes INTEGER NOT NULL CHECK(delay_minutes IN (15,60)),
    status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE(event_id,instrument_type),
    FOREIGN KEY (event_id) REFERENCES quant_events(id)
);

CREATE INDEX IF NOT EXISTS idx_tg_delayed_delivery_pending
    ON telegram_delayed_group_deliveries(status,next_attempt_at,id);
