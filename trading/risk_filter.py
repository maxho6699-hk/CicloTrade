# -*- coding: utf-8 -*-
"""所有订单共用的统一风控过滤器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    code: str
    message: str


def validate_order(
    *,
    symbol: str,
    quantity: float,
    price: float,
    symbol_exposure: float,
    total_exposure: float,
    daily_pnl: float,
    config: dict,
    paused: bool,
    require_market_hours: bool,
    projected_symbol_exposure: float | None = None,
    projected_total_exposure: float | None = None,
    cooldown_active: bool = False,
) -> RiskDecision:
    if paused:
        return RiskDecision(False, "OPENING_PAUSED", "系统已暂停所有新开仓。")
    if not symbol or quantity <= 0 or price <= 0:
        return RiskDecision(False, "INVALID_ORDER", "标的、数量和价格必须有效。")
    if cooldown_active:
        return RiskDecision(False, "LOSS_COOLDOWN", "账户连续亏损，仍处于交易冷却期。")
    new_value = quantity * price
    max_symbol = float(config.get("max_position_per_symbol", 5_000))
    max_total = float(config.get("max_total_position", 50_000))
    symbol_after = symbol_exposure + new_value if projected_symbol_exposure is None else projected_symbol_exposure
    total_after = total_exposure + new_value if projected_total_exposure is None else projected_total_exposure
    if symbol_after > max_symbol:
        return RiskDecision(False, "POSITION_LIMIT", f"{symbol} 订单会超过单标的仓位上限。")
    if total_after > max_total:
        return RiskDecision(False, "TOTAL_POSITION_LIMIT", "订单会超过账户总仓位上限。")
    if daily_pnl <= -float(config.get("max_daily_loss", 2_000)):
        return RiskDecision(False, "DAILY_LOSS", "账户已达到单日最大亏损阈值。")
    if require_market_hours:
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5 or not (time(9, 30) <= now.time() <= time(16, 0)):
            return RiskDecision(False, "TRADING_HOURS", "当前不在美股常规交易时段。")
    return RiskDecision(True, "PASS", "订单通过统一风控。")
