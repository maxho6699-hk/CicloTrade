-- V2 official-paper events live in a separate append-only ledger.  Delivery
-- rows must not point at the legacy quant_events foreign key because IDs are
-- only unique inside their own ledger.
CREATE TABLE IF NOT EXISTS official_paper_event_deliveries_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('telegram')),
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE(event_id,user_id,channel,instrument_type,symbol),
    FOREIGN KEY (event_id) REFERENCES official_paper_events_v2(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_official_paper_v2_deliveries_pending
    ON official_paper_event_deliveries_v2(status,next_attempt_at,id);

CREATE TABLE IF NOT EXISTS official_paper_group_deliveries_v2 (
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
    FOREIGN KEY (event_id) REFERENCES official_paper_events_v2(id)
);

CREATE INDEX IF NOT EXISTS idx_official_paper_v2_group_due
    ON official_paper_group_deliveries_v2(status,next_attempt_at,id);

CREATE TABLE IF NOT EXISTS official_paper_delayed_group_deliveries_v2 (
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
    FOREIGN KEY (event_id) REFERENCES official_paper_events_v2(id)
);

CREATE INDEX IF NOT EXISTS idx_official_paper_v2_delayed_due
    ON official_paper_delayed_group_deliveries_v2(status,next_attempt_at,id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_official_paper_v2_corrects_once
    ON official_paper_events_v2(corrects_event_id) WHERE corrects_event_id IS NOT NULL;
