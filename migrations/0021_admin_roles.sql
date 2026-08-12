CREATE TABLE IF NOT EXISTS admin_roles (
    user_id INTEGER PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('super_admin','support','finance','research','risk_audit')),
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

INSERT OR IGNORE INTO admin_roles (user_id, role, updated_at)
SELECT id, 'super_admin', COALESCE(created_at, datetime('now'))
FROM users
WHERE is_admin = 1;
