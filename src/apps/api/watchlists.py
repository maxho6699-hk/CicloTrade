"""Canonical validation and normalization for browser watchlists."""

from __future__ import annotations

import re
from typing import Any


WATCHLIST_LIMIT = 100
WATCHLIST_MARKETS = {"US": "us", "CN": "a_share"}


def normalize_watchlist_symbol(value: Any, market: str) -> str:
    if not isinstance(value, str):
        raise ValueError("自选股票代码必须是字符串。")
    symbol = value.strip().upper()
    if market == "CN" and symbol.endswith((".SS", ".SZ")):
        symbol = symbol[:-3]
    pattern = r"\d{6}" if market == "CN" else r"[A-Z][A-Z0-9.-]{0,11}"
    if not re.fullmatch(pattern, symbol):
        raise ValueError("自选股票代码无效。")
    return symbol


def normalize_watchlists(stored: dict[str, Any]) -> dict[str, list[str]]:
    source = stored.get("watchlists")
    source = source if isinstance(source, dict) else {}
    output: dict[str, list[str]] = {"us": [], "a_share": []}
    for market, key in WATCHLIST_MARKETS.items():
        values = source.get(key)
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            try:
                symbol = normalize_watchlist_symbol(value, market)
            except ValueError:
                continue
            if symbol not in output[key] and len(output[key]) < WATCHLIST_LIMIT:
                output[key].append(symbol)
    pins = normalize_watchlist_pins(stored, output)
    return {
        key: [symbol for symbol in pins[key] if symbol in output[key]]
        + [symbol for symbol in output[key] if symbol not in pins[key]]
        for key in output
    }


def normalize_watchlist_pins(
    stored: dict[str, Any], watchlists: dict[str, list[str]] | None = None
) -> dict[str, list[str]]:
    """Return valid pinned symbols, limited to symbols still in each watchlist."""
    source = stored.get("watchlist_pins")
    source = source if isinstance(source, dict) else {}
    current = watchlists if watchlists is not None else normalize_watchlists({"watchlists": stored.get("watchlists")})
    output: dict[str, list[str]] = {"us": [], "a_share": []}
    for market, key in WATCHLIST_MARKETS.items():
        values = source.get(key)
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            try:
                symbol = normalize_watchlist_symbol(value, market)
            except ValueError:
                continue
            if symbol in current[key] and symbol not in output[key]:
                output[key].append(symbol)
    return output
