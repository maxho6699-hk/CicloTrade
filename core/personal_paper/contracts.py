from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
SIDES = {"BUY", "SELL", "SHORT", "COVER"}
ORDER_TYPES = {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}
SOURCE_KINDS = {"manual", "recommendation", "chart", "screener"}


class PersonalPaperValidationError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise PersonalPaperValidationError("请求包含不可序列化或非有限数值。") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise PersonalPaperValidationError(f"{label} 无效。")
    return value


def _decimal(value: Any, label: str, *, required: bool = True) -> Decimal | None:
    if value is None and not required:
        return None
    if isinstance(value, bool):
        raise PersonalPaperValidationError(f"{label} 必须为正数。")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PersonalPaperValidationError(f"{label} 必须为正数。") from exc
    if not result.is_finite() or result <= 0:
        raise PersonalPaperValidationError(f"{label} 必须为正数。")
    return result


def money_to_minor(value: Any, label: str, *, required: bool = True) -> int | None:
    number = _decimal(value, label, required=required)
    if number is None:
        return None
    minor = number * 100
    if minor != minor.to_integral_value(rounding=ROUND_HALF_UP):
        raise PersonalPaperValidationError(f"{label} 最多保留两位小数。")
    result = int(minor)
    if result > 10_000_000_000_000:
        raise PersonalPaperValidationError(f"{label} 超出范围。")
    return result


def quantity_to_micros(value: Any) -> int:
    number = _decimal(value, "quantity")
    assert number is not None
    if number != number.to_integral_value():
        raise PersonalPaperValidationError("首批个人模拟只支持整数股。")
    micros = number * 1_000_000
    result = int(micros)
    if result > 1_000_000_000_000:
        raise PersonalPaperValidationError("quantity 超出范围。")
    return result


def normalize_stock_order(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PersonalPaperValidationError("订单必须为对象。")
    required = {
        "idempotency_key", "season_id", "market", "symbol", "side", "order_type",
        "quantity", "limit_price", "stop_price", "time_in_force", "quote_id",
        "account_version", "source_context",
    }
    if set(raw) != required:
        raise PersonalPaperValidationError("订单字段不完整或包含未知字段。")
    value = dict(raw)
    value["idempotency_key"] = _id(value["idempotency_key"], "idempotency_key")
    value["season_id"] = _id(value["season_id"], "season_id")
    value["quote_id"] = _id(value["quote_id"], "quote_id")
    if value["market"] != "US" or not isinstance(value["symbol"], str) or not SYMBOL.fullmatch(value["symbol"]):
        raise PersonalPaperValidationError("首批个人模拟仅允许有效美股代码。")
    if value["side"] not in SIDES or value["order_type"] not in ORDER_TYPES:
        raise PersonalPaperValidationError("side 或 order_type 无效。")
    if value["time_in_force"] != "DAY":
        raise PersonalPaperValidationError("首批个人模拟仅支持 DAY。")
    if isinstance(value["account_version"], bool) or not isinstance(value["account_version"], int) or value["account_version"] < 0:
        raise PersonalPaperValidationError("account_version 无效。")
    value["quantity_micros"] = quantity_to_micros(value.pop("quantity"))
    value["limit_price_minor"] = money_to_minor(value.pop("limit_price"), "limit_price", required=False)
    value["stop_price_minor"] = money_to_minor(value.pop("stop_price"), "stop_price", required=False)
    expected = {
        "MARKET": (False, False), "LIMIT": (True, False),
        "STOP": (False, True), "STOP_LIMIT": (True, True),
    }[value["order_type"]]
    if (value["limit_price_minor"] is not None, value["stop_price_minor"] is not None) != expected:
        raise PersonalPaperValidationError("订单价格字段与 order_type 不匹配。")
    source = value["source_context"]
    if not isinstance(source, dict) or set(source) != {"kind", "reference_id"} or source["kind"] not in SOURCE_KINDS:
        raise PersonalPaperValidationError("source_context 无效。")
    if source["reference_id"] is not None:
        source = dict(source)
        source["reference_id"] = _id(source["reference_id"], "source_context.reference_id")
    value["source_context"] = source
    return value


def enforce_defined_risk_option_limit(
    *, pre_order_equity_minor: int, max_loss_minor: int | None,
    fees_minor: int | None, conservative_slippage_minor: int | None,
) -> int:
    values = (pre_order_equity_minor, max_loss_minor, fees_minor, conservative_slippage_minor)
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in values):
        raise PersonalPaperValidationError("期权最大亏损、费用、滑点与下单前权益证明必须完整。")
    assert max_loss_minor is not None and fees_minor is not None and conservative_slippage_minor is not None
    total = max_loss_minor + fees_minor + conservative_slippage_minor
    if total * 10 > pre_order_equity_minor:
        raise PersonalPaperValidationError("期权新开或加仓最大亏损不得超过下单前权益 10%。")
    return total


def minor_times_quantity(price_minor: int, quantity_micros: int) -> int:
    raw = Decimal(price_minor) * Decimal(quantity_micros) / Decimal(1_000_000)
    if not raw.is_finite():
        raise PersonalPaperValidationError("订单名义金额无效。")
    return int(raw.to_integral_value(rounding=ROUND_HALF_UP))


__all__ = [
    "PersonalPaperValidationError", "canonical_json", "enforce_defined_risk_option_limit",
    "minor_times_quantity", "normalize_stock_order", "sha256_json",
]
