from datetime import datetime, timedelta

import sqlite3
import pytest

from core.account_center import (
    AccountCenterError,
    AccountCenterNotFound,
    AccountCenterService,
    IdempotencyConflict,
    request_sha256,
)
from core.database import DatabaseManager


@pytest.fixture()
def service(tmp_path):
    db = DatabaseManager(str(tmp_path / "account.db"))
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES (?,?,?)", ("one@example.com", "x", "2026-01-01"))
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES (?,?,?)", ("two@example.com", "x", "2026-01-01"))
    db.execute("INSERT INTO membership_entitlements(user_id,plan_type,starts_at,expires_at,source_kind,source_ref,created_at) VALUES (?,?,?,?,?,?,?)", (1, "免费版", "2026-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00", "test", "appearance", "2026-01-01T00:00:00+00:00"))
    return AccountCenterService(db, appearance_entitlement_resolver=lambda owner, skin, version, digest: owner == 1 and skin == "free" and version == "v1")


def _manifest(**changes):
    value = {"manifest_key": "ciclo", "skin_id": "free", "asset_version": "v1", "assets": {"core": "asset"}}
    value.update(changes)
    value["manifest_sha256"] = request_sha256({k: v for k, v in value.items() if k != "manifest_sha256"})
    return value


def test_manifest_hash_history_immutability_and_fallback(service):
    manifest = service.register_manifest(_manifest(), "manifest-001")
    assert service.register_manifest(_manifest(), "manifest-001")["public_id"] == manifest["public_id"]
    with pytest.raises(AccountCenterError, match="hash 不匹配"):
        service.register_manifest({**_manifest(), "manifest_sha256": "0" * 64}, "manifest-002")
    frozen = _manifest(assets={"core": "different"})
    frozen["asset_version"] = "v1"
    frozen["manifest_sha256"] = request_sha256({k: v for k, v in frozen.items() if k != "manifest_sha256"})
    with pytest.raises(AccountCenterError, match="历史 manifest 已冻结"):
        service.register_manifest(frozen, "manifest-003")
    assert service.current_appearance(1)["source"] == "fallback"


def test_idempotency_conflict_and_cross_user_404(service):
    first = service.put_memory(1, "risk", {"enabled": True}, "memory-001")
    assert service.put_memory(1, "risk", {"enabled": True}, "memory-001")["public_id"] == first["public_id"]
    with pytest.raises(IdempotencyConflict):
        service.put_memory(1, "risk", {"enabled": False}, "memory-001")
    with pytest.raises(AccountCenterNotFound):
        service.tombstone_memory(2, first["public_id"], "cross-user", "memory-002")
    service.tombstone_memory(1, first["public_id"], "replace", "memory-003")
    replacement = service.put_memory(1, "risk", {"enabled": False}, "memory-004")
    assert replacement["public_id"] != first["public_id"]


def test_memory_tombstone_and_expiry(service):
    expired = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
    old = service.put_memory(1, "expired", {"x": 1}, "memory-expired", expired)
    current = service.put_memory(1, "current", {"x": 2}, "memory-current")
    assert {item["public_id"] for item in service.list_memories(1)} == {current["public_id"]}
    service.tombstone_memory(1, current["public_id"], "user removed", "memory-tombstone")
    assert service.list_memories(1) == []
    with pytest.raises(Exception, match="append-only"):
        service.db.execute("UPDATE account_memory_entries SET memory_key='x' WHERE public_id=?", (old["public_id"],))


def test_authorization_revoke_and_content_version_unique(service):
    service.authorize_data(1, "quotes", {"pages": ["research"]}, "grant", "auth-grant-01")
    assert service.authorization_status(1, "quotes")["authorized"] is True
    service.authorize_data(1, "quotes", {"pages": ["research"]}, "revoke", "auth-revoke-01")
    assert service.authorization_status(1, "quotes")["authorized"] is False
    service.index_content(1, "account-guide", 1, {"title": "v1"}, "content-001")
    indexed = service.list_content(1)
    assert indexed[0]["content"] == {"title": "v1"}
    assert "owner_id" not in indexed[0]
    with pytest.raises(AccountCenterError, match="版本号已存在"):
        service.index_content(1, "account-guide", 1, {"title": "changed"}, "content-002")


def test_account_overview_fails_closed_for_unconfigured_levels(service):
    overview = service.account_overview(1)
    assert all(item == {"level": None, "policy_state": "not_configured"} for item in overview["agent_levels"].values())
    assert overview["runtime"]["auto_live"] == "not_ready"
    assert "id" not in overview["account"]


def test_selection_requires_exact_resolver_and_sql_manifest_proof(service):
    manifest = service.register_manifest(_manifest(), "manifest-proof-01")
    locked = AccountCenterService(service.db)
    with pytest.raises(AccountCenterError, match="entitlement"):
        locked.select_appearance(1, manifest["public_id"], "selection-locked")
    with service.db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="manifest proof"):
            conn.execute("""INSERT INTO account_appearance_selection_events
                (public_id,owner_id,manifest_public_id,skin_id,asset_version,manifest_sha256,idempotency_key,request_sha256,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""", ("sel_invalid", 1, manifest["public_id"], "free", "v1", "0" * 64, "selection-sql-01", "1" * 64, "2026-01-01T00:00:00+00:00"))


def test_current_appearance_reverts_to_highest_still_entitled(service):
    allowed = {("free", "v1"), ("standard", "v1")}
    def resolver(owner, skin, version, digest):
        return {"allowed": (skin, version) in allowed, "rank": {"free": 1, "standard": 2}.get(skin, 0)}

    scoped = AccountCenterService(service.db, appearance_entitlement_resolver=resolver)
    free = scoped.register_manifest(_manifest(), "manifest-revert-free")
    standard = scoped.register_manifest(_manifest(skin_id="standard"), "manifest-revert-standard")
    scoped.select_appearance(1, free["public_id"], "selection-revert-free")
    scoped.select_appearance(1, standard["public_id"], "selection-revert-standard")
    assert scoped.current_appearance(1)["skin_id"] == "standard"
    allowed.remove(("standard", "v1"))
    assert scoped.current_appearance(1)["skin_id"] == "free"
    appearances = [
        item for item in scoped.list_appearances(1)
        if item["skin_id"] in {"free", "standard"}
    ]
    assert [(item["skin_id"], item["entitled"], item["rank"]) for item in appearances] == [
        ("free", True, 1),
        ("standard", False, 2),
    ]
    assert appearances[0]["assets"] == {"core": "asset"}


def test_service_requires_explicit_database():
    with pytest.raises(AccountCenterError, match="显式 database"):
        AccountCenterService()


def test_raw_fk_off_rejects_fake_owner_and_orphan_selection(service):
    connection = sqlite3.connect(service.db._db_path)
    connection.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(sqlite3.IntegrityError, match="owner does not exist"):
        connection.execute("""INSERT INTO account_content_index
            (public_id,owner_id,content_key,content_version,content_json,content_sha256,idempotency_key,request_sha256,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""", ("cnt_fake_owner_0000000000000000", 999, "x", 1, "{}", "0" * 64, "content-fkoff-01", "1" * 64, "2026-01-01T00:00:00+00:00"))
    with pytest.raises(sqlite3.IntegrityError, match="manifest proof"):
        connection.execute("""INSERT INTO account_appearance_selection_events
            (public_id,owner_id,manifest_public_id,skin_id,asset_version,manifest_sha256,idempotency_key,request_sha256,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""", ("sel_orphan_00000000000000000", 1, "man_orphan_0000000000000000", "free", "v1", "0" * 64, "selection-fkoff-01", "1" * 64, "2026-01-01T00:00:00+00:00"))
    connection.close()
