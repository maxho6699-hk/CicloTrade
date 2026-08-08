CREATE TABLE IF NOT EXISTS telegram_service_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    chat_id TEXT NOT NULL,
    message TEXT NOT NULL,
    buttons_json TEXT,
    copy_from_chat_id TEXT,
    copy_message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','sending','sent','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    message_sent_at TEXT,
    copy_sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    CHECK(length(dedupe_key) BETWEEN 1 AND 160),
    CHECK(length(chat_id) BETWEEN 1 AND 20),
    CHECK(length(message) BETWEEN 1 AND 4096),
    CHECK(copy_message_id IS NULL OR copy_message_id > 0)
);

CREATE INDEX IF NOT EXISTS idx_telegram_service_outbox_due
    ON telegram_service_outbox(status,next_attempt_at,id);
