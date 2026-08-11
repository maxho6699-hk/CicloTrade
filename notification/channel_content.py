# -*- coding: utf-8 -*-
"""Channel-neutral CicloTrade recommendation content contracts.

Telegram renders this model today. Discord can render the same model later
without inheriting Telegram callbacks, tokens, or delivery state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
import math
from typing import Any, Iterable, Mapping


MISSING_FIELD = "未记录"
RESEARCH_ONLY_WARNING = "仅供研究，不用于立即交易"
MAX_ACTIONABLE_QUOTE_AGE_SECONDS = 300
MAX_ACTIONABLE_QUOTE_FUTURE_SKEW_SECONDS = 60


class RecommendationState(str, Enum):
    ENTRY = "entry"
    WAIT = "wait"
    MANAGE = "manage"
    INVALIDATED = "invalidated"
    NO_TRADE = "no_trade"
    DATA_INSUFFICIENT = "data_insufficient"
    RISK_PAUSED = "risk_paused"
    UNRECORDED = "unrecorded"


class RecommendationDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    UNRECORDED = "unrecorded"


class RecommendationChange(str, Enum):
    NEW_OPPORTUNITY = "new_opportunity"
    WAIT_TO_ENTRY = "wait_to_entry"
    ENTRY_TO_INVALIDATED = "entry_to_invalidated"
    RISK_CHANGED = "risk_changed"
    ACTION_CHANGED = "action_changed"
    QUOTE_SAFETY_CHANGED = "quote_safety_changed"
    NO_TRADE = "no_trade"
    DATA_INSUFFICIENT = "data_insufficient"
    RISK_PAUSED = "risk_paused"
    UPDATED = "updated"


@dataclass(frozen=True)
class ChannelRenderPolicy:
    """Channel-neutral safety inputs supplied at the actual render boundary."""

    reference_time: str
    immediate_action_allowed: bool = False
    delivery_delay_minutes: int = 0

    def __post_init__(self) -> None:
        if _parse_quote_time(self.reference_time) is None:
            raise ValueError("reference_time must be a timezone-aware ISO datetime")
        if not isinstance(self.immediate_action_allowed, bool):
            raise ValueError("immediate_action_allowed must be a boolean")
        if (
            isinstance(self.delivery_delay_minutes, bool)
            or not isinstance(self.delivery_delay_minutes, int)
            or not 0 <= self.delivery_delay_minutes <= 10_080
        ):
            raise ValueError("delivery_delay_minutes must be between 0 and 10080")

    @property
    def research_only(self) -> bool:
        return not self.immediate_action_allowed or self.delivery_delay_minutes > 0


STATE_LABELS = {
    RecommendationState.ENTRY: "推荐入场",
    RecommendationState.WAIT: "等待机会",
    RecommendationState.MANAGE: "仅管理已有持仓",
    RecommendationState.INVALIDATED: "建议已失效",
    RecommendationState.NO_TRADE: "今日不交易",
    RecommendationState.DATA_INSUFFICIENT: "数据不足",
    RecommendationState.RISK_PAUSED: "风险暂停",
    RecommendationState.UNRECORDED: MISSING_FIELD,
}

DIRECTION_LABELS = {
    RecommendationDirection.LONG: "做多",
    RecommendationDirection.SHORT: "做空",
    RecommendationDirection.UNRECORDED: MISSING_FIELD,
}

CHANGE_LABELS = {
    RecommendationChange.NEW_OPPORTUNITY: "新机会",
    RecommendationChange.WAIT_TO_ENTRY: "等待 → 入场",
    RecommendationChange.ENTRY_TO_INVALIDATED: "入场 → 失效",
    RecommendationChange.RISK_CHANGED: "止损 / 目标 / 风险已变化",
    RecommendationChange.ACTION_CHANGED: "行动合同已变化",
    RecommendationChange.QUOTE_SAFETY_CHANGED: "报价安全状态已变化",
    RecommendationChange.NO_TRADE: "今日不交易",
    RecommendationChange.DATA_INSUFFICIENT: "数据不足",
    RecommendationChange.RISK_PAUSED: "风险暂停",
    RecommendationChange.UPDATED: "机会已更新",
}


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first(candidates: Iterable[Mapping[str, Any]], *keys: str) -> Any:
    for candidate in candidates:
        for key in keys:
            if key in candidate:
                return candidate[key]
    return None


def _has(candidates: Iterable[Mapping[str, Any]], *keys: str) -> bool:
    return any(any(key in candidate for key in keys) for candidate in candidates)


def _text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _strict_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _parse_quote_time(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass(frozen=True)
class QuoteSafety:
    source: str | None
    quote_at: str | None
    freshness: str | None
    is_realtime: bool | None
    actionable_quote: bool | None
    fallback_from: str | None
    fallback_recorded: bool
    verification: str | None
    evaluated_at: str | None

    def supports_immediate_action_at(self, reference_time: object = None) -> bool:
        """Fail closed unless a verified OpenD/Futu quote is explicitly actionable."""
        source = (self.source or "").strip().lower()
        freshness = (self.freshness or "").strip().lower()
        verification = (self.verification or "").strip().lower()
        provider_key = "".join(char for char in source if char.isalnum())
        verified_provider = provider_key in {"opend", "futuopend"}
        delayed_or_unverified = any(
            marker in f"{freshness} {verification}"
            for marker in (
                "delay", "delayed", "research", "unverified", "request_failed",
                "stale", "cached", "historical", "history",
                "延迟", "延遲", "研究", "未验证", "未驗證", "回退",
                "过期", "過期", "陈旧", "陳舊", "缓存", "緩存", "历史", "歷史",
                "免费", "免費",
            )
        )
        realtime_freshness = any(
            marker in freshness for marker in ("realtime", "real-time", "live", "实时", "實時")
        )
        verification_parts = verification.split("_")
        verified_entitlement = verification == "verified_realtime" or (
            len(verification_parts) >= 4
            and verification_parts[0] in {"opend", "futu"}
            and "_".join(verification_parts[1:-1])
            in {"qot_right", "us_qot_right", "option_qot_right", "us_option_qot_right"}
            and verification_parts[-1] in {"lv1", "lv2", "lv3"}
        )
        quote_time = _parse_quote_time(self.quote_at)
        reference = (
            _parse_quote_time(self.evaluated_at)
            if reference_time is None
            else _parse_quote_time(reference_time)
        )
        quote_age = (reference - quote_time).total_seconds() if quote_time and reference else None
        timely = bool(
            quote_age is not None
            and -MAX_ACTIONABLE_QUOTE_FUTURE_SKEW_SECONDS
            <= quote_age
            <= MAX_ACTIONABLE_QUOTE_AGE_SECONDS
        )
        return bool(
            timely
            and self.is_realtime is True
            and self.actionable_quote is True
            and verified_provider
            and verified_entitlement
            and realtime_freshness
            and self.fallback_recorded
            and not self.fallback_from
            and not delayed_or_unverified
        )

    @property
    def supports_immediate_action(self) -> bool:
        return self.supports_immediate_action_at()

    @property
    def safety_text(self) -> str:
        return "已验证实时报价，可核对即时行动描述" if self.supports_immediate_action else RESEARCH_ONLY_WARNING

    def safety_text_at(self, reference_time: object = None) -> str:
        return (
            "已验证实时报价，可核对即时行动描述"
            if self.supports_immediate_action_at(reference_time)
            else RESEARCH_ONLY_WARNING
        )

    def semantic_signature(self) -> tuple[object, ...]:
        return (
            self.source,
            self.supports_immediate_action,
            self.fallback_from,
            self.verification,
        )


@dataclass(frozen=True)
class RecommendationContent:
    event_id: int | None
    event_type: str | None
    event_time: str | None
    recorded_at: str | None
    state: RecommendationState
    state_explicit: bool
    direction: RecommendationDirection
    market: str | None
    instrument_type: str | None
    instrument_key: str | None
    symbol: str | None
    currency: str | None
    option_expiry: str | None
    option_right: str | None
    option_strike: float | None
    entry_price: float | None
    quantity: float | None
    target_quantity: float | None
    stop_price: float | None
    target_price: float | None
    max_risk: float | None
    invalidation_condition: str | None
    rationale: str | None
    quote: QuoteSafety

    @property
    def state_label(self) -> str:
        return STATE_LABELS[self.state]

    @property
    def direction_label(self) -> str:
        return DIRECTION_LABELS[self.direction]

    def action_signature(self) -> tuple[object, ...]:
        return (
            self.state,
            self.direction,
            self.market,
            self.instrument_type,
            self.instrument_key,
            self.symbol,
            self.currency,
            self.option_expiry,
            self.option_right,
            self.option_strike,
            self.entry_price,
            self.quantity,
            self.target_quantity,
            self.invalidation_condition,
        )

    def risk_signature(self) -> tuple[object, ...]:
        return (self.stop_price, self.target_price, self.max_risk)

    def semantic_signature(self) -> tuple[object, ...]:
        """Exclude timestamps so a quote refresh alone cannot create notification spam."""
        return (*self.action_signature(), *self.risk_signature(), *self.quote.semantic_signature())

    def as_channel_payload(self, reference_time: object = None) -> dict[str, Any]:
        """Serialize for a channel, failing closed without its actual send time."""
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["direction"] = self.direction.value
        payload["state_label"] = self.state_label
        payload["direction_label"] = self.direction_label
        supports_immediate_action = bool(
            reference_time is not None
            and self.quote.supports_immediate_action_at(reference_time)
        )
        payload["quote"]["supports_immediate_action"] = supports_immediate_action
        payload["quote"]["safety_text"] = (
            "已验证实时报价，可核对即时行动描述"
            if supports_immediate_action
            else RESEARCH_ONLY_WARNING
        )
        return payload


def _state_from_value(value: object) -> RecommendationState | None:
    cleaned = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "entry": RecommendationState.ENTRY,
        "execute": RecommendationState.ENTRY,
        "action": RecommendationState.ENTRY,
        "buy": RecommendationState.ENTRY,
        "short": RecommendationState.ENTRY,
        "wait": RecommendationState.WAIT,
        "waiting": RecommendationState.WAIT,
        "watch": RecommendationState.WAIT,
        "manage": RecommendationState.MANAGE,
        "reduce": RecommendationState.MANAGE,
        "exit": RecommendationState.MANAGE,
        "cover": RecommendationState.MANAGE,
        "invalid": RecommendationState.INVALIDATED,
        "invalidated": RecommendationState.INVALIDATED,
        "cancelled": RecommendationState.INVALIDATED,
        "canceled": RecommendationState.INVALIDATED,
        "expired": RecommendationState.INVALIDATED,
        "no_trade": RecommendationState.NO_TRADE,
        "today_no_trade": RecommendationState.NO_TRADE,
        "data_insufficient": RecommendationState.DATA_INSUFFICIENT,
        "insufficient_data": RecommendationState.DATA_INSUFFICIENT,
        "risk_paused": RecommendationState.RISK_PAUSED,
        "risk_pause": RecommendationState.RISK_PAUSED,
        "推荐入场": RecommendationState.ENTRY,
        "推薦入場": RecommendationState.ENTRY,
        "等待机会": RecommendationState.WAIT,
        "等待機會": RecommendationState.WAIT,
        "今日不交易": RecommendationState.NO_TRADE,
        "数据不足": RecommendationState.DATA_INSUFFICIENT,
        "資料不足": RecommendationState.DATA_INSUFFICIENT,
        "风险暂停": RecommendationState.RISK_PAUSED,
        "風險暫停": RecommendationState.RISK_PAUSED,
    }
    return aliases.get(cleaned)


def _direction_from_value(value: object) -> RecommendationDirection | None:
    cleaned = str(value or "").strip().lower()
    if cleaned in {"long", "buy", "做多", "多"}:
        return RecommendationDirection.LONG
    if cleaned in {"short", "sell_short", "做空", "空"}:
        return RecommendationDirection.SHORT
    return None


def recommendation_from_event(
    event: Mapping[str, Any],
    leg: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None = None,
) -> RecommendationContent:
    """Build a fail-closed recommendation contract without inventing missing data."""
    event_values = _mapping(event)
    leg_values = _mapping(leg)
    metadata_values = _mapping(metadata)
    contracts = _mapping(metadata_values.get("contracts"))
    instrument_key = _text(leg_values.get("instrument_key"))
    symbol = _text(leg_values.get("symbol"))
    specific = _mapping(contracts.get(instrument_key or "") or contracts.get(symbol or ""))
    action_contract = _mapping(metadata_values.get("action_contract"))
    candidates = [specific, action_contract, metadata_values]

    risk_levels = _mapping(metadata_values.get("risk_levels"))
    risk = _mapping(risk_levels.get(instrument_key or "") or risk_levels.get(symbol or ""))
    risk_candidates = [risk, *candidates]

    quote_blocks = [
        _mapping(candidate.get("quote"))
        for candidate in candidates
        if isinstance(candidate.get("quote"), Mapping)
    ]
    quote_candidates = [*quote_blocks, *candidates]
    quote_source = _first(quote_blocks, "source") if quote_blocks else None
    if quote_source is None:
        quote_source = _first(candidates, "quote_source", "data_source")
    fallback_recorded = _has(quote_candidates, "fallback_from", "is_fallback")
    fallback_from = _text(_first(quote_candidates, "fallback_from"))
    if fallback_from is None and _first(quote_candidates, "is_fallback") is True:
        fallback_from = MISSING_FIELD
    quote = QuoteSafety(
        source=_text(quote_source),
        quote_at=_text(_first(quote_candidates, "quote_at", "data_time")),
        freshness=_text(_first(quote_candidates, "freshness")),
        is_realtime=_strict_bool(_first(quote_candidates, "is_realtime")),
        actionable_quote=_strict_bool(_first(quote_candidates, "actionable_quote")),
        fallback_from=fallback_from,
        fallback_recorded=fallback_recorded,
        verification=_text(_first(quote_candidates, "verification")),
        evaluated_at=_text(event_values.get("recorded_at") or event_values.get("occurred_at")),
    )

    explicit_state = _state_from_value(
        _first(candidates, "decision_state", "conclusion", "action_state", "stage", "status")
    )
    event_type = _text(event_values.get("event_type"))
    target = _number(leg_values.get("target_quantity"))
    delta = _number(leg_values.get("quantity_delta", leg_values.get("delta")))
    previous = target - delta if target is not None and delta is not None else None
    if event_type == "reversal":
        state = RecommendationState.INVALIDATED
    elif explicit_state is not None:
        state = explicit_state
    elif event_type == "correction" and target == 0 and previous not in {None, 0}:
        state = RecommendationState.INVALIDATED
    elif target is None or delta is None:
        state = RecommendationState.UNRECORDED
    elif target != 0 and (
        previous == 0
        or abs(target) > abs(previous)
        or (previous != 0 and target * previous < 0)
    ):
        state = RecommendationState.ENTRY
    else:
        state = RecommendationState.MANAGE

    exposure = target if target not in {None, 0} else previous
    direction = (
        RecommendationDirection.LONG
        if exposure is not None and exposure > 0
        else RecommendationDirection.SHORT
        if exposure is not None and exposure < 0
        else _direction_from_value(_first(candidates, "direction", "side"))
        or RecommendationDirection.UNRECORDED
    )

    rationale = _text(_first(candidates, "rationale", "reason"))
    if rationale:
        rationale = rationale[:500]
    return RecommendationContent(
        event_id=int(event_values["id"]) if str(event_values.get("id") or "").isdigit() else None,
        event_type=event_type,
        event_time=_text(event_values.get("occurred_at")),
        recorded_at=_text(event_values.get("recorded_at")),
        state=state,
        state_explicit=explicit_state is not None,
        direction=direction,
        market=_text(leg_values.get("market")),
        instrument_type=_text(leg_values.get("instrument_type")),
        instrument_key=instrument_key,
        symbol=symbol,
        currency=_text(leg_values.get("currency")),
        option_expiry=_text(leg_values.get("option_expiry", leg_values.get("expiry"))),
        option_right=_text(leg_values.get("option_right", leg_values.get("right"))),
        option_strike=_number(leg_values.get("option_strike", leg_values.get("strike"))),
        entry_price=_number(
            _first(risk_candidates, "entry_price", "entry", "reference_price")
            if _has(risk_candidates, "entry_price", "entry", "reference_price")
            else leg_values.get("price")
        ),
        quantity=abs(delta) if delta is not None else None,
        target_quantity=target,
        stop_price=_number(_first(risk_candidates, "stop_price", "stop_loss", "stop")),
        target_price=_number(_first(risk_candidates, "target_price", "target", "take_profit")),
        max_risk=_number(_first(risk_candidates, "max_loss", "max_risk", "risk_amount")),
        invalidation_condition=_text(
            _first(
                risk_candidates,
                "invalidation_condition",
                "invalid_condition",
                "invalidation",
                "expires_when",
            )
        ),
        rationale=rationale,
        quote=quote,
    )


def normalize_recommendation_revision(
    previous: RecommendationContent | None,
    current: RecommendationContent,
) -> RecommendationContent:
    """Keep a no-op correction on the prior action contract.

    QuantJournal reports the net execution delta for a correction. When the
    corrected target is unchanged that delta is zero, which must not turn an
    entry recommendation into a synthetic position-management update.
    """
    if (
        previous is not None
        and current.event_type == "correction"
        and not current.state_explicit
        and current.state == RecommendationState.MANAGE
        and current.quantity is not None
        and abs(current.quantity) < 1e-12
        and current.target_quantity == previous.target_quantity
    ):
        return replace(
            current,
            state=previous.state,
            direction=previous.direction,
            quantity=previous.quantity,
        )
    return current


def classify_recommendation_change(
    previous: RecommendationContent | None,
    current: RecommendationContent,
) -> RecommendationChange | None:
    """Return only material differences; timestamps alone never notify."""
    conclusion_changes = {
        RecommendationState.NO_TRADE: RecommendationChange.NO_TRADE,
        RecommendationState.DATA_INSUFFICIENT: RecommendationChange.DATA_INSUFFICIENT,
        RecommendationState.RISK_PAUSED: RecommendationChange.RISK_PAUSED,
    }
    if previous is None:
        return conclusion_changes.get(current.state, RecommendationChange.NEW_OPPORTUNITY)
    current = normalize_recommendation_revision(previous, current)
    if previous.semantic_signature() == current.semantic_signature():
        return None
    if previous.state == RecommendationState.WAIT and current.state == RecommendationState.ENTRY:
        return RecommendationChange.WAIT_TO_ENTRY
    if previous.state in {RecommendationState.ENTRY, RecommendationState.MANAGE} and current.state == RecommendationState.INVALIDATED:
        return RecommendationChange.ENTRY_TO_INVALIDATED
    if current.state in conclusion_changes and previous.state != current.state:
        return conclusion_changes[current.state]
    if previous.risk_signature() != current.risk_signature():
        return RecommendationChange.RISK_CHANGED
    if previous.action_signature() != current.action_signature():
        return RecommendationChange.ACTION_CHANGED
    if previous.quote.semantic_signature() != current.quote.semantic_signature():
        return RecommendationChange.QUOTE_SAFETY_CHANGED
    return RecommendationChange.UPDATED


def recommendation_change_label(value: RecommendationChange | str | None) -> str:
    if value is None:
        return MISSING_FIELD
    try:
        normalized = value if isinstance(value, RecommendationChange) else RecommendationChange(str(value))
    except ValueError:
        return MISSING_FIELD
    return CHANGE_LABELS[normalized]


def recommendation_render_payload(
    content: RecommendationContent,
    change: RecommendationChange | str | None,
    policy: ChannelRenderPolicy,
) -> dict[str, Any]:
    """Build one plain-data render contract for Telegram or future Discord."""
    payload = content.as_channel_payload(
        None if policy.research_only else policy.reference_time
    )
    try:
        normalized_change = (
            change if isinstance(change, RecommendationChange) else RecommendationChange(str(change))
        )
    except ValueError:
        normalized_change = None
    payload["schema_version"] = 1
    payload["change"] = normalized_change.value if normalized_change else None
    payload["change_label"] = recommendation_change_label(normalized_change)
    final_actionable = bool(
        not policy.research_only
        and payload["quote"]["supports_immediate_action"]
    )
    payload["delivery"] = {
        "reference_time": policy.reference_time,
        "delay_minutes": policy.delivery_delay_minutes,
        "research_only": not final_actionable,
        "immediate_action_allowed": policy.immediate_action_allowed,
        "final_actionable": final_actionable,
        "final_actionability": (
            "immediate" if final_actionable else "research_only"
        ),
    }
    return payload
