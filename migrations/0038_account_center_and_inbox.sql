-- Account growth, memory, authorization and notification inbox.
-- Every table is append-only.  Mutable projections (delivery state and
-- current authorization) are reconstructed from their event streams.

CREATE TABLE account_appearance_manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    manifest_key TEXT NOT NULL,
    skin_id TEXT NOT NULL,
    asset_version TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    owner_id INTEGER REFERENCES users(id),
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE(manifest_key, skin_id, asset_version),
    UNIQUE(created_by, idempotency_key),
    CHECK(length(public_id) BETWEEN 16 AND 160),
    CHECK(length(manifest_sha256)=64),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE account_appearance_selection_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    manifest_public_id TEXT NOT NULL REFERENCES account_appearance_manifests(public_id),
    skin_id TEXT NOT NULL,
    asset_version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE account_content_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    content_key TEXT NOT NULL,
    content_version INTEGER NOT NULL CHECK(content_version >= 1),
    content_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    expires_at TEXT,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, content_key, content_version),
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(content_sha256)=64),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE account_memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    memory_key TEXT NOT NULL,
    memory_json TEXT NOT NULL,
    expires_at TEXT,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE account_memory_tombstone_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    memory_public_id TEXT NOT NULL REFERENCES account_memory_entries(public_id),
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE account_data_authorization_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    data_kind TEXT NOT NULL,
    policy_type TEXT NOT NULL,
    policy_version INTEGER NOT NULL CHECK(policy_version >= 1),
    policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
    scope_json TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('granted','revoked')),
    request_identity TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE notification_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    source_kind TEXT NOT NULL,
    source_public_id TEXT NOT NULL,
    source_version INTEGER NOT NULL CHECK(source_version >= 1),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    target_kind TEXT,
    target_public_id TEXT,
    target_version INTEGER,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, idempotency_key),
    UNIQUE(owner_id, source_kind, source_public_id, source_version),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    CHECK((target_kind IS NULL AND target_public_id IS NULL AND target_version IS NULL)
       OR (target_kind IS NOT NULL AND target_public_id IS NOT NULL AND target_version IS NOT NULL))
);

CREATE TABLE notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    item_public_id TEXT NOT NULL REFERENCES notification_items(public_id),
    channel TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, item_public_id, channel),
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE notification_delivery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    delivery_public_id TEXT NOT NULL REFERENCES notification_deliveries(public_id),
    status TEXT NOT NULL CHECK(status IN ('queued','sending','sent','delivered','failed','skipped')),
    error_code TEXT,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE notification_read_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    item_public_id TEXT NOT NULL REFERENCES notification_items(public_id),
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, item_public_id, idempotency_key),
    CHECK(length(request_sha256)=64),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE INDEX idx_account_selection_owner ON account_appearance_selection_events(owner_id, created_at);
CREATE UNIQUE INDEX idx_account_manifest_skin_version ON account_appearance_manifests(skin_id, asset_version);
CREATE UNIQUE INDEX idx_account_manifest_actor_idempotency ON account_appearance_manifests(COALESCE(created_by, 0), idempotency_key);
CREATE INDEX idx_account_content_owner ON account_content_index(owner_id, content_key, content_version);
CREATE INDEX idx_account_memory_owner ON account_memory_entries(owner_id, created_at);
CREATE INDEX idx_account_memory_tombstones_owner ON account_memory_tombstone_events(owner_id, created_at);
CREATE INDEX idx_account_authorization_owner ON account_data_authorization_receipts(owner_id, data_kind, created_at);
CREATE UNIQUE INDEX idx_notification_source_payload ON notification_items(owner_id, source_kind, source_public_id, source_version, payload_sha256);
CREATE INDEX idx_notification_items_owner ON notification_items(owner_id, created_at);
CREATE INDEX idx_notification_deliveries_owner ON notification_deliveries(owner_id, item_public_id);
CREATE INDEX idx_notification_delivery_events_owner ON notification_delivery_events(owner_id, delivery_public_id, created_at);
CREATE INDEX idx_notification_read_events_owner ON notification_read_events(owner_id, item_public_id, created_at);


CREATE TRIGGER trg_account_selection_manifest_reference
BEFORE INSERT ON account_appearance_selection_events
WHEN NOT EXISTS (
  SELECT 1 FROM account_appearance_manifests m
  WHERE m.public_id=NEW.manifest_public_id AND m.skin_id=NEW.skin_id
    AND m.asset_version=NEW.asset_version AND m.manifest_sha256=NEW.manifest_sha256
    AND (m.owner_id IS NULL OR m.owner_id=NEW.owner_id)
)
BEGIN SELECT RAISE(ABORT, 'appearance selection manifest proof is invalid'); END;
CREATE TRIGGER trg_account_tombstone_owner_consistency
BEFORE INSERT ON account_memory_tombstone_events
WHEN (SELECT owner_id FROM account_memory_entries WHERE public_id=NEW.memory_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'memory tombstone owner mismatch'); END;
CREATE TRIGGER trg_notification_delivery_owner_consistency
BEFORE INSERT ON notification_deliveries
WHEN (SELECT owner_id FROM notification_items WHERE public_id=NEW.item_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'notification delivery owner mismatch'); END;
CREATE TRIGGER trg_notification_delivery_event_owner_consistency
BEFORE INSERT ON notification_delivery_events
WHEN (SELECT owner_id FROM notification_deliveries WHERE public_id=NEW.delivery_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'notification delivery event owner mismatch'); END;
CREATE TRIGGER trg_notification_read_owner_consistency
BEFORE INSERT ON notification_read_events
WHEN (SELECT owner_id FROM notification_items WHERE public_id=NEW.item_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'notification read owner mismatch'); END;

CREATE TRIGGER trg_account_manifest_owner_exists
BEFORE INSERT ON account_appearance_manifests
WHEN NEW.owner_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'appearance manifest owner does not exist'); END;
CREATE TRIGGER trg_account_manifest_actor_exists
BEFORE INSERT ON account_appearance_manifests
WHEN NEW.created_by IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.created_by AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'appearance manifest actor does not exist'); END;
CREATE TRIGGER trg_account_selection_owner_exists
BEFORE INSERT ON account_appearance_selection_events
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'appearance selection owner does not exist'); END;
CREATE TRIGGER trg_account_content_owner_exists
BEFORE INSERT ON account_content_index
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'content owner does not exist'); END;
CREATE TRIGGER trg_account_memory_owner_exists
BEFORE INSERT ON account_memory_entries
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'memory owner does not exist'); END;
CREATE TRIGGER trg_account_tombstone_owner_exists
BEFORE INSERT ON account_memory_tombstone_events
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'memory tombstone owner does not exist'); END;
CREATE TRIGGER trg_account_authorization_owner_exists
BEFORE INSERT ON account_data_authorization_receipts
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'authorization owner does not exist'); END;
CREATE TRIGGER trg_notification_item_owner_exists
BEFORE INSERT ON notification_items
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'notification owner does not exist'); END;
CREATE TRIGGER trg_notification_delivery_owner_exists
BEFORE INSERT ON notification_deliveries
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'notification delivery owner does not exist'); END;
CREATE TRIGGER trg_notification_event_owner_exists
BEFORE INSERT ON notification_delivery_events
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'notification delivery event owner does not exist'); END;
CREATE TRIGGER trg_notification_read_owner_exists
BEFORE INSERT ON notification_read_events
WHEN NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.owner_id AND is_active=1)
BEGIN SELECT RAISE(ABORT, 'notification read owner does not exist'); END;

CREATE TRIGGER trg_account_appearance_manifests_no_update BEFORE UPDATE ON account_appearance_manifests BEGIN SELECT RAISE(ABORT, 'appearance manifests are append-only'); END;
CREATE TRIGGER trg_account_appearance_manifests_no_delete BEFORE DELETE ON account_appearance_manifests BEGIN SELECT RAISE(ABORT, 'appearance manifests are append-only'); END;
CREATE TRIGGER trg_account_appearance_selection_no_update BEFORE UPDATE ON account_appearance_selection_events BEGIN SELECT RAISE(ABORT, 'appearance selection events are append-only'); END;
CREATE TRIGGER trg_account_appearance_selection_no_delete BEFORE DELETE ON account_appearance_selection_events BEGIN SELECT RAISE(ABORT, 'appearance selection events are append-only'); END;
CREATE TRIGGER trg_account_content_index_no_update BEFORE UPDATE ON account_content_index BEGIN SELECT RAISE(ABORT, 'content index is append-only'); END;
CREATE TRIGGER trg_account_content_index_no_delete BEFORE DELETE ON account_content_index BEGIN SELECT RAISE(ABORT, 'content index is append-only'); END;
CREATE TRIGGER trg_account_memory_entries_no_update BEFORE UPDATE ON account_memory_entries BEGIN SELECT RAISE(ABORT, 'memory entries are append-only'); END;
CREATE TRIGGER trg_account_memory_entries_no_delete BEFORE DELETE ON account_memory_entries BEGIN SELECT RAISE(ABORT, 'memory entries are append-only'); END;
CREATE TRIGGER trg_account_memory_tombstones_no_update BEFORE UPDATE ON account_memory_tombstone_events BEGIN SELECT RAISE(ABORT, 'memory tombstones are append-only'); END;
CREATE TRIGGER trg_account_memory_tombstones_no_delete BEFORE DELETE ON account_memory_tombstone_events BEGIN SELECT RAISE(ABORT, 'memory tombstones are append-only'); END;
CREATE TRIGGER trg_account_authorization_no_update BEFORE UPDATE ON account_data_authorization_receipts BEGIN SELECT RAISE(ABORT, 'authorization receipts are append-only'); END;
CREATE TRIGGER trg_account_authorization_no_delete BEFORE DELETE ON account_data_authorization_receipts BEGIN SELECT RAISE(ABORT, 'authorization receipts are append-only'); END;
CREATE TRIGGER trg_notification_items_no_update BEFORE UPDATE ON notification_items BEGIN SELECT RAISE(ABORT, 'notification items are append-only'); END;
CREATE TRIGGER trg_notification_items_no_delete BEFORE DELETE ON notification_items BEGIN SELECT RAISE(ABORT, 'notification items are append-only'); END;
CREATE TRIGGER trg_notification_deliveries_no_update BEFORE UPDATE ON notification_deliveries BEGIN SELECT RAISE(ABORT, 'notification deliveries are append-only'); END;
CREATE TRIGGER trg_notification_deliveries_no_delete BEFORE DELETE ON notification_deliveries BEGIN SELECT RAISE(ABORT, 'notification deliveries are append-only'); END;
CREATE TRIGGER trg_notification_delivery_events_no_update BEFORE UPDATE ON notification_delivery_events BEGIN SELECT RAISE(ABORT, 'notification delivery events are append-only'); END;
CREATE TRIGGER trg_notification_delivery_events_no_delete BEFORE DELETE ON notification_delivery_events BEGIN SELECT RAISE(ABORT, 'notification delivery events are append-only'); END;
CREATE TRIGGER trg_notification_read_events_no_update BEFORE UPDATE ON notification_read_events BEGIN SELECT RAISE(ABORT, 'notification read events are append-only'); END;
CREATE TRIGGER trg_notification_read_events_no_delete BEFORE DELETE ON notification_read_events BEGIN SELECT RAISE(ABORT, 'notification read events are append-only'); END;
