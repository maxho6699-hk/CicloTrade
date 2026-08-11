CREATE TABLE IF NOT EXISTS membership_entitlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_type TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    duration_days INTEGER CHECK (duration_days IS NULL OR duration_days > 0),
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE (source_kind, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_membership_entitlements_user_active
ON membership_entitlements(user_id,status,starts_at,expires_at);

INSERT OR IGNORE INTO membership_entitlements
    (user_id,plan_type,starts_at,expires_at,duration_days,source_kind,source_ref,status,created_at)
SELECT id,
       plan_type,
       COALESCE(created_at, CURRENT_TIMESTAMP),
       subscription_expire,
       NULL,
       'legacy_cache',
       'user:' || id || ':' || plan_type || ':' || subscription_expire,
       'active',
       CURRENT_TIMESTAMP
FROM users
WHERE plan_type IN ('标准版','高级版','专业版','定制版')
  AND subscription_expire IS NOT NULL
  AND datetime(subscription_expire)>datetime('now');
