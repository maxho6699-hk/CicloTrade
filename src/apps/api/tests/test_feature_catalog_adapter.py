from __future__ import annotations

import pytest

from core.database import DatabaseManager
from core.feature_catalog import FeatureCatalogConflict
from src.apps.api.feature_catalog_adapter import FeatureCatalogAdapter


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
    adapter = FeatureCatalogAdapter(database)

    payload = adapter.read(user_id=9)
    assert payload["preferences"] == {"pinned": [], "recent": [], "version": 0}
    assert any(item["key"] == "stock-screener" and item["access"] == "open" for item in payload["items"])

    updated = adapter.update_preferences(
        user_id=9,
        payload={
            "expected_version": 0,
            "pinned": ["stock-screener", "risk-calculator", "market-heatmap"],
            "recent": ["market-heatmap"],
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
        "pinned": ["stock-screener", "risk-calculator", "market-heatmap"],
        "recent": ["risk-calculator", "market-heatmap"],
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
