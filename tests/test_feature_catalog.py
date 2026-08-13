from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.compat import UTC

from core.database import DatabaseManager
from core.feature_catalog import (
    FeatureCatalogConflict,
    FeatureCatalogValidationError,
    FeaturePreferenceStore,
    resolve_feature_catalog,
    secondary_nav_features,
)


def _database(tmp_path) -> DatabaseManager:
    database = DatabaseManager(str(tmp_path / "feature-catalog.db"))
    for user_id, email in ((1, "one@example.com"), (2, "two@example.com")):
        database.execute(
            """INSERT INTO users(id,email,password_hash,display_name,plan_type,created_at)
               VALUES (?,?,?,?,?,?)""",
            (user_id, email, "hash", email, "免费版", "2026-08-13T00:00:00+00:00"),
        )
    return database


def test_catalog_resolves_access_without_hiding_safety_or_selling_planned() -> None:
    now = datetime(2026, 8, 13, 0, 1, tzinfo=UTC)
    payload = resolve_feature_catalog(
        "免费版",
        runtime={
            "market-heatmap": {
                "data_state": "stale", "health": "degraded", "verified_at": "2026-08-13T00:00:00+00:00",
                "reason": "行情更新时间超过门限",
            },
        },
        now=now,
    )
    by_key = {item["key"]: item for item in payload["items"]}

    assert payload["catalog_version"]
    assert by_key["risk-calculator"]["availability"] == "available"
    assert by_key["risk-calculator"]["access"] == "open"
    assert by_key["data-status"]["availability"] == "available"
    assert {item["route"] for item in payload["items"] if item["primary_nav"]} == {
        "/today", "/discover", "/research", "/paper", "/portfolio", "/more",
    }
    assert by_key["research"]["primary_nav"] is True
    assert by_key["research"]["pin_allowed"] is False
    assert by_key["research"]["availability"] == "available"
    assert by_key["risk-calculator"]["data_state"] == "not_applicable"
    assert by_key["option-live-automation"]["availability"] == "planned"
    assert by_key["option-live-automation"]["pin_allowed"] is False
    assert by_key["stock-screener"]["availability"] == "locked"
    assert by_key["stock-screener"]["access"] == "upgrade"
    assert by_key["market-heatmap"]["availability"] == "degraded"
    assert by_key["market-heatmap"]["reason"] == "行情更新时间超过门限"
    assert by_key["market-heatmap"]["health"] == "degraded"
    assert "secondary_nav" in by_key["market-heatmap"]["placements"]
    assert by_key["stock-screener"]["actions"] == {"research_url": "/discover?tool=screener"}


def test_runtime_fails_closed_without_freshness_and_health_evidence() -> None:
    payload = resolve_feature_catalog("标准版", runtime={
        "market-heatmap": {"data_state": "ready", "health": "healthy"},
    })
    item = next(item for item in payload["items"] if item["key"] == "market-heatmap")
    assert item["availability"] == "unavailable"
    assert item["data_state"] == "missing"
    assert item["health"] == "unavailable"


@pytest.mark.parametrize("verified_at", ["garbage", "2026-08-13T00:00:00", None])
def test_runtime_rejects_invalid_or_naive_evidence_time(verified_at) -> None:
    payload = resolve_feature_catalog("标准版", runtime={
        "market-heatmap": {"data_state": "ready", "health": "healthy", "verified_at": verified_at},
    }, now=datetime(2026, 8, 13, 0, 1, tzinfo=UTC))
    item = next(item for item in payload["items"] if item["key"] == "market-heatmap")
    assert item["availability"] == "unavailable"
    assert item["access"] == "retry"


def test_runtime_derives_availability_and_rejects_stale_or_future_evidence() -> None:
    now = datetime(2026, 8, 13, 0, 10, tzinfo=UTC)
    cases = (
        ({"availability": "available", "data_state": "stale", "health": "degraded", "verified_at": (now - timedelta(minutes=1)).isoformat()}, "degraded"),
        ({"availability": "available", "data_state": "ready", "health": "healthy", "verified_at": (now - timedelta(minutes=6)).isoformat()}, "unavailable"),
        ({"availability": "available", "data_state": "ready", "health": "healthy", "verified_at": (now + timedelta(minutes=1)).isoformat()}, "unavailable"),
    )
    for evidence, expected in cases:
        payload = resolve_feature_catalog("标准版", runtime={"market-heatmap": evidence}, now=now)
        item = next(item for item in payload["items"] if item["key"] == "market-heatmap")
        assert item["availability"] == expected

    with pytest.raises(FeatureCatalogValidationError, match="timezone"):
        resolve_feature_catalog("标准版", now=datetime(2026, 8, 13, 0, 10))


def test_catalog_actions_are_drafts_only() -> None:
    payload = resolve_feature_catalog("标准版")
    actions = next(item["actions"] for item in payload["items"] if item["key"] == "risk-calculator")
    assert actions == {"paper_prefill": {"market": "US", "side": "BUY"}}
    assert not {"quote_id", "account_version", "idempotency_key", "auto_submit"} & set(actions["paper_prefill"])


def test_secondary_pins_are_desktop_only_and_cannot_repeat_primary_navigation(tmp_path) -> None:
    store = FeaturePreferenceStore(_database(tmp_path))
    preferences = store.replace(
        1, expected_version=0,
        pinned=["stock-screener", "market-heatmap", "risk-calculator"], recent=[],
    )
    catalog = resolve_feature_catalog("标准版", runtime={
        "market-heatmap": {
            "data_state": "ready", "health": "healthy", "verified_at": "2026-08-13T00:00:00+00:00",
        },
    }, now=datetime(2026, 8, 13, 0, 1, tzinfo=UTC))
    catalog["preferences"] = preferences
    assert secondary_nav_features(catalog, viewport_width=1024) == []
    desktop = secondary_nav_features(catalog, viewport_width=1025)
    assert [item["key"] for item in desktop] == ["stock-screener", "market-heatmap", "risk-calculator"]
    assert all(item["route"] not in {"/today", "/discover", "/research", "/paper", "/portfolio", "/more"} for item in desktop)


def test_preferences_are_cas_protected_cleaned_and_isolated(tmp_path) -> None:
    store = FeaturePreferenceStore(_database(tmp_path))

    initial = store.load(1)
    assert initial == {"pinned": [], "recent": [], "version": 0}
    saved = store.replace(
        1,
        expected_version=0,
        pinned=["stock-screener", "risk-calculator", "market-heatmap"],
        recent=["unknown", "stock-screener", "stock-screener", "risk-calculator"],
    )
    assert saved == {
        "pinned": ["stock-screener", "risk-calculator", "market-heatmap"],
        "recent": ["stock-screener", "risk-calculator"],
        "version": 1,
    }
    assert store.load(2) == {"pinned": [], "recent": [], "version": 0}

    with pytest.raises(FeatureCatalogConflict):
        store.replace(1, expected_version=0, pinned=[], recent=[])


def test_recent_updates_are_atomic_cas_mutations(tmp_path) -> None:
    store = FeaturePreferenceStore(_database(tmp_path))
    saved = store.replace(
        1, expected_version=0,
        pinned=["stock-screener", "risk-calculator", "market-heatmap"], recent=["option-lab"],
    )
    updated = store.record_recent(1, expected_version=saved["version"], key="stock-screener")
    assert updated == {
        "pinned": ["stock-screener", "risk-calculator", "market-heatmap"],
        "recent": ["stock-screener", "option-lab"],
        "version": 2,
    }
    with pytest.raises(FeatureCatalogConflict):
        store.record_recent(1, expected_version=1, key="market-heatmap")
    with pytest.raises(FeatureCatalogValidationError, match="unknown"):
        store.record_recent(1, expected_version=2, key="not-a-feature")


def test_preference_validation_enforces_pin_and_recent_contract(tmp_path) -> None:
    store = FeaturePreferenceStore(_database(tmp_path))

    with pytest.raises(FeatureCatalogValidationError, match="zero or between three and five"):
        store.replace(1, expected_version=0, pinned=["stock-screener"], recent=[])
    with pytest.raises(FeatureCatalogValidationError, match="cannot be pinned"):
        store.replace(
            1,
            expected_version=0,
            pinned=["risk-calculator", "data-status", "option-live-automation"],
            recent=[],
        )
    with pytest.raises(FeatureCatalogValidationError, match="cannot be pinned"):
        store.replace(
            1,
            expected_version=0,
            pinned=["risk-calculator", "data-status", "personal-paper"],
            recent=[],
        )

    saved = store.replace(
        1,
        expected_version=0,
        pinned=[],
        recent=[
            "risk-calculator",
            "data-status",
            "stock-screener",
            "market-heatmap",
            "price-alerts",
            "option-lab",
            "earnings-forecast",
            "portfolio-review",
            "feedback",
        ],
    )
    assert len(saved["recent"]) == 8
