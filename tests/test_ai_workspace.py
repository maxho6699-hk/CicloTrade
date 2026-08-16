from __future__ import annotations

import hashlib
import json

import pytest

from core.ai_workspace import (
    AIWorkspaceIdempotencyConflict,
    AIWorkspaceNotFound,
    AIWorkspaceService,
)
from core.auth import AuthService
from core.database import DatabaseManager


def _db(tmp_path):
    return DatabaseManager(str(tmp_path / "ai-workspace.db"))


def _owner(db, email: str) -> int:
    user = AuthService(db).register(email, "CorrectHorse123", email.split("@")[0], True)
    return int(user["id"])


def _config(monkeypatch):
    monkeypatch.setenv("CICLO_AI_ENABLED", "1")
    monkeypatch.setenv("CICLO_AI_ENDPOINT", "https://provider.example.test/v1/respond")
    monkeypatch.setenv("CICLO_AI_MODEL", "ciclo-test")
    monkeypatch.setenv("CICLO_AI_PROVIDER_VERSION", "provider-v1")
    monkeypatch.setenv("CICLO_AI_CONTRACT_VERSION", "contract-v1")


def _citation(service: AIWorkspaceService, owner: int):
    return service.create_context_snapshot(
        owner,
        {
            "citations": [
                {
                    "citation_id": "cit_aapl_1",
                    "source_kind": "quote",
                    "source_public_id": "quote_aapl_1",
                    "source_version": 1,
                    "title": "AAPL 行情",
                    "observed_at": "2026-08-16T01:00:00+00:00",
                    "available_at": "2026-08-16T01:00:01+00:00",
                    "quote_at": "2026-08-16T01:00:00+00:00",
                }
            ]
        },
    )


def test_readiness_is_fail_closed_when_any_fixed_config_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CICLO_AI_ENABLED", raising=False)
    for name in (
        "CICLO_AI_ENDPOINT",
        "CICLO_AI_MODEL",
        "CICLO_AI_PROVIDER_VERSION",
        "CICLO_AI_CONTRACT_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)
    result = AIWorkspaceService(_db(tmp_path)).readiness()
    assert result["ready"] is False
    assert result["status"] == "unavailable"
    assert set(result["missing"]) == {
        "CICLO_AI_ENABLED",
        "CICLO_AI_ENDPOINT",
        "CICLO_AI_MODEL",
        "CICLO_AI_PROVIDER_VERSION",
        "CICLO_AI_CONTRACT_VERSION",
    }


def test_sessions_and_archive_are_owner_scoped_and_idempotent(tmp_path, monkeypatch):
    _config(monkeypatch)
    db = _db(tmp_path)
    first, second = _owner(db, "first@example.com"), _owner(db, "second@example.com")
    service = AIWorkspaceService(db)
    created = service.create_session(first, title="AAPL 研究", idempotency_key="session-first-1")
    replay = service.create_session(first, title="AAPL 研究", idempotency_key="session-first-1")
    assert replay["public_id"] == created["public_id"]
    assert service.list_sessions(first)[0]["public_id"] == created["public_id"]
    with pytest.raises(AIWorkspaceNotFound):
        service.get_session(second, created["public_id"])
    archived = service.archive_session(first, created["public_id"], "archive-first-1")
    assert archived["status"] == "archived"
    assert service.list_sessions(first)[0]["status"] == "archived"


def test_context_loader_receives_only_selectors_and_service_mints_citations(tmp_path, monkeypatch):
    _config(monkeypatch)
    db = _db(tmp_path)
    owner = _owner(db, "loader@example.com")
    seen = {}

    def loader(user_id, selectors):
        seen["owner"] = user_id
        seen["selectors"] = selectors
        return {
            "page_context": {
                "route": "/research",
                "market": "US",
                "symbol": "AAPL",
                "timeframe": "1d",
                "account_domain": "research",
            },
            "citations": [{"source_kind": "quote", "source_public_id": "q1", "source_version": 1, "title": "服务器行情"}],
        }

    service = AIWorkspaceService(db, context_loader=loader)
    created = service.create_session(owner, title="AAPL", selectors={"route": "/research", "symbol": "AAPL"}, idempotency_key="loader-session-1")
    assert seen == {"owner": owner, "selectors": {"route": "/research", "symbol": "AAPL"}}
    snapshot_id = created["context_snapshot_public_id"]
    snapshot = db.fetch_one("SELECT context_json FROM ai_workspace_context_snapshots WHERE public_id=?", (snapshot_id,))
    citation_id = db.fetch_one("SELECT public_id FROM ai_workspace_citations WHERE snapshot_public_id=?", (snapshot_id,))["public_id"]
    assert citation_id.startswith("cit_") and len(citation_id) >= 16
    assert citation_id in snapshot["context_json"]
    assert json.loads(snapshot["context_json"])["page_context"]["symbol"] == "AAPL"
    context = service._context_for_session(owner, {"context_snapshot_public_id": snapshot_id})
    assert context["page_context"] == {
        "route": "/research",
        "market": "US",
        "symbol": "AAPL",
        "timeframe": "1d",
        "account_domain": "research",
    }


def test_ai_memory_uses_only_the_latest_authorization_receipt(tmp_path, monkeypatch):
    _config(monkeypatch)
    db = _db(tmp_path)
    owner = _owner(db, "memory@example.com")
    db.execute(
        """INSERT INTO account_memory_entries
           (public_id,owner_id,memory_key,memory_json,expires_at,idempotency_key,request_sha256,created_at)
           VALUES ('mem_public_00000001',?,'format','{\"preference\":\"先列风险\"}',NULL,'memory-key-001',?,'2026-08-16T01:00:00+00:00')""",
        (owner, "a" * 64),
    )
    policy = db.fetch_one(
        """SELECT policy_version,policy_sha256 FROM account_data_policy_versions
           WHERE policy_type='account_data' AND data_kind='ai_memory' AND status='published'
           ORDER BY policy_version DESC LIMIT 1"""
    )
    assert policy is not None
    receipt = """INSERT INTO account_data_authorization_receipts
      (public_id,owner_id,data_kind,policy_type,policy_version,policy_sha256,scope_json,scopes_json,action,request_identity,idempotency_key,request_sha256,created_at)
      VALUES (?,?,'ai_memory','account_data',1,?,'{\"pages\":[\"ai\"]}','{\"pages\":[\"ai\"]}',?,?,?,?,?)"""
    db.execute(
        receipt,
        ("auth_public_grant_001", owner, policy["policy_sha256"], "granted", "request-grant", "auth-key-grant", "c" * 64, "2026-08-16T01:01:00+00:00"),
    )
    service = AIWorkspaceService(db)
    assert service._memory_context(owner)[0]["memory_key"] == "format"
    db.execute(
        receipt,
        ("auth_public_revoke_01", owner, policy["policy_sha256"], "revoked", "request-revoke", "auth-key-revoke", "d" * 64, "2026-08-16T01:02:00+00:00"),
    )
    assert service._memory_context(owner) == []


def test_ai_memory_grant_expires_on_policy_upgrade_retirement_or_integrity_mismatch(tmp_path, monkeypatch):
    _config(monkeypatch)
    db = _db(tmp_path)
    owner = _owner(db, "memory-policy@example.com")
    db.execute(
        """INSERT INTO account_memory_entries
           (public_id,owner_id,memory_key,memory_json,expires_at,idempotency_key,request_sha256,created_at)
           VALUES ('mem_policy_0000001',?,'format','{\"preference\":\"先列风险\"}',NULL,'memory-policy-001',?,'2026-08-16T01:00:00+00:00')""",
        (owner, "a" * 64),
    )
    policy = db.fetch_one(
        """SELECT policy_version,policy_json,policy_sha256 FROM account_data_policy_versions
           WHERE policy_type='account_data' AND data_kind='ai_memory' AND status='published'
           ORDER BY policy_version DESC LIMIT 1"""
    )
    assert policy is not None
    db.execute(
        """INSERT INTO account_data_authorization_receipts
           (public_id,owner_id,data_kind,policy_type,policy_version,policy_sha256,scope_json,scopes_json,action,request_identity,idempotency_key,request_sha256,created_at)
           VALUES ('auth_policy_grant',?,'ai_memory','account_data',?,?, '{\"pages\":[\"ai\"]}','{\"pages\":[\"ai\"]}','granted','request-policy','auth-policy-001',?,'2026-08-16T01:01:00+00:00')""",
        (owner, policy["policy_version"], policy["policy_sha256"], "c" * 64),
    )
    service = AIWorkspaceService(db)
    assert service._memory_context(owner)[0]["memory_key"] == "format"

    policy_body = json.loads(policy["policy_json"])
    policy_body["title"] = "AI 可控记忆授权 v2"
    encoded = json.dumps(policy_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    upgraded_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    db.execute(
        """INSERT INTO account_data_policy_versions
           (policy_type,data_kind,policy_version,policy_json,policy_sha256,status,published_at)
           VALUES ('account_data','ai_memory',2,?,?, 'published','2026-08-16T02:00:00+00:00')""",
        (encoded, upgraded_hash),
    )
    assert service._memory_context(owner) == []

    db.execute("UPDATE account_data_policy_versions SET status='retired' WHERE data_kind='ai_memory' AND policy_version=2")
    assert service._memory_context(owner) == []

    db.execute(
        "UPDATE account_data_policy_versions SET status='published',policy_json=? WHERE data_kind='ai_memory' AND policy_version=2",
        (json.dumps({"tampered": True}, separators=(",", ":")),),
    )
    assert service._memory_context(owner) == []


def test_ai_memory_rejects_receipt_hash_or_version_mismatch(tmp_path, monkeypatch):
    _config(monkeypatch)
    db = _db(tmp_path)
    owner = _owner(db, "memory-mismatch@example.com")
    db.execute(
        """INSERT INTO account_memory_entries
           (public_id,owner_id,memory_key,memory_json,expires_at,idempotency_key,request_sha256,created_at)
           VALUES ('mem_mismatch_0001',?,'format','{\"preference\":\"先列风险\"}',NULL,'memory-mismatch-1',?,'2026-08-16T01:00:00+00:00')""",
        (owner, "a" * 64),
    )
    policy = db.fetch_one(
        """SELECT policy_version FROM account_data_policy_versions
           WHERE policy_type='account_data' AND data_kind='ai_memory' AND status='published'
           ORDER BY policy_version DESC LIMIT 1"""
    )
    assert policy is not None
    db.execute(
        """INSERT INTO account_data_authorization_receipts
           (public_id,owner_id,data_kind,policy_type,policy_version,policy_sha256,scope_json,scopes_json,action,request_identity,idempotency_key,request_sha256,created_at)
           VALUES ('auth_mismatch_hash',?,'ai_memory','account_data',?,?, '{\"pages\":[\"ai\"]}','{\"pages\":[\"ai\"]}','granted','request-mismatch','auth-mismatch-1',?,'2026-08-16T01:01:00+00:00')""",
        (owner, policy["policy_version"], "f" * 64, "c" * 64),
    )
    service = AIWorkspaceService(db)
    assert service._memory_context(owner) == []


def test_provider_unavailable_persists_public_blocked_task_without_assistant(tmp_path, monkeypatch):
    monkeypatch.delenv("CICLO_AI_ENDPOINT", raising=False)
    db = _db(tmp_path)
    owner = _owner(db, "blocked@example.com")
    service = AIWorkspaceService(db)
    session = service.create_session(owner, title="研究", idempotency_key="session-blocked-1")
    result = service.submit_message(owner, session["public_id"], "请分析 AAPL", "message-blocked-1")
    assert result["blocked"] is True
    assert result["task"]["status"] == "blocked"
    assert result["assistant"] is None
    assert service.get_task(owner, result["task"]["public_id"])["status"] == "blocked"
    messages = service.get_session(owner, session["public_id"])["messages"]
    assert [message["role"] for message in messages] == ["user"]
    events = service.list_task_events(owner, result["task"]["public_id"])
    assert [event["status"] for event in events] == ["queued", "blocked"]


def test_provider_output_requires_allowlisted_citations_and_fixed_versions(tmp_path, monkeypatch):
    _config(monkeypatch)
    db = _db(tmp_path)
    owner = _owner(db, "provider@example.com")
    service = AIWorkspaceService(db)
    snapshot = _citation(service, owner)
    citation_id = snapshot["citation_ids"][0]
    service.provider = lambda _: {
        "provider_version": "provider-v1",
        "contract_version": "contract-v1",
        "conclusion": "需要先核验数据新鲜度。",
        "citations": [citation_id],
        "support": ["价格来源可追溯。"],
        "counter": ["尚无完整财报证据。"],
        "risks": ["行情可能延迟。"],
        "next_steps": ["打开研究页继续核验。"],
    }
    session = service.create_session(
        owner,
        title="AAPL 研究",
        context_snapshot_public_id=snapshot["public_id"],
        idempotency_key="session-provider-1",
    )
    result = service.submit_message(owner, session["public_id"], "请分析 AAPL", "message-provider-1")
    assert result["task"]["status"] == "succeeded"
    assert result["assistant"]["structured"]["conclusion"]["text"] == "需要先核验数据新鲜度。"
    assert result["assistant"]["structured"]["conclusion"]["citation_ids"] == [citation_id]
    assert len(service.get_session(owner, session["public_id"])["messages"]) == 2

    bad = AIWorkspaceService(
        db,
        provider=lambda _: {
            "provider_version": "provider-v1",
            "contract_version": "contract-v1",
            "conclusion": "不可验证",
            "citations": ["cit_not_allowlisted"],
            "support": [],
            "counter": [],
            "risks": [],
            "next_steps": [],
        },
    )
    second = bad.create_session(owner, title="bad", idempotency_key="session-provider-2")
    failed = bad.submit_message(owner, second["public_id"], "继续", "message-provider-2")
    assert failed["task"]["status"] == "failed"
    assert failed["assistant"] is None


def test_cancel_task_is_idempotent_and_request_fingerprint_is_bound(tmp_path, monkeypatch):
    _config(monkeypatch)
    db = _db(tmp_path)
    owner = _owner(db, "cancel@example.com")
    service = AIWorkspaceService(db, provider=lambda _: (_ for _ in ()).throw(TimeoutError("slow")))
    session = service.create_session(owner, title="研究", idempotency_key="session-cancel-1")
    result = service.submit_message(owner, session["public_id"], "分析", "message-cancel-1")
    task_id = result["task"]["public_id"]
    cancelled = service.cancel_task(owner, task_id, "cancel-task-1")
    assert cancelled["status"] == "timed_out"
    assert service.cancel_task(owner, task_id, "cancel-task-1")["status"] == "timed_out"
    with pytest.raises(AIWorkspaceIdempotencyConflict):
        service.submit_message(owner, session["public_id"], "不同请求", "message-cancel-1")
