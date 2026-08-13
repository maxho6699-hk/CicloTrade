CREATE TABLE membership_entitlement_policy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    policy_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(created_by) REFERENCES users(id),
    UNIQUE(policy_key, version),
    UNIQUE(policy_key, policy_sha256, effective_at),
    CHECK(length(policy_key) BETWEEN 3 AND 96),
    CHECK(length(policy_sha256) = 64),
    CHECK(length(policy_json) >= 2)
);

CREATE INDEX idx_membership_entitlement_policy_effective
ON membership_entitlement_policy_versions(policy_key, effective_at, version);

CREATE TRIGGER trg_membership_entitlement_policy_no_update
BEFORE UPDATE ON membership_entitlement_policy_versions
BEGIN
    SELECT RAISE(ABORT, 'entitlement policy versions are append-only');
END;

CREATE TRIGGER trg_membership_entitlement_policy_no_delete
BEFORE DELETE ON membership_entitlement_policy_versions
BEGIN
    SELECT RAISE(ABORT, 'entitlement policy versions are append-only');
END;

CREATE TABLE membership_entitlement_policy_admin_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    policy_key TEXT NOT NULL,
    policy_version INTEGER NOT NULL CHECK(policy_version >= 1),
    policy_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(actor_id) REFERENCES users(id),
    UNIQUE(actor_id, idempotency_key),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    CHECK(length(request_sha256) = 64),
    CHECK(length(policy_sha256) = 64)
);

CREATE TRIGGER trg_membership_entitlement_policy_events_no_update
BEFORE UPDATE ON membership_entitlement_policy_admin_events
BEGIN SELECT RAISE(ABORT, 'entitlement policy admin events are append-only'); END;

CREATE TRIGGER trg_membership_entitlement_policy_events_no_delete
BEFORE DELETE ON membership_entitlement_policy_admin_events
BEGIN SELECT RAISE(ABORT, 'entitlement policy admin events are append-only'); END;

CREATE TRIGGER trg_membership_entitlement_policy_events_reference
BEFORE INSERT ON membership_entitlement_policy_admin_events
WHEN NOT EXISTS (
    SELECT 1 FROM membership_entitlement_policy_versions
    WHERE policy_key=NEW.policy_key
      AND version=NEW.policy_version
      AND policy_sha256=NEW.policy_sha256
)
BEGIN SELECT RAISE(ABORT, 'entitlement policy admin event proof is invalid'); END;

ALTER TABLE subscription_orders ADD COLUMN entitlement_policy_key_snapshot TEXT;
ALTER TABLE subscription_orders ADD COLUMN entitlement_policy_version_snapshot INTEGER;
ALTER TABLE subscription_orders ADD COLUMN entitlement_policy_sha256_snapshot TEXT;

CREATE TABLE membership_entitlement_legacy_orders (
    order_no TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY(order_no) REFERENCES subscription_orders(order_no)
);

INSERT INTO membership_entitlement_legacy_orders(order_no,recorded_at)
SELECT order_no,strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM subscription_orders;

CREATE TRIGGER trg_membership_entitlement_legacy_orders_no_insert
BEFORE INSERT ON membership_entitlement_legacy_orders
BEGIN SELECT RAISE(ABORT, 'legacy entitlement order allowlist is sealed'); END;

CREATE TRIGGER trg_membership_entitlement_legacy_orders_no_update
BEFORE UPDATE ON membership_entitlement_legacy_orders
BEGIN SELECT RAISE(ABORT, 'legacy entitlement order allowlist is sealed'); END;

CREATE TRIGGER trg_membership_entitlement_legacy_orders_no_delete
BEFORE DELETE ON membership_entitlement_legacy_orders
BEGIN SELECT RAISE(ABORT, 'legacy entitlement order allowlist is sealed'); END;

CREATE TRIGGER trg_subscription_order_entitlement_snapshot_insert
BEFORE INSERT ON subscription_orders
WHEN NOT (
    (
        NEW.entitlement_policy_key_snapshot IS NULL AND
        NEW.entitlement_policy_version_snapshot IS NULL AND
        NEW.entitlement_policy_sha256_snapshot IS NULL
    ) OR (
        NEW.entitlement_policy_key_snapshot IS NOT NULL AND
        NEW.entitlement_policy_version_snapshot IS NOT NULL AND
        NEW.entitlement_policy_sha256_snapshot IS NOT NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'entitlement policy snapshot must be complete');
END;

CREATE TRIGGER trg_subscription_order_entitlement_snapshot_update
BEFORE UPDATE OF entitlement_policy_key_snapshot,entitlement_policy_version_snapshot,
                 entitlement_policy_sha256_snapshot ON subscription_orders
WHEN NOT (
    (
        OLD.entitlement_policy_key_snapshot IS NULL AND
        OLD.entitlement_policy_version_snapshot IS NULL AND
        OLD.entitlement_policy_sha256_snapshot IS NULL AND
        NEW.entitlement_policy_key_snapshot IS NULL AND
        NEW.entitlement_policy_version_snapshot IS NULL AND
        NEW.entitlement_policy_sha256_snapshot IS NULL
    ) OR (
        OLD.entitlement_policy_key_snapshot IS NULL AND
        OLD.entitlement_policy_version_snapshot IS NULL AND
        OLD.entitlement_policy_sha256_snapshot IS NULL AND
        NEW.entitlement_policy_key_snapshot IS NOT NULL AND
        NEW.entitlement_policy_version_snapshot IS NOT NULL AND
        NEW.entitlement_policy_sha256_snapshot IS NOT NULL
    ) OR (
        NEW.entitlement_policy_key_snapshot IS OLD.entitlement_policy_key_snapshot AND
        NEW.entitlement_policy_version_snapshot IS OLD.entitlement_policy_version_snapshot AND
        NEW.entitlement_policy_sha256_snapshot IS OLD.entitlement_policy_sha256_snapshot
    )
)
BEGIN SELECT RAISE(ABORT, 'entitlement policy snapshot is immutable'); END;
