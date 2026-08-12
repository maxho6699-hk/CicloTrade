CREATE TABLE IF NOT EXISTS user_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('bug','suggestion','data','experience','other')),
    message TEXT NOT NULL,
    message_sha256 TEXT NOT NULL,
    context_path TEXT,
    contact_preference TEXT NOT NULL CHECK(contact_preference IN ('none','email','telegram')),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received' CHECK(status IN ('received','reviewing','resolved','closed')),
    UNIQUE(user_id, idempotency_key),
    FOREIGN KEY(user_id) REFERENCES users(id),
    CHECK(length(public_id) BETWEEN 16 AND 80),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    CHECK(length(message) BETWEEN 1 AND 2000),
    CHECK(context_path IS NULL OR length(context_path) <= 300)
);

CREATE INDEX IF NOT EXISTS idx_user_feedback_user_created
    ON user_feedback(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_feedback_duplicate
    ON user_feedback(user_id, message_sha256, created_at DESC);
