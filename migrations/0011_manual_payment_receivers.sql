CREATE TABLE IF NOT EXISTS manual_payment_receivers (
    method TEXT PRIMARY KEY CHECK(method IN ('fps','alipay','wechat')),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    receiver_text TEXT,
    qr_storage_key TEXT,
    qr_sha256 TEXT,
    qr_telegram_file_id TEXT,
    qr_telegram_file_unique_id TEXT,
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (updated_by) REFERENCES users(id),
    CHECK(receiver_text IS NULL OR length(receiver_text) BETWEEN 1 AND 500),
    CHECK(qr_storage_key IS NULL OR length(qr_storage_key) BETWEEN 1 AND 80),
    CHECK(qr_sha256 IS NULL OR length(qr_sha256)=64),
    CHECK((qr_storage_key IS NULL)=(qr_sha256 IS NULL)),
    CHECK((qr_storage_key IS NULL)=(qr_telegram_file_id IS NULL)),
    CHECK((qr_storage_key IS NULL)=(qr_telegram_file_unique_id IS NULL)),
    CHECK(enabled=0 OR receiver_text IS NOT NULL OR qr_storage_key IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS subscription_order_payment_receivers (
    order_no TEXT PRIMARY KEY,
    method TEXT NOT NULL CHECK(method IN ('fps','alipay','wechat')),
    receiver_version INTEGER NOT NULL CHECK(receiver_version >= 0),
    receiver_text TEXT,
    qr_storage_key TEXT,
    qr_sha256 TEXT,
    qr_telegram_file_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_no) REFERENCES subscription_orders(order_no),
    CHECK(receiver_text IS NOT NULL OR qr_storage_key IS NOT NULL),
    CHECK((qr_storage_key IS NULL)=(qr_sha256 IS NULL)),
    CHECK((qr_storage_key IS NULL)=(qr_telegram_file_id IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_order_payment_receivers_method
    ON subscription_order_payment_receivers(method,created_at DESC);

CREATE TABLE IF NOT EXISTS telegram_payment_receiver_sessions (
    chat_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    method TEXT NOT NULL CHECK(method IN ('fps','alipay','wechat')),
    action TEXT NOT NULL CHECK(action IN ('receiver_text','qr')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_telegram_payment_receiver_sessions_expiry
    ON telegram_payment_receiver_sessions(expires_at);
