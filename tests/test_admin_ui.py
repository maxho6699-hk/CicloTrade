from __future__ import annotations

from types import SimpleNamespace

import ui.pages.admin as admin
from core.database import DatabaseManager


class _SessionState(dict):
    def __getattr__(self, name: str) -> object:
        return self[name]

    def __setattr__(self, name: str, value: object) -> None:
        self[name] = value


def test_membership_intent_idempotency_key_reuses_key_for_repeated_submission(monkeypatch):
    state = _SessionState()
    monkeypatch.setattr(admin, "st", SimpleNamespace(session_state=state))

    payload = (7, 12, "标准版", 7, "新用户体验", "备注")
    first = admin._intent_idempotency_key("admin_membership_trial", *payload)

    assert 8 <= len(first) <= 128
    assert admin._intent_idempotency_key("admin_membership_trial", *payload) == first
    assert list(state["admin_idempotency_admin_membership_trial"].values()) == [first]
    admin._clear_intent_idempotency_key("admin_membership_trial", *payload)
    assert admin._intent_idempotency_key("admin_membership_trial", *payload) != first


def test_membership_intent_idempotency_key_rotates_for_changed_payload_or_target(monkeypatch):
    state = _SessionState()
    monkeypatch.setattr(admin, "st", SimpleNamespace(session_state=state))

    first = admin._intent_idempotency_key("admin_membership_trial", 7, 12, "标准版", 7, "原因", "备注")

    assert admin._intent_idempotency_key("admin_membership_trial", 8, 12, "标准版", 7, "原因", "备注") != first
    assert admin._intent_idempotency_key("admin_membership_trial", 7, 13, "标准版", 7, "原因", "备注") != first
    assert admin._intent_idempotency_key("admin_membership_trial", 7, 12, "高级版", 7, "原因", "备注") != first
    assert admin._intent_idempotency_key("admin_membership_trial", 7, 12, "标准版", 8, "原因", "备注") != first
    assert admin._intent_idempotency_key("admin_membership_trial", 7, 12, "标准版", 7, "新原因", "备注") != first
    assert admin._intent_idempotency_key("admin_membership_trial", 7, 12, "标准版", 7, "原因", "新备注") != first


def test_membership_intent_idempotency_key_reuses_pending_key_after_input_changes(monkeypatch):
    state = _SessionState()
    monkeypatch.setattr(admin, "st", SimpleNamespace(session_state=state))
    first_payload = (7, 12, "标准版", 7, "原因 A", "备注")
    second_payload = (7, 13, "高级版", 14, "原因 B", "新备注")
    first = admin._intent_idempotency_key("admin_membership_trial", *first_payload)

    second = admin._intent_idempotency_key("admin_membership_trial", *second_payload)

    assert second != first
    assert admin._intent_idempotency_key("admin_membership_trial", *first_payload) == first


def test_clear_membership_intent_idempotency_key_removes_only_completed_request(monkeypatch):
    state = _SessionState()
    monkeypatch.setattr(admin, "st", SimpleNamespace(session_state=state))
    first_payload = (7, 12, "标准版", 7, "原因 A", "备注")
    second_payload = (7, 13, "高级版", 14, "原因 B", "新备注")
    first = admin._intent_idempotency_key("admin_membership_trial", *first_payload)
    second = admin._intent_idempotency_key("admin_membership_trial", *second_payload)

    admin._clear_intent_idempotency_key("admin_membership_trial", *first_payload)

    assert admin._intent_idempotency_key("admin_membership_trial", *second_payload) == second
    assert admin._intent_idempotency_key("admin_membership_trial", *first_payload) != first


def test_run_action_clears_membership_key_after_success_or_business_rejection(monkeypatch):
    state = _SessionState()
    events: list[str] = []
    monkeypatch.setattr(
        admin,
        "st",
        SimpleNamespace(
            session_state=state,
            error=lambda *_args, **_kwargs: events.append("error"),
            rerun=lambda: events.append("rerun"),
        ),
    )

    admin._run_action(lambda: None, "done", lambda: events.append("clear-success"))
    admin._run_action(
        lambda: (_ for _ in ()).throw(ValueError("invalid")),
        "done",
        lambda: events.append("clear-failure"),
    )

    assert state.admin_flash == "done"
    assert events == ["clear-success", "rerun", "clear-failure", "error"]


def test_run_action_keeps_membership_key_after_unknown_failure(monkeypatch):
    state = _SessionState()
    events: list[str] = []
    monkeypatch.setattr(
        admin,
        "st",
        SimpleNamespace(
            session_state=state,
            error=lambda *_args, **_kwargs: events.append("error"),
        ),
    )
    monkeypatch.setattr(
        admin,
        "get_database",
        lambda: SimpleNamespace(log_system_event=lambda *_args: events.append("logged")),
    )
    payload = (7, 12, "标准版", 7, "原因", "备注")
    first = admin._intent_idempotency_key("admin_membership_trial", *payload)

    admin._run_action(
        lambda: (_ for _ in ()).throw(OSError("connection lost")),
        "done",
        lambda: admin._clear_intent_idempotency_key("admin_membership_trial", *payload),
    )

    assert admin._intent_idempotency_key("admin_membership_trial", *payload) == first
    assert events == ["logged", "error"]


def test_run_action_keeps_membership_key_after_runtime_error(monkeypatch):
    state = _SessionState()
    events: list[str] = []
    monkeypatch.setattr(
        admin,
        "st",
        SimpleNamespace(
            session_state=state,
            error=lambda *_args, **_kwargs: events.append("error"),
        ),
    )
    payload = (7, 12, "标准版", 7, "原因", "备注")
    first = admin._intent_idempotency_key("admin_membership_trial", *payload)

    admin._run_action(
        lambda: (_ for _ in ()).throw(RuntimeError("transaction state unknown")),
        "done",
        lambda: admin._clear_intent_idempotency_key("admin_membership_trial", *payload),
    )

    assert admin._intent_idempotency_key("admin_membership_trial", *payload) == first
    assert events == ["error"]


def test_admin_grant_picker_uses_only_published_grantable_public_plans(tmp_path):
    plans = admin._grantable_plan_options(DatabaseManager(str(tmp_path / "admin-policy.db")))

    assert plans == ["标准版", "高级版"]
    assert "专业版" not in plans and "定制版" not in plans
