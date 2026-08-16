"""Source-bound truth contracts for migrations 0039, 0041 and 0042."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from core.account_center import AccountCenterError, AccountCenterService
from core.database import DatabaseManager


ROOT = Path(__file__).parents[1]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def test_0039_seed_manifests_bind_hashes_assets_and_unique_identity(tmp_path):
    db = DatabaseManager(str(tmp_path / "appearance-truth.db"))
    rows = db.fetch_all(
        "SELECT skin_id,asset_version,manifest_json,manifest_sha256 FROM account_appearance_manifests "
        "WHERE manifest_key='ciclo' ORDER BY id"
    )

    assert len(rows) == 4
    assert len({(row["skin_id"], row["asset_version"]) for row in rows}) == 4
    for row in rows:
        manifest = json.loads(row["manifest_json"])
        supplied = manifest.pop("manifest_sha256")
        assert supplied == row["manifest_sha256"] == _digest(manifest)
        for field in ("full", "preview"):
            asset = manifest["assets"][field]
            assert asset.startswith("/media/ciclo/")
            assert (ROOT / "src/apps/web/public" / asset.lstrip("/")).is_file()

    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO account_appearance_manifests "
                "(public_id,manifest_key,skin_id,asset_version,manifest_json,manifest_sha256,idempotency_key,request_sha256,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("man_duplicate_truth", "ciclo", rows[0]["skin_id"], rows[0]["asset_version"], "{}", "0" * 64, "duplicate-truth", "1" * 64, "2026-08-16"),
            )


def test_0039_entitlement_selection_fallback_and_downgrade_are_server_resolved(tmp_path):
    db = DatabaseManager(str(tmp_path / "appearance-entitlement.db"))
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES (?,?,?)", ("truth@example.com", "x", "2026-01-01"))
    allowed = {"shell-f0": 1, "shell-s1": 2, "shell-a2": 3, "shell-p3": 4}

    def resolver(owner, skin, version, digest):
        return {"allowed": skin in allowed, "rank": allowed.get(skin, 0)}

    service = AccountCenterService(db, appearance_entitlement_resolver=resolver)
    manifests = {item["skin_id"]: item for item in service.list_appearances(1)}
    service.select_appearance(1, manifests["shell-p3"]["public_id"], "truth-select-professional")
    assert service.current_appearance(1)["skin_id"] == "shell-p3"

    del allowed["shell-p3"]
    del allowed["shell-a2"]
    assert service.current_appearance(1)["skin_id"] == "shell-s1"
    del allowed["shell-s1"]
    assert service.current_appearance(1)["skin_id"] == "shell-f0"
    del allowed["shell-f0"]
    assert service.current_appearance(1)["source"] == "unavailable"


def test_0041_published_policy_is_hashed_and_scope_validation_fails_closed(tmp_path):
    db = DatabaseManager(str(tmp_path / "policy-truth.db"))
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES (?,?,?)", ("policy@example.com", "x", "2026-01-01"))
    policy = db.fetch_one(
        "SELECT policy_json,policy_version,policy_sha256 FROM account_data_policy_versions "
        "WHERE data_kind='quotes' AND status='published'"
    )
    body = json.loads(policy["policy_json"])
    assert policy["policy_version"] == 1
    assert policy["policy_sha256"] == _digest(body)

    service = AccountCenterService(db)
    with pytest.raises(AccountCenterError, match="页面范围无效"):
        service.authorize_data(1, "quotes", {"pages": ["not-a-page"]}, "grant", "policy-invalid-page")
    with pytest.raises(AccountCenterError, match="范围必须只包含 pages"):
        service.authorize_data(1, "quotes", {"pages": ["research"], "extra": True}, "grant", "policy-invalid-scope")
    with pytest.raises(AccountCenterError, match="不得覆盖 hash"):
        service.authorize_data(1, "quotes", {"pages": ["research"]}, "grant", "policy-client-hash", policy_sha256="0" * 64)

    service.authorize_data(1, "quotes", {"pages": ["research"]}, "grant", "policy-valid")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE account_data_policy_versions SET policy_json=? WHERE data_kind='quotes' AND policy_version=1",
            (json.dumps({**body, "title": "tampered"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
    assert service.authorization_status(1, "quotes")["policy_state"] == "compatibility_only"
    with pytest.raises(AccountCenterError, match="完整性校验失败"):
        service.authorize_data(1, "quotes", {"pages": ["research"]}, "grant", "policy-drift-retry")


def test_0042_adds_new_paper_columns_and_legacy_numbers_are_not_api_authority(tmp_path):
    db = DatabaseManager(str(tmp_path / "earnings-truth.db"))
    columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(earnings_postmortems)")}
    assert {
        "paper_performance_state", "paper_pnl_net_v2", "paper_max_drawdown_v2", "paper_ledger_snapshot_sha256",
    } <= columns

    journal_source = (ROOT / "core/earnings_forecast_journal.py").read_text(encoding="utf-8")
    read_model_source = (ROOT / "src/apps/api/earnings_read_model.py").read_text(encoding="utf-8")
    assert "# Legacy NOT NULL compatibility columns are not authoritative." in journal_source
    assert '"pnl_net": row["paper_pnl_net_v2"]' in read_model_source
    assert '"max_drawdown": row["paper_max_drawdown_v2"]' in read_model_source
    assert "# Legacy NOT NULL compatibility columns are not authoritative.\n                    0.0, 0.0," in journal_source


def test_telegram_is_active_and_revocation_are_both_required_for_account_truth(tmp_path):
    db = DatabaseManager(str(tmp_path / "telegram-truth.db"))
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES (?,?,?)", ("tg@example.com", "x", "2026-01-01"))
    service = AccountCenterService(db)

    db.execute(
        "INSERT INTO telegram_accounts(user_id,chat_id,is_active,revoked_at,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        (1, "10001", 0, None, "2026-01-01", "2026-01-01"),
    )
    assert service.account_overview(1)["telegram"]["state"] == "not_configured"
    db.execute("UPDATE telegram_accounts SET is_active=1,revoked_at=? WHERE user_id=1", ("2026-01-02",))
    assert service.account_overview(1)["telegram"]["state"] == "not_configured"
    db.execute("UPDATE telegram_accounts SET revoked_at=NULL WHERE user_id=1")
    assert service.account_overview(1)["telegram"]["state"] == "configured"
