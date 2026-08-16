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
from core.plans import CAPABILITIES, PLAN_ORDER


def catalog(plan, runtime=None, *, now=None, capabilities=None):
    inherited_capabilities = set().union(
        *(CAPABILITIES[level] for level in PLAN_ORDER[:PLAN_ORDER.index(plan) + 1]),
    )
    return resolve_feature_catalog(
        plan, runtime, now=now,
        capabilities=inherited_capabilities if capabilities is None else capabilities,
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
    payload = catalog(
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
    assert by_key["data-status"]["title_key"] == "feature.account_center.title"
    assert by_key["data-status"]["description_key"] == "feature.account_center.description"
    assert sum(item["route"] == "/account" for item in payload["items"]) == 1
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
    assert by_key["market-heatmap"]["availability"] == "planned"
    assert by_key["market-heatmap"]["access"] == "wait"
    assert by_key["market-heatmap"]["pin_allowed"] is False
    assert by_key["earnings-calendar"]["availability"] == "planned"
    assert by_key["earnings-calendar"]["pin_allowed"] is False
    assert "secondary_nav" in by_key["market-heatmap"]["placements"]
    assert by_key["stock-screener"]["actions"] == {"research_url": "/discover?tool=screener"}


def test_runtime_fails_closed_without_freshness_and_health_evidence() -> None:
    payload = catalog("标准版", runtime={
        "market-heatmap": {"data_state": "ready", "health": "healthy"},
    })
    item = next(item for item in payload["items"] if item["key"] == "market-heatmap")
    assert item["availability"] == "planned"
    assert item["access"] == "wait"


@pytest.mark.parametrize("verified_at", ["garbage", "2026-08-13T00:00:00", None])
def test_runtime_rejects_invalid_or_naive_evidence_time(verified_at) -> None:
    payload = catalog("标准版", runtime={
        "market-heatmap": {"data_state": "ready", "health": "healthy", "verified_at": verified_at},
    }, now=datetime(2026, 8, 13, 0, 1, tzinfo=UTC))
    item = next(item for item in payload["items"] if item["key"] == "market-heatmap")
    assert item["availability"] == "planned"
    assert item["access"] == "wait"


def test_runtime_derives_availability_and_rejects_stale_or_future_evidence() -> None:
    now = datetime(2026, 8, 13, 0, 10, tzinfo=UTC)
    cases = (
        ({"availability": "available", "data_state": "stale", "health": "degraded", "verified_at": (now - timedelta(minutes=1)).isoformat()}, "degraded"),
        ({"availability": "available", "data_state": "ready", "health": "healthy", "verified_at": (now - timedelta(minutes=6)).isoformat()}, "unavailable"),
        ({"availability": "available", "data_state": "ready", "health": "healthy", "verified_at": (now + timedelta(minutes=1)).isoformat()}, "unavailable"),
    )
    for evidence, expected in cases:
        payload = catalog("标准版", runtime={"market-heatmap": evidence}, now=now)
        item = next(item for item in payload["items"] if item["key"] == "market-heatmap")
        assert item["availability"] == "planned"

    with pytest.raises(FeatureCatalogValidationError, match="timezone"):
        catalog("标准版", now=datetime(2026, 8, 13, 0, 10))


def test_required_runtime_features_cover_ready_degraded_expired_and_missing() -> None:
    now = datetime(2026, 8, 13, 0, 10, tzinfo=UTC)
    capabilities = set(CAPABILITIES["专业版"])
    cases = (
        ({"data_state": "ready", "health": "healthy", "verified_at": now.isoformat()}, "available"),
        ({"data_state": "delayed", "health": "degraded", "verified_at": now.isoformat(), "reason": "上游延迟"}, "degraded"),
        ({"data_state": "ready", "health": "healthy", "verified_at": (now - timedelta(minutes=6)).isoformat()}, "unavailable"),
        ({"data_state": "missing", "health": "unavailable", "verified_at": now.isoformat(), "reason": "缺少运行证明"}, "unavailable"),
    )
    for evidence, expected in cases:
        payload = resolve_feature_catalog(
            "专业版",
            {"option-lab": evidence},
            capabilities=capabilities,
            now=now,
        )
        option_lab = next(item for item in payload["items"] if item["key"] == "option-lab")
        assert option_lab["availability"] == expected


def test_catalog_actions_are_drafts_only() -> None:
    payload = catalog("标准版")
    actions = next(item["actions"] for item in payload["items"] if item["key"] == "risk-calculator")
    assert actions == {"paper_prefill": {"market": "US", "side": "BUY"}}
    assert not {"quote_id", "account_version", "idempotency_key", "auto_submit"} & set(actions["paper_prefill"])


def test_feature_routes_use_real_hosts_and_preview_entries_are_not_actionable() -> None:
    payload = catalog("高级版")
    by_key = {item["key"]: item for item in payload["items"]}
    assert by_key["price-alerts"]["route"] == "/research?panel=预警"
    assert by_key["option-lab"]["route"] == "/lab"
    assert by_key["risk-calculator"]["route"] == "/paper"
    assert by_key["data-status"]["route"] == "/account"
    assert by_key["data-status"]["title_key"] == "feature.account_center.title"
    assert by_key["workflow-tasks"]["route"] == "/workflow"
    assert by_key["notifications"]["route"] == "/notifications"
    assert by_key["trade-control"]["route"] == "/trade"
    assert by_key["membership"]["route"] == "/membership"
    assert by_key["promotion"]["route"] == "/promotion"
    assert by_key["help"]["route"] == "/help"
    assert by_key["legal"]["route"] == "/legal"
    assert "admin" not in by_key
    for key in ("market-heatmap", "earnings-calendar"):
        assert by_key[key]["availability"] == "planned"
        assert by_key[key]["access"] == "wait"
        assert by_key[key]["pin_allowed"] is False
        assert by_key[key]["actions"] == {}


def test_admin_catalog_entry_is_visible_only_to_super_admin_projection() -> None:
    regular = resolve_feature_catalog("高级版", capabilities=set(), is_super_admin=False)
    elevated = resolve_feature_catalog("高级版", capabilities=set(), is_super_admin=True)
    assert all(item["key"] != "admin" for item in regular["items"])
    admin = next(item for item in elevated["items"] if item["key"] == "admin")
    assert admin["route"] == "/admin"
    assert admin["pin_allowed"] is False
    assert admin["availability"] == "available"


def test_legacy_only_capabilities_wait_without_upgrade_cta() -> None:
    payload = catalog("高级版")
    by_key = {item["key"]: item for item in payload["items"]}
    for key in ("option-lab", "earnings-forecast", "research-reports"):
        assert by_key[key]["availability"] == "locked"
        assert by_key[key]["access"] == "wait"
        assert "sales_unavailable" in by_key[key]["reason"]


def test_verified_legacy_capabilities_keep_read_routes_while_missing_receipts_fail_closed() -> None:
    verified = set(CAPABILITIES["专业版"])
    legacy = catalog("专业版", capabilities=verified)
    reports = next(item for item in legacy["items"] if item["key"] == "research-reports")
    assert reports["availability"] == "available"
    assert reports["access"] == "open"

    unverified = resolve_feature_catalog("高级版", capabilities=set())
    screener = next(item for item in unverified["items"] if item["key"] == "stock-screener")
    assert screener["availability"] == "locked"
    assert screener["pin_allowed"] is False


def test_strategy_research_is_pinable_and_non_executable() -> None:
    now = datetime(2026, 8, 13, 0, 1, tzinfo=UTC)
    payload = catalog("标准版", runtime={
        "strategy-research": {
            "data_state": "ready",
            "health": "healthy",
            "verified_at": now.isoformat(),
        },
    }, now=now, capabilities={"expanded_research_full"})
    item = next(item for item in payload["items"] if item["key"] == "strategy-research")

    assert item["route"] == "/reports?view=影子策略研究&research_scope=expanded"
    assert item["availability"] == "available"
    assert item["pin_allowed"] is True
    assert item["actions"] == {"research_url": item["route"]}
    assert not {"submit", "execute", "order"} & set(item["actions"])

    unavailable = next(item for item in catalog("标准版", capabilities={"expanded_research_full"})["items"] if item["key"] == "strategy-research")
    assert unavailable["availability"] == "unavailable"
    assert unavailable["pin_allowed"] is False


def test_secondary_pins_are_desktop_only_and_cannot_repeat_primary_navigation(tmp_path) -> None:
    store = FeaturePreferenceStore(_database(tmp_path))
    preferences = store.replace(
        1, expected_version=0,
        pinned=["stock-screener", "price-alerts", "risk-calculator"], recent=[],
    )
    payload_catalog = catalog("标准版", runtime={
            "market-heatmap": {
            "data_state": "ready", "health": "healthy", "verified_at": "2026-08-13T00:00:00+00:00",
        },
    }, now=datetime(2026, 8, 13, 0, 1, tzinfo=UTC))
    payload_catalog["preferences"] = preferences
    assert secondary_nav_features(payload_catalog, viewport_width=1024) == []
    desktop = secondary_nav_features(payload_catalog, viewport_width=1025)
    assert [item["key"] for item in desktop] == ["stock-screener", "price-alerts", "risk-calculator"]
    assert all(item["primary_nav"] is False for item in desktop)


def test_preferences_are_cas_protected_cleaned_and_isolated(tmp_path) -> None:
    store = FeaturePreferenceStore(_database(tmp_path))

    initial = store.load(1)
    assert initial == {"pinned": [], "recent": [], "version": 0}
    saved = store.replace(
        1,
        expected_version=0,
        pinned=["stock-screener", "risk-calculator", "price-alerts"],
        recent=["unknown", "stock-screener", "stock-screener", "risk-calculator"],
    )
    assert saved == {
        "pinned": ["stock-screener", "risk-calculator", "price-alerts"],
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
        pinned=["stock-screener", "risk-calculator", "price-alerts"], recent=["option-lab"],
    )
    updated = store.record_recent(1, expected_version=saved["version"], key="stock-screener")
    assert updated == {
        "pinned": ["stock-screener", "risk-calculator", "price-alerts"],
        "recent": ["stock-screener", "option-lab"],
        "version": 2,
    }
    with pytest.raises(FeatureCatalogConflict):
        store.record_recent(1, expected_version=1, key="price-alerts")
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
