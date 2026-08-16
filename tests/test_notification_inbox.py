import sqlite3
import pytest

from core.account_center import AccountCenterError, AccountCenterNotFound, AccountCenterService, IdempotencyConflict, request_sha256
from core.database import DatabaseManager
from core.notification_inbox import NotificationInboxService


@pytest.fixture()
def service(tmp_path):
    db = DatabaseManager(str(tmp_path / "inbox.db"))
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES (?,?,?)", ("one@example.com", "x", "2026-01-01"))
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES (?,?,?)", ("two@example.com", "x", "2026-01-01"))
    return NotificationInboxService(db, appearance_entitlement_resolver=lambda owner, skin, version, digest: owner == 1 and skin == "free" and version == "v1")


def test_notification_delivery_read_are_separate_and_idempotent(service):
    item = service.create_item(1, {"source_kind": "test", "source_public_id": "source_aaaaaaaaaaaaaaaaaaaaaaaa", "source_version": 1, "kind": "system", "title": "Hello", "body": "World"}, "notice-001")
    assert set(item) == {"public_id"}
    delivery = service.create_delivery(1, item["public_id"], "website", "delivery-001")
    event = service.create_delivery_event(1, delivery["public_id"], "delivered", "delivery-event-001")
    assert event["status"] == "delivered"
    read = service.create_read_event(1, item["public_id"], "read-001")
    assert read["item_public_id"] == item["public_id"]
    snapshot = service.list_notifications(1)[0]
    assert snapshot["read"] is True
    assert snapshot["delivery"][0]["status"] == "delivered"
    assert service.create_delivery_event(1, delivery["public_id"], "delivered", "delivery-event-001")["public_id"] == event["public_id"]
    with pytest.raises(IdempotencyConflict):
        service.create_delivery_event(1, delivery["public_id"], "failed", "delivery-event-001")


def test_deep_link_owner_check_and_stale_fallback(service):
    item = service.create_item(1, {"source_kind": "test", "source_public_id": "source_bbbbbbbbbbbbbbbbbbbbbbbb", "source_version": 1, "kind": "system", "title": "Hello", "body": "World"}, "notice-002")
    with pytest.raises(AccountCenterNotFound):
        service.resolve_deep_link(2, "notifications", item["public_id"], 1, allow_fallback=False)
    stale = service.resolve_deep_link(1, "content", "cnt_aaaaaaaaaaaaaaaaaaaaaaaa", 9)
    assert stale == {"target_kind": "notifications", "public_id": None, "version": 1, "stale": True}
    unsupported = service.resolve_deep_link(1, "research", "rsh_aaaaaaaaaaaaaaaaaaaaaaaa", 1)
    assert unsupported == {"target_kind": "notifications", "public_id": None, "version": 1, "stale": True}
    with pytest.raises(AccountCenterNotFound):
        service.resolve_deep_link(1, "research", "rsh_aaaaaaaaaaaaaaaaaaaaaaaa", 1, allow_fallback=False)
    with pytest.raises(AccountCenterError):
        service.create_item(1, {"source_kind": "test", "source_public_id": "source_cccccccccccccccccccccccc", "source_version": 1, "kind": "xx", "title": "x", "body": "x", "target": {"target_kind": "https", "public_id": "https://evil", "version": 1}}, "notice-003")
    with pytest.raises(AccountCenterError):
        service.create_item(1, {"source_kind": "test", "source_public_id": "source_dddddddddddddddddddddddd", "source_version": 1, "kind": "xx", "title": "x", "body": "x", "target": {"target_kind": "content", "public_id": "//evil", "version": 1}}, "notice-005")
    with pytest.raises(AccountCenterError):
        service.create_item(1, {"source_kind": "test", "source_public_id": "source_eeeeeeeeeeeeeeeeeeeeeeee", "source_version": 1, "kind": "xx", "title": "x", "body": "x", "target": {"target_kind": "CONTENT", "public_id": "cnt_aaaaaaaaaaaaaaaaaaaaaaaa", "version": 1}}, "notice-006")
    assert "id" not in service.list_notifications(1)[0]
    manifest = {"skin_id": "free", "asset_version": "v1", "assets": {"core": "asset"}}
    manifest["manifest_sha256"] = request_sha256(manifest)
    appearance = AccountCenterService(service.db).register_manifest(manifest, "manifest-deeplink-01")
    assert service.resolve_deep_link(1, "appearance", appearance["public_id"], 1)["stale"] is False
    with pytest.raises(AccountCenterNotFound):
        service.resolve_deep_link(1, "appearance", appearance["public_id"], 2, allow_fallback=False)
    with pytest.raises(AccountCenterNotFound):
        service.resolve_deep_link(1, "notifications", item["public_id"], 2, allow_fallback=False)


def test_notification_resolution_uses_only_the_stored_owner_scoped_target(service):
    memory = AccountCenterService(service.db).put_memory(
        1, "notification-target", {"preference": "evidence-first"}, "memory-target-01"
    )
    item = service.create_item(
        1,
        {
            "source_kind": "test",
            "source_public_id": "source_targetaaaaaaaaaaaaaaaaaa",
            "source_version": 1,
            "kind": "system",
            "title": "Memory updated",
            "body": "Open the exact memory entry.",
            "target": {"target_kind": "memory", "public_id": memory["public_id"], "version": 1},
        },
        "notice-target-01",
    )
    assert service.resolve_notification(1, item["public_id"]) == {
        "route": "/account",
        "locator": {"kind": "memory", "public_id": memory["public_id"], "version": 1},
        "stale": False,
    }
    with pytest.raises(AccountCenterNotFound):
        service.resolve_notification(2, item["public_id"])
    with pytest.raises(AccountCenterNotFound):
        service.resolve_notification(1, "ntf_aaaaaaaaaaaaaaaaaaaaaaaa")
    with pytest.raises(AccountCenterNotFound):
        service.resolve_deep_link(1, "memory", memory["public_id"], 2, allow_fallback=False)

    AccountCenterService(service.db).tombstone_memory(
        1, memory["public_id"], "notification target removed", "memory-target-delete-01"
    )
    assert service.resolve_notification(1, item["public_id"]) == {
        "route": "/notifications", "locator": None, "stale": True,
    }


def test_sql_owner_consistency_triggers(service):
    item = service.create_item(1, {"source_kind": "test", "source_public_id": "source_ffffffffffffffffffffffff", "source_version": 1, "kind": "system", "title": "Hello", "body": "World"}, "notice-004")
    memory = AccountCenterService(service.db).put_memory(1, "secret", {"x": 1}, "memory-owner-01")
    with service.db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="tombstone owner"):
            conn.execute("INSERT INTO account_memory_tombstone_events (public_id,owner_id,memory_public_id,reason,idempotency_key,request_sha256,created_at) VALUES (?,?,?,?,?,?,?)", ("mtr_invalid", 2, memory["public_id"], "x", "tombstone-sql-01", "1" * 64, "2026-01-01T00:00:00+00:00"))
        with pytest.raises(sqlite3.IntegrityError, match="delivery owner"):
            conn.execute("INSERT INTO notification_deliveries (public_id,owner_id,item_public_id,channel,idempotency_key,request_sha256,created_at) VALUES (?,?,?,?,?,?,?)", ("dly_invalid", 2, item["public_id"], "website", "delivery-sql-01", "2" * 64, "2026-01-01T00:00:00+00:00"))


def test_raw_fk_off_rejects_fake_notification_owner(service):
    connection = sqlite3.connect(service.db._db_path)
    connection.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(sqlite3.IntegrityError, match="notification owner"):
        connection.execute("""INSERT INTO notification_items
            (public_id,owner_id,source_kind,source_public_id,source_version,payload_sha256,kind,title,body,severity,idempotency_key,request_sha256,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("ntf_fake_owner_000000000000000", 999, "test", "source-fkoff", 1, "0" * 64, "x", "x", "x", "info", "notice-fkoff-01", "1" * 64, "2026-01-01T00:00:00+00:00"))
    connection.close()
