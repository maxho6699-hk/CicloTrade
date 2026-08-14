"""HTTP-neutral adapter for the independent stock screener slice."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from core.stock_screener import StockScreenerAdapter, recommendation_to_candidate


class ApiStockScreenerAdapter:
    """Keep request validation and membership gating outside shared API wiring."""

    def __init__(self, *, has_capability=None):
        self._screener = StockScreenerAdapter(has_capability=has_capability)

    def read(
        self,
        candidates: Iterable[Mapping[str, Any]],
        payload: Mapping[str, Any] | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self._screener.screen(candidates, payload, now=now)

    def save_preset(
        self,
        current: Mapping[str, Any] | None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._screener.save_preset(current, payload)

    def read_recommendations(
        self,
        recommendations: Iterable[Mapping[str, Any]],
        payload: Mapping[str, Any] | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        try:
            candidates = [recommendation_to_candidate(item) for item in recommendations]
        except TypeError as exc:
            from core.stock_screener import StockScreenerError

            raise StockScreenerError("recommendations must be iterable") from exc
        return self._screener.screen(candidates, payload, now=now)
