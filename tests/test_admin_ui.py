from __future__ import annotations

from types import SimpleNamespace

import ui.pages.admin as admin


class _SessionState(dict):
    def __getattr__(self, name: str) -> object:
        return self[name]

    def __setattr__(self, name: str, value: object) -> None:
        self[name] = value


def test_membership_intent_idempotency_key_is_stable_until_cleared(monkeypatch):
    state = _SessionState()
    monkeypatch.setattr(admin, "st", SimpleNamespace(session_state=state))

    first = admin._intent_idempotency_key("admin_membership_trial")

    assert 8 <= len(first) <= 128
    assert admin._intent_idempotency_key("admin_membership_trial") == first
    admin._clear_intent_idempotency_key("admin_membership_trial")
    assert admin._intent_idempotency_key("admin_membership_trial") != first


def test_run_action_clears_membership_key_after_success_or_failure(monkeypatch):
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
