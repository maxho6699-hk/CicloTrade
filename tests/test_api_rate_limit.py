"""Per-user API quotas must match the plan limits and reset by window."""

from __future__ import annotations

import pytest

import asgi_app
from core.database import DatabaseManager


def test_api_quota_blocks_only_after_plan_limit(tmp_path, monkeypatch):
    database = DatabaseManager(str(tmp_path / "api-rate.db"))
    monkeypatch.setattr(asgi_app, "get_database", lambda: database)
    user = {"id": 42, "plan_type": "专业版", "subscription_expire": "2099-01-01T00:00:00+00:00"}

    for _ in range(100):
        asgi_app._consume_api_quota(user)

    with pytest.raises(asgi_app.ApiError, match="每分钟上限") as error:
        asgi_app._consume_api_quota(user)
    assert error.value.status == 429

    key = asgi_app.AuthService._rate_key("api-user", "42", "*")
    database.execute(
        "UPDATE auth_rate_limits SET window_started='2000-01-01T00:00:00+00:00' WHERE rate_key=?",
        (key,),
    )
    asgi_app._consume_api_quota(user)
