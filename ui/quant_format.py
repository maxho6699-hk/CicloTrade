"""Customer-facing labels for internal quant records."""

from __future__ import annotations

import re
from typing import Any


def contract_label(leg: dict[str, Any], *, show_market: bool = False) -> str:
    symbol = str(leg.get("symbol") or "--")
    market = "大A" if str(leg.get("market") or "").upper() == "CN" or symbol.isdigit() else "美股"
    prefix = f"{market} · " if show_market else ""
    if leg.get("instrument_type") != "option":
        return f"{prefix}{symbol}"
    right = "Call" if str(leg.get("option_right") or "").upper() == "CALL" else "Put"
    strike = float(leg.get("option_strike") or 0)
    return f"{prefix}{symbol} · {leg.get('option_expiry') or '--'} · {strike:g} {right}"


def strategy_version_label(value: Any) -> str:
    text = str(value or "--").strip()
    catalog = re.fullmatch(r"catalog-(\d{4}-\d{2}-\d{2})", text, flags=re.IGNORECASE)
    if catalog:
        return f"每日模型 {catalog.group(1)}"
    simple = re.fullmatch(r"v(\d+(?:\.\d+)*)", text, flags=re.IGNORECASE)
    if simple:
        return f"第 {simple.group(1)} 版"
    return text


def source_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("tiger"):
        return "Tiger 模擬帳戶"
    if text.startswith("ciclotrade"):
        return "系統量化模型"
    return "策略服務"
