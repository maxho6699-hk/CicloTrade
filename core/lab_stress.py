"""Deterministic, fixed-scenario portfolio stress calculations.

This module deliberately does not forecast returns or produce trading advice.
It re-marks an explicit, complete position snapshot using server-owned shocks,
fees and slippage, and emits enough provenance to audit the calculation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Callable, Mapping


METHOD_VERSION = "lab-stress.v1"
SCENARIOS: dict[str, dict[str, Any]] = {
    "market_drawdown": {"label": "市场回撤", "price_shock_pct": -20, "volatility_shock_pct": 50, "gap_risk": "normal"},
    "earnings_gap": {"label": "财报跳空", "price_shock_pct": -10, "volatility_shock_pct": 35, "gap_risk": "earnings"},
    "extreme_event": {"label": "极端事件", "price_shock_pct": -35, "volatility_shock_pct": 100, "gap_risk": "extreme"},
}
FEE_BPS = 5
SLIPPAGE_BPS = 10
MAX_POSITIONS = 200
MAX_ABS_QUANTITY = 10_000_000
MAX_PRICE = 10_000_000
MAX_SNAPSHOT_AGE_SECONDS = 15 * 60
MAX_FUTURE_SKEW_SECONDS = 60
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LabStressError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = "invalid_stress_request") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _finite(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise LabStressError(f"{name} 必须是有限数字。")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LabStressError(f"{name} 必须是有限数字。") from exc
    if not math.isfinite(parsed):
        raise LabStressError(f"{name} 必须是有限数字。")
    return parsed


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LabStressError("压力测试输入无法规范化。") from exc


def sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def scenario_catalog() -> dict[str, Any]:
    scenarios = [
        {"key": key, **SCENARIOS[key]}
        for key in sorted(SCENARIOS)
    ]
    content = {
        "method_version": METHOD_VERSION,
        "fee_bps": FEE_BPS,
        "slippage_bps": SLIPPAGE_BPS,
        "scenarios": scenarios,
    }
    return {**content, "catalog_sha256": sha256(content)}


def _as_of(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LabStressError("持仓快照缺少 as_of。", 409, "snapshot_incomplete")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LabStressError("as_of 必须是有效 ISO-8601 时间。", 409, "snapshot_incomplete") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise LabStressError("as_of 必须包含时区。", 409, "snapshot_incomplete")
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_snapshot(snapshot: Mapping[str, Any], *, now: datetime) -> tuple[list[dict[str, Any]], str, str, str, str]:
    if not isinstance(snapshot, Mapping):
        raise LabStressError("持仓快照必须是对象。", 409, "snapshot_incomplete")
    status = snapshot.get("data_status")
    if status not in {"fresh", "recorded"}:
        raise LabStressError("持仓快照不是可重估的新鲜完整数据。", 409, "snapshot_stale")
    as_of = _as_of(snapshot.get("as_of") or snapshot.get("captured_at"))
    captured = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    age = (now.astimezone(timezone.utc) - captured).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise LabStressError("持仓快照时间超前，已拒绝重估。", 409, "snapshot_future")
    if age > MAX_SNAPSHOT_AGE_SECONDS:
        raise LabStressError("持仓快照已过期，已拒绝重估。", 409, "snapshot_stale")
    account_mode = snapshot.get("account_mode")
    if account_mode not in {"official", "personal_paper"}:
        raise LabStressError("账户域无效，已拒绝混合账户数据。", 409, "account_scope_invalid")
    currency = snapshot.get("currency")
    positions = snapshot.get("positions")
    if currency not in {"USD", "HKD", "CNY"} or not isinstance(positions, list) or not positions:
        raise LabStressError("持仓快照不完整。", 409, "snapshot_incomplete")
    if len(positions) > MAX_POSITIONS:
        raise LabStressError("持仓数量超过压力测试上限。", 413, "position_limit")
    normalized: list[dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, Mapping):
            raise LabStressError("持仓记录无效。", 409, "snapshot_incomplete")
        symbol = str(position.get("symbol") or "").strip().upper()
        if not symbol or len(symbol) > 32 or position.get("instrument_type", "stock") != "stock":
            raise LabStressError("当前压力测试只支持股票持仓。", 409, "unsupported_position")
        if position.get("currency", currency) != currency:
            raise LabStressError("不允许混合币种压力测试。", 409, "mixed_currency")
        quantity = _finite(position.get("quantity"), name="quantity")
        price = _finite(position.get("last_trade_price", position.get("price")), name="price")
        if quantity == 0 or abs(quantity) > MAX_ABS_QUANTITY or price <= 0 or price > MAX_PRICE:
            raise LabStressError("持仓数量或价格超限。", 409, "position_bounds")
        normalized.append({"symbol": symbol, "quantity": quantity, "price": price, "currency": currency})
    normalized.sort(key=lambda item: item["symbol"])
    return normalized, currency, account_mode, as_of, str(status)


def calculate_stress(
    request: Mapping[str, Any],
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Calculate one fixed scenario; all scenario and cost values are server-owned."""
    if not isinstance(request, Mapping) or set(request) - {"scenario_key", "snapshot"}:
        raise LabStressError("压力测试请求字段无效。")
    key = request.get("scenario_key")
    if key not in SCENARIOS:
        raise LabStressError("压力场景不在服务端固定 allowlist。", 403, "scenario_not_allowed")
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise LabStressError("服务端 as_of 无效。", 503, "service_time_invalid")
    positions, currency, account_mode, as_of, data_status = _validate_snapshot(request.get("snapshot"), now=moment)
    scenario = SCENARIOS[key]
    input_payload = {"scenario_key": key, "snapshot": {"account_mode": account_mode, "currency": currency, "as_of": as_of, "positions": positions}}
    input_hash = sha256(input_payload)
    shock = float(scenario["price_shock_pct"]) / 100
    cost_rate = (FEE_BPS + SLIPPAGE_BPS) / 10_000
    rows: list[dict[str, Any]] = []
    baseline = stressed = 0.0
    for position in positions:
        notional = position["quantity"] * position["price"]
        baseline += notional
        stressed_value = notional * (1 + shock)
        transaction_cost = abs(stressed_value) * cost_rate
        stressed_net = stressed_value - transaction_cost
        stressed += stressed_net
        rows.append({"symbol": position["symbol"], "currency": currency, "quantity": position["quantity"], "base_price": position["price"], "stressed_price": round(position["price"] * (1 + shock), 10), "baseline_value": round(notional, 10), "stressed_value": round(stressed_net, 10), "cost": round(transaction_cost, 10)})
    result = {
        "method_version": METHOD_VERSION,
        "scenario": {"key": key, **scenario, "fee_bps": FEE_BPS, "slippage_bps": SLIPPAGE_BPS},
        "account_mode": account_mode,
        "currency": currency,
        "as_of": as_of,
        "evaluated_at": moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "data_status": data_status,
        "input_sha256": input_hash,
        "baseline_value": round(baseline, 10),
        "stressed_value": round(stressed, 10),
        "pnl_change": round(stressed - baseline, 10),
        "positions": rows,
        "is_prediction": False,
        "execution_eligible": False,
    }
    result["result_sha256"] = sha256(result)
    return result


def handle_lab_stress(payload: Mapping[str, Any], *, snapshot_provider: Callable[[], Mapping[str, Any]] | None = None, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """Route contract for thin app.py wiring; provider is optional and server-owned."""
    if not isinstance(payload, Mapping):
        raise LabStressError("压力测试请求字段无效。")
    request = dict(payload)
    if "snapshot" not in request:
        if snapshot_provider is None:
            raise LabStressError("必须提供显式持仓快照。", 409, "snapshot_required")
        request["snapshot"] = snapshot_provider()
    return calculate_stress(request, now=now)


__all__ = ["FEE_BPS", "METHOD_VERSION", "SCENARIOS", "SLIPPAGE_BPS", "LabStressError", "calculate_stress", "handle_lab_stress", "scenario_catalog", "sha256"]
