# -*- coding: utf-8 -*-
"""Versioned, server-authoritative product feature catalog and preferences."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from core.compat import UTC
import hashlib
import json
from typing import Any, Mapping

from core.database import DatabaseManager, get_database
from core.user_settings import load_user_settings
from core.plans import can


CATEGORIES = frozenset({"discover", "research", "simulate", "review", "automation", "account"})
DATA_STATES = frozenset({"ready", "delayed", "stale", "missing", "not_applicable"})
HEALTH_STATES = frozenset({"healthy", "degraded", "unavailable", "not_applicable"})
PLACEMENTS = frozenset({"more", "secondary_nav", "dashboard_card", "inspector", "drawer", "dialog", "overlay"})
ACTION_NAMES = frozenset({"research_url", "alert_prefill", "paper_prefill"})
ACTION_FORBIDDEN_FIELD_PARTS = frozenset({"account", "quote", "idempotency", "submit", "execute", "order", "auto"})
ICON_ALLOWLIST = frozenset({
    "BellRing", "BookOpenCheck", "CalendarClock", "ChartCandlestick", "ClipboardCheck",
    "Gauge", "Grid2X2", "LifeBuoy", "ListFilter", "RadioTower", "ShieldCheck",
    "Sparkles", "Target", "WalletCards",
})
ROUTE_PREFIX_ALLOWLIST = (
    "/account", "/discover", "/earnings", "/feedback", "/more", "/notifications", "/paper",
    "/portfolio", "/reports", "/research", "/today", "/trade",
)
PRIMARY_NAV_ROUTES = frozenset({"/today", "/discover", "/research", "/paper", "/portfolio", "/more"})
PREFERENCE_KEY = "feature_catalog_preferences_v1"
MAX_RECENT = 8
RUNTIME_EVIDENCE_TTL = timedelta(minutes=5)
RUNTIME_EVIDENCE_FUTURE_SKEW = timedelta(seconds=30)


class FeatureCatalogError(ValueError):
    """Base feature-catalog contract error."""


class FeatureCatalogConflict(FeatureCatalogError):
    """The optimistic-concurrency version no longer matches."""


class FeatureCatalogValidationError(FeatureCatalogError):
    """The requested preference state violates the catalog contract."""


@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    routes: tuple[str, ...]
    category: str
    title_key: str
    description_key: str
    icon: str
    capability: str | None
    pin_allowed: bool
    primary_nav: bool
    sort_order: int
    planned: bool = False
    safety_critical: bool = False
    recommendation_rank: int | None = None
    requires_runtime: bool = False
    placements: tuple[str, ...] = ("more",)
    actions: Mapping[str, object] | None = None


FEATURES = (
    # The six stable navigation modules are represented once here and may never be pinned.
    FeatureDefinition("today", ("/today",), "review", "feature.today.title", "feature.today.description", "ClipboardCheck", None, False, True, 10, placements=("more", "dashboard_card")),
    FeatureDefinition("discover", ("/discover",), "discover", "feature.discover.title", "feature.discover.description", "Grid2X2", None, False, True, 20, placements=("more", "dashboard_card")),
    FeatureDefinition("research", ("/research",), "research", "feature.research.title", "feature.research.description", "ChartCandlestick", None, False, True, 30, placements=("more", "dashboard_card", "inspector")),
    FeatureDefinition("personal-paper", ("/paper",), "simulate", "feature.personal_paper.title", "feature.personal_paper.description", "WalletCards", None, False, True, 40, placements=("more", "dashboard_card", "drawer")),
    FeatureDefinition("portfolio", ("/portfolio",), "review", "feature.portfolio.title", "feature.portfolio.description", "ClipboardCheck", None, False, True, 50, placements=("more", "dashboard_card")),
    FeatureDefinition("more", ("/more",), "account", "feature.more.title", "feature.more.description", "Sparkles", None, False, True, 60, placements=("more",)),
    FeatureDefinition("stock-screener", ("/discover?tool=screener",), "discover", "feature.stock_screener.title", "feature.stock_screener.description", "ListFilter", "strategy_all", True, False, 100, recommendation_rank=10, placements=("more", "secondary_nav", "dashboard_card"), actions={"research_url": "/discover?tool=screener"}),
    FeatureDefinition("market-heatmap", ("/discover?tool=heatmap",), "discover", "feature.market_heatmap.title", "feature.market_heatmap.description", "Grid2X2", "dashboard", True, False, 110, recommendation_rank=20, requires_runtime=True, placements=("more", "secondary_nav", "dashboard_card"), actions={"research_url": "/discover?tool=heatmap"}),
    FeatureDefinition("earnings-calendar", ("/discover?tool=calendar",), "discover", "feature.earnings_calendar.title", "feature.earnings_calendar.description", "CalendarClock", "dashboard", True, False, 120, requires_runtime=True, placements=("more", "secondary_nav", "dashboard_card")),
    FeatureDefinition("price-alerts", ("/research?tool=alerts",), "research", "feature.price_alerts.title", "feature.price_alerts.description", "BellRing", "alert_basic", True, False, 210, placements=("more", "secondary_nav", "inspector", "drawer"), actions={"alert_prefill": {"market": "US"}}),
    FeatureDefinition("option-lab", ("/research?workspace=options",), "research", "feature.option_lab.title", "feature.option_lab.description", "Gauge", "option_strategy", True, False, 220, requires_runtime=True, placements=("more", "secondary_nav", "inspector")),
    FeatureDefinition("earnings-forecast", ("/earnings",), "research", "feature.earnings_forecast.title", "feature.earnings_forecast.description", "Sparkles", "earnings_forecast", True, False, 230, requires_runtime=True, placements=("more", "secondary_nav", "dashboard_card")),
    FeatureDefinition("strategy-research", ("/reports?view=影子策略研究&research_scope=expanded",), "research", "feature.strategy_research.title", "feature.strategy_research.description", "BookOpenCheck", None, True, False, 240, recommendation_rank=15, requires_runtime=True, placements=("more", "secondary_nav", "dashboard_card"), actions={"research_url": "/reports?view=影子策略研究&research_scope=expanded"}),
    FeatureDefinition("risk-calculator", ("/paper?tool=risk",), "simulate", "feature.risk_calculator.title", "feature.risk_calculator.description", "ShieldCheck", None, True, False, 300, safety_critical=True, recommendation_rank=5, placements=("more", "secondary_nav", "drawer"), actions={"paper_prefill": {"market": "US", "side": "BUY"}}),
    FeatureDefinition("research-reports", ("/reports",), "review", "feature.research_reports.title", "feature.research_reports.description", "BookOpenCheck", "reports", True, False, 410, placements=("more", "secondary_nav", "dashboard_card")),
    FeatureDefinition("data-status", ("/account?section=data",), "account", "feature.data_status.title", "feature.data_status.description", "RadioTower", None, True, False, 500, safety_critical=True, placements=("more", "secondary_nav", "drawer")),
    FeatureDefinition("feedback", ("/feedback",), "account", "feature.feedback.title", "feature.feedback.description", "LifeBuoy", None, True, False, 510, placements=("more", "secondary_nav", "dialog")),
    FeatureDefinition("option-live-automation", ("/trade?mode=options",), "automation", "feature.option_live_automation.title", "feature.option_live_automation.description", "Target", "option_auto_live", False, False, 900, planned=True, placements=("more",)),
)

_BY_KEY = {item.key: item for item in FEATURES}
if len(_BY_KEY) != len(FEATURES):  # pragma: no cover - import-time invariant
    raise RuntimeError("feature keys must be unique")
for _feature in FEATURES:  # pragma: no cover - import-time invariants
    if _feature.category not in CATEGORIES or _feature.icon not in ICON_ALLOWLIST or not _feature.routes:
        raise RuntimeError(f"unsafe feature definition: {_feature.key}")
    if not set(_feature.placements).issubset(PLACEMENTS):
        raise RuntimeError(f"unsafe feature placement: {_feature.key}")
    if _feature.primary_nav != (_feature.routes[0] in PRIMARY_NAV_ROUTES):
        raise RuntimeError(f"primary navigation route mismatch: {_feature.key}")
    if _feature.primary_nav and _feature.pin_allowed:
        raise RuntimeError(f"primary navigation cannot be pinned: {_feature.key}")
    if _feature.actions and not set(_feature.actions).issubset(ACTION_NAMES):
        raise RuntimeError(f"unsafe feature action: {_feature.key}")
    for _route in _feature.routes:
        if not any(_route == prefix or _route.startswith(f"{prefix}?") or _route.startswith(f"{prefix}/") for prefix in ROUTE_PREFIX_ALLOWLIST):
            raise RuntimeError(f"unsafe feature route: {_route}")

if {feature.routes[0] for feature in FEATURES if feature.primary_nav} != PRIMARY_NAV_ROUTES:  # pragma: no cover
    raise RuntimeError("the six primary navigation modules must be complete")

_VERSION_SOURCE = json.dumps([asdict(item) for item in FEATURES], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
CATALOG_VERSION = f"2026.08.13-{hashlib.sha256(_VERSION_SOURCE.encode()).hexdigest()[:12]}"


def _aware_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _runtime_state(
    value: object,
    *,
    required: bool,
    now: datetime,
) -> tuple[str, str, str, str | None]:
    if value is None:
        return ("unavailable", "missing", "unavailable", "尚未取得可核验的数据新鲜度或服务健康证明。") if required else ("available", "not_applicable", "not_applicable", None)
    if not isinstance(value, Mapping):
        return "unavailable", "missing", "unavailable", "运行状态格式无效，功能已安全停用。"
    data_state = str(value.get("data_state") or "missing")
    health = str(value.get("health") or "unavailable")
    verified_at = _aware_utc(value.get("verified_at"))
    if data_state not in DATA_STATES or health not in HEALTH_STATES:
        return "unavailable", "missing", "unavailable", "数据状态或服务健康状态无法验证，功能已安全停用。"
    if verified_at is None:
        return "unavailable", "missing", "unavailable", "未取得可核验的数据新鲜度证据，功能已安全停用。"
    reason = str(value.get("reason") or "").strip() or None
    if verified_at is not None:
        age = now - verified_at
        if age < -RUNTIME_EVIDENCE_FUTURE_SKEW:
            return "unavailable", "missing", "unavailable", "运行状态时间晚于当前时钟，功能已安全停用。"
        if age > RUNTIME_EVIDENCE_TTL:
            return "unavailable", "stale", "unavailable", "运行状态证明已超过 5 分钟，请刷新后重试。"
    if data_state == "missing" or health == "unavailable":
        availability = "unavailable"
    elif data_state in {"delayed", "stale"} or health == "degraded":
        availability = "degraded"
    elif data_state == "ready" and health == "healthy":
        availability = "available"
    elif not required and data_state == "not_applicable" and health == "not_applicable":
        availability = "available"
    else:
        availability = "unavailable"
        reason = reason or "运行状态组合无法验证，功能已安全停用。"
    if availability != "available" and not reason:
        reason = "当前数据或服务未达到可用门限。"
    return availability, data_state, health, reason


def _validate_action_contract(actions: Mapping[str, object]) -> dict[str, object]:
    """Return a declarative-only action contract, never an executable command."""
    if not set(actions).issubset(ACTION_NAMES):
        raise FeatureCatalogValidationError("feature action is not allowlisted")
    result: dict[str, object] = {}
    for name, value in actions.items():
        if name == "research_url":
            if not isinstance(value, str) or not any(
                value == prefix or value.startswith(f"{prefix}?") or value.startswith(f"{prefix}/")
                for prefix in ROUTE_PREFIX_ALLOWLIST
            ):
                raise FeatureCatalogValidationError("research action route is invalid")
            result[name] = value
            continue
        if not isinstance(value, Mapping):
            raise FeatureCatalogValidationError("feature prefill action must be an object")
        normalized: dict[str, object] = {}
        for field, field_value in value.items():
            if not isinstance(field, str) or any(part in field.lower() for part in ACTION_FORBIDDEN_FIELD_PARTS):
                raise FeatureCatalogValidationError("feature action contains an executable field")
            if field not in {"market", "symbol", "price", "side", "reference_id"}:
                raise FeatureCatalogValidationError("feature action field is not allowlisted")
            if isinstance(field_value, (Mapping, list, tuple, set)) or isinstance(field_value, bool) or field_value is None:
                raise FeatureCatalogValidationError("feature action value is invalid")
            normalized[field] = field_value
        result[name] = normalized
    return result


def resolve_feature_catalog(
    plan: str,
    runtime: Mapping[str, object] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve membership and runtime state without hiding safety information."""
    runtime = runtime or {}
    resolved_now = now or datetime.now(UTC)
    if resolved_now.tzinfo is None or resolved_now.utcoffset() is None:
        raise FeatureCatalogValidationError("now must include a timezone")
    resolved_now = resolved_now.astimezone(UTC)
    items: list[dict[str, Any]] = []
    for definition in sorted(FEATURES, key=lambda item: (item.sort_order, item.key)):
        if definition.planned:
            availability, access = "planned", "wait"
            reason = "该能力仍在独立开发与验收中，当前会员不包含此功能。"
            data_state = "not_applicable"
            health = "not_applicable"
        elif definition.safety_critical or definition.capability is None or can(plan, definition.capability):
            availability, data_state, health, reason = _runtime_state(
                runtime.get(definition.key), required=definition.requires_runtime, now=resolved_now,
            )
            access = "open" if availability == "available" else "retry"
        else:
            availability, access = "locked", "upgrade"
            reason = "当前会员未包含此研究深度；风险与数据状态仍永久免费可见。"
            data_state = "not_applicable"
            health = "not_applicable"
        items.append({
            "key": definition.key,
            # ``route`` remains during the integration wave for old callers; ``routes`` is authoritative.
            "route": definition.routes[0],
            "routes": list(definition.routes),
            "category": definition.category,
            "title_key": definition.title_key,
            "description_key": definition.description_key,
            "icon": definition.icon,
            "capability": definition.capability,
            "availability": availability,
            "access": access,
            "reason": reason,
            "data_state": data_state,
            "health": health,
            "placements": list(definition.placements),
            "actions": _validate_action_contract(definition.actions or {}),
            "pin_allowed": definition.pin_allowed and not definition.primary_nav and availability == "available",
            "primary_nav": definition.primary_nav,
            "sort_order": definition.sort_order,
            "recommendation_rank": definition.recommendation_rank,
        })
    return {"catalog_version": CATALOG_VERSION, "items": items}


def _unique_known(values: object, *, maximum: int | None = None) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        key = str(value) if isinstance(value, str) else ""
        if key in _BY_KEY and key not in result:
            result.append(key)
        if maximum is not None and len(result) == maximum:
            break
    return result


def _decode_settings(raw: object) -> dict[str, Any]:
    try:
        settings = json.loads(str(raw)) if raw is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        settings = {}
    return settings if isinstance(settings, dict) else {}


def _clean_preferences(raw: object) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    pinned = [key for key in _unique_known(source.get("pinned")) if _BY_KEY[key].pin_allowed and not _BY_KEY[key].primary_nav and not _BY_KEY[key].planned]
    if len(pinned) not in {0, 3, 4, 5}:
        pinned = []
    version = source.get("version", 0)
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        version = 0
    return {"pinned": pinned, "recent": _unique_known(source.get("recent"), maximum=MAX_RECENT), "version": version}


def _validate_requested(pinned: object, recent: object) -> tuple[list[str], list[str]]:
    if not isinstance(pinned, list) or any(not isinstance(value, str) for value in pinned):
        raise FeatureCatalogValidationError("pinned must be a string list")
    if not isinstance(recent, list) or any(not isinstance(value, str) for value in recent):
        raise FeatureCatalogValidationError("recent must be a string list")
    requested_pins = list(dict.fromkeys(pinned))
    if len(requested_pins) not in {0, 3, 4, 5}:
        raise FeatureCatalogValidationError("pinned must contain zero or between three and five tools")
    for key in requested_pins:
        definition = _BY_KEY.get(key)
        if definition is None or not definition.pin_allowed or definition.primary_nav or definition.planned:
            raise FeatureCatalogValidationError(f"feature cannot be pinned: {key}")
    return requested_pins, _unique_known(recent, maximum=MAX_RECENT)


def secondary_nav_features(catalog: Mapping[str, object], *, viewport_width: int) -> list[dict[str, Any]]:
    """Resolve the desktop-only secondary navigation insertion point.

    At 1024px and below the shell keeps its fixed icon rail / five-item mobile
    bottom bar; preferences remain editable from More but create no extra nav.
    """
    if viewport_width <= 1024:
        return []
    items = catalog.get("items")
    preferences = catalog.get("preferences")
    if not isinstance(items, list) or not isinstance(preferences, Mapping):
        return []
    pins = _clean_preferences(preferences).get("pinned", [])
    by_key = {item.get("key"): item for item in items if isinstance(item, dict)}
    return [
        item for key in pins
        if isinstance((item := by_key.get(key)), dict)
        and item.get("pin_allowed") is True
        and item.get("primary_nav") is False
        and "secondary_nav" in item.get("placements", [])
        and item.get("availability") == "available"
    ]


class FeaturePreferenceStore:
    """CAS-protected feature preferences stored inside the existing settings row."""

    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def load(self, user_id: int) -> dict[str, Any]:
        settings = load_user_settings(user_id, self.db)
        return _clean_preferences(settings.get(PREFERENCE_KEY))

    def replace(
        self,
        user_id: int,
        *,
        expected_version: int,
        pinned: list[str],
        recent: list[str],
    ) -> dict[str, Any]:
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
            raise FeatureCatalogValidationError("expected_version must be a non-negative integer")
        normalized_pins, normalized_recent = _validate_requested(pinned, recent)
        with self.db.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
            settings = _decode_settings(row["settings_json"] if row else None)
            current = _clean_preferences(settings.get(PREFERENCE_KEY))
            if current["version"] != expected_version:
                raise FeatureCatalogConflict("feature preferences changed; reload before saving")
            updated = {
                "pinned": normalized_pins,
                "recent": normalized_recent,
                "version": expected_version + 1,
            }
            settings[PREFERENCE_KEY] = updated
            connection.execute(
                """INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
                (user_id, json.dumps(settings, ensure_ascii=False), datetime.now(UTC).isoformat(timespec="seconds")),
            )
        return updated

    def record_recent(
        self,
        user_id: int,
        *,
        expected_version: int,
        key: str,
    ) -> dict[str, Any]:
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
            raise FeatureCatalogValidationError("expected_version must be a non-negative integer")
        if not isinstance(key, str) or key not in _BY_KEY:
            raise FeatureCatalogValidationError("recent feature is unknown")
        with self.db.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
            settings = _decode_settings(row["settings_json"] if row else None)
            current = _clean_preferences(settings.get(PREFERENCE_KEY))
            if current["version"] != expected_version:
                raise FeatureCatalogConflict("feature preferences changed; reload before saving")
            updated = {
                "pinned": current["pinned"],
                "recent": [key, *(candidate for candidate in current["recent"] if candidate != key)][:MAX_RECENT],
                "version": expected_version + 1,
            }
            settings[PREFERENCE_KEY] = updated
            connection.execute(
                """INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
                (user_id, json.dumps(settings, ensure_ascii=False), datetime.now(UTC).isoformat(timespec="seconds")),
            )
        return updated


def pinnable_feature_keys() -> frozenset[str]:
    return frozenset(item.key for item in FEATURES if item.pin_allowed and not item.primary_nav and not item.planned)


def known_feature_keys() -> frozenset[str]:
    return frozenset(_BY_KEY)
