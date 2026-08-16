from __future__ import annotations

import asyncio
from datetime import datetime
import importlib
from types import SimpleNamespace

import pytest

from core.compat import UTC
from core.database import DatabaseManager
from core.entitlement_policy import seed_canonical_policy
from core.feature_catalog import FeatureCatalogConflict
from src.apps.api.earnings_forecasts import EarningsForecastApi
from src.apps.api.feature_catalog_adapter import FeatureCatalogAdapter


def _seed_policy(database: DatabaseManager) -> None:
    with database.transaction() as connection:
        seed_canonical_policy(connection)


def test_adapter_combines_authoritative_catalog_and_preferences(tmp_path) -> None:
    database = DatabaseManager(str(tmp_path / "adapter.db"))
    database.execute(
        """INSERT INTO users(
               id,email,password_hash,display_name,plan_type,subscription_expire,created_at
           ) VALUES (?,?,?,?,?,?,?)""",
        (
            9,
            "member@example.com",
            "hash",
            "Member",
            "标准版",
            "2099-08-13T00:00:00+00:00",
            "2026-08-13T00:00:00+00:00",
        ),
    )
    _seed_policy(database)
    adapter = FeatureCatalogAdapter(database)

    payload = adapter.read(user_id=9)
    assert payload["preferences"] == {"pinned": [], "recent": [], "version": 0}
    assert any(item["key"] == "stock-screener" and item["access"] == "open" for item in payload["items"])

    updated = adapter.update_preferences(
        user_id=9,
        payload={
            "expected_version": 0,
            "pinned": ["stock-screener", "risk-calculator", "price-alerts"],
            "recent": ["price-alerts"],
        },
    )
    assert updated["preferences"]["version"] == 1
    assert updated["preferences"]["pinned"][0] == "stock-screener"

    recent = adapter.record_recent(
        user_id=9,
        key="risk-calculator",
        expected_version=1,
    )
    assert recent["preferences"] == {
        "pinned": ["stock-screener", "risk-calculator", "price-alerts"],
        "recent": ["risk-calculator", "price-alerts"],
        "version": 2,
    }

    with pytest.raises(ValueError, match="available"):
        adapter.record_recent(
            user_id=9,
            key="option-live-automation",
            expected_version=2,
        )

    with pytest.raises(FeatureCatalogConflict):
        adapter.update_preferences(
            user_id=9,
            payload={"expected_version": 1, "pinned": [], "recent": []},
        )


def test_adapter_fails_closed_without_verified_readiness_and_never_persists_locked_pins(tmp_path) -> None:
    database = DatabaseManager(str(tmp_path / "unreviewed-policy.db"))
    database.execute(
        "INSERT INTO users(id,email,password_hash,display_name,plan_type,subscription_expire,created_at) VALUES (?,?,?,?,?,?,?)",
        (12, "advanced@example.com", "hash", "Advanced", "高级版", "2099-08-13T00:00:00+00:00", "2026-08-13T00:00:00+00:00"),
    )
    database.execute("DELETE FROM membership_entitlement_readiness_reviews")
    adapter = FeatureCatalogAdapter(database)

    payload = adapter.read(user_id=12)
    screener = next(item for item in payload["items"] if item["key"] == "stock-screener")
    assert screener["availability"] == "locked"
    assert screener["pin_allowed"] is False
    with pytest.raises(ValueError, match="available"):
        adapter.update_preferences(
            user_id=12,
            payload={"expected_version": 0, "pinned": ["stock-screener", "risk-calculator", "price-alerts"], "recent": []},
        )
    assert database.fetch_one("SELECT COUNT(*) count FROM user_settings WHERE user_id=12")["count"] == 0


def test_adapter_keeps_verified_legacy_reads_open_without_membership_upgrade(tmp_path) -> None:
    database = DatabaseManager(str(tmp_path / "legacy-policy.db"))
    database.execute(
        "INSERT INTO users(id,email,password_hash,display_name,plan_type,subscription_expire,created_at) VALUES (?,?,?,?,?,?,?)",
        (13, "legacy@example.com", "hash", "Legacy", "专业版", "2099-08-13T00:00:00+00:00", "2026-08-13T00:00:00+00:00"),
    )
    _seed_policy(database)
    payload = FeatureCatalogAdapter(database).read(user_id=13)
    reports = next(item for item in payload["items"] if item["key"] == "research-reports")
    assert reports["availability"] == "available"
    assert reports["access"] == "open"


def test_adapter_ignores_caller_plan_and_fails_closed_for_unknown_user(tmp_path) -> None:
    database = DatabaseManager(str(tmp_path / "authority.db"))
    database.execute(
        """INSERT INTO users(id,email,password_hash,display_name,plan_type,created_at)
           VALUES (?,?,?,?,?,?)""",
        (10, "free@example.com", "hash", "Free", "免费版", "2026-08-13T00:00:00+00:00"),
    )
    adapter = FeatureCatalogAdapter(database)

    payload = adapter.read(user_id=10)
    screener = next(item for item in payload["items"] if item["key"] == "stock-screener")
    assert screener["availability"] == "locked"
    assert screener["access"] == "upgrade"

    with pytest.raises(TypeError):
        adapter.read(user_id=10, plan="高级版")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="unavailable"):
        adapter.read(user_id=999)


@pytest.mark.parametrize("user_id", (999, 11))
def test_invalid_user_preference_mutations_have_no_side_effect(tmp_path, user_id) -> None:
    database = DatabaseManager(str(tmp_path / "mutation-authority.db"))
    database.execute(
        """INSERT INTO users(
               id,email,password_hash,display_name,plan_type,is_active,created_at
           ) VALUES (?,?,?,?,?,?,?)""",
        (11, "inactive@example.com", "hash", "Inactive", "高级版", 0, "2026-08-13T00:00:00+00:00"),
    )
    adapter = FeatureCatalogAdapter(database)

    with pytest.raises(ValueError, match="unavailable"):
        adapter.update_preferences(
            user_id=user_id,
            payload={"expected_version": 0, "pinned": [], "recent": []},
        )
    with pytest.raises(ValueError, match="unavailable"):
        adapter.record_recent(
            user_id=user_id,
            key="risk-calculator",
            expected_version=0,
        )

    assert database.fetch_one(
        "SELECT COUNT(*) count FROM user_settings WHERE user_id=?", (user_id,)
    )["count"] == 0


def test_runtime_projection_uses_real_option_probe_and_earnings_read_model(monkeypatch) -> None:
    module = importlib.import_module("src.apps.api.app")
    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)

    class ReadModel:
        def __init__(self, data_state: str = "ready", *, broken: bool = False):
            self.data_state = data_state
            self.broken = broken

        def overview(self, **kwargs):
            assert kwargs["has_capability"] is True
            assert kwargs["window_days"] == 7
            assert kwargs["limit"] == 1
            if self.broken:
                raise RuntimeError("private failure")
            return {"state": "research", "data_state": self.data_state, "items": []}

    earnings_api = EarningsForecastApi(
        ReadModel("no_data"),
        authenticate=lambda request: None,
        has_capability=lambda identity, capability: True,
        clock=lambda: now,
    )
    monkeypatch.setattr(module, "_expanded_research_read_model", lambda request: None)
    monkeypatch.setattr(
        module,
        "_upstream_market_status",
        lambda: {
            "connected": True,
            "configuration_allows_realtime": True,
            "option_realtime_entitled": True,
        },
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(earnings_forecast_api=earnings_api))
    )
    runtime = asyncio.run(
        module._feature_catalog_runtime(request, SimpleNamespace(id=9))
    )

    assert runtime["option-lab"]["data_state"] == "ready"
    assert runtime["option-lab"]["health"] == "healthy"
    assert runtime["earnings-forecast"]["data_state"] == "ready"
    assert runtime["earnings-forecast"]["health"] == "healthy"
    assert "未来 7 日暂无已确认事件" in runtime["earnings-forecast"]["reason"]

    degraded = module._option_lab_runtime_status(
        {"connected": True, "configuration_allows_realtime": False}, now.isoformat()
    )
    assert degraded["data_state"] == "delayed"
    assert degraded["health"] == "degraded"
    missing = module._earnings_forecast_runtime_status(
        EarningsForecastApi(
            ReadModel(broken=True),
            authenticate=lambda request: None,
            has_capability=lambda identity, capability: True,
            clock=lambda: now,
        ),
        now.isoformat(),
    )
    assert missing["data_state"] == "missing"
    assert missing["health"] == "unavailable"
