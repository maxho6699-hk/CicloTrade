"""In-memory, non-canonical forming candles built from live snapshots.

The tracker owns no historical frame. A forming candle is an ephemeral overlay
and is discarded when the upstream feed is stale or loses permission.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any
from zoneinfo import ZoneInfo

from core.compat import UTC


_US_MARKET_TIMEZONE = ZoneInfo("America/New_York")
_TIMEFRAME_MINUTES = {
    "1分": 1, "2分": 2, "3分": 3, "4分": 4, "5分": 5,
    "10分": 10, "15分": 15, "20分": 20, "30分": 30, "45分": 45,
    "1小时": 60, "2小时": 120, "3小时": 180, "4小时": 240,
    "6小时": 360, "8小时": 480,
}


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bar_start_for(timeframe: str, observed_at: datetime) -> datetime:
    """Floor a fresh US quote timestamp to its exchange-time bar boundary."""
    local = observed_at.astimezone(_US_MARKET_TIMEZONE)
    if timeframe == "日线":
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        raise ValueError("unsupported realtime timeframe")
    minute = (local.minute // minutes) * minutes
    return local.replace(minute=minute, second=0, microsecond=0).astimezone(UTC)


class RealtimeCandleTracker:
    """Aggregate snapshot deltas into a client-only forming-candle overlay."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], dict[str, Any]] = {}
        self._sequence = 0

    def reset(self, symbol: str, timeframe: str) -> None:
        self._states.pop((symbol, timeframe), None)

    def update(
        self,
        *,
        symbol: str,
        timeframe: str,
        observed_at: datetime,
        last: object,
        cumulative_volume: object,
    ) -> dict[str, object] | None:
        """Return a new overlay only when the upstream snapshot changes."""
        price = _finite(last)
        if price is None:
            return None
        volume = _finite(cumulative_volume)
        bar_start = bar_start_for(timeframe, observed_at)
        key = (symbol, timeframe)
        signature = (observed_at.isoformat(), price, volume)
        previous = self._states.get(key)
        if previous and previous["bar_start"] == bar_start and previous["signature"] == signature:
            return None
        if previous is None or previous["bar_start"] != bar_start:
            state: dict[str, Any] = {
                "bar_start": bar_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
                "last_cumulative_volume": volume,
                "signature": signature,
            }
            self._states[key] = state
        else:
            state = previous
            state["high"] = max(float(state["high"]), price)
            state["low"] = min(float(state["low"]), price)
            state["close"] = price
            last_volume = _finite(state.get("last_cumulative_volume"))
            if volume is not None and last_volume is not None and volume >= last_volume:
                state["volume"] = float(state["volume"]) + (volume - last_volume)
            state["last_cumulative_volume"] = volume
            state["signature"] = signature
        self._sequence += 1
        return {
            "sequence": self._sequence,
            "symbol": symbol,
            "timeframe": timeframe,
            "bar_start": bar_start.isoformat(),
            "open": float(state["open"]),
            "high": float(state["high"]),
            "low": float(state["low"]),
            "close": float(state["close"]),
            "volume": float(state["volume"]),
            "state": "forming",
            "forming": True,
            "observed_at": observed_at.astimezone(UTC).isoformat(),
        }
