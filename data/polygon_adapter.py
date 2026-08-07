# -*- coding: utf-8 -*-
"""Polygon 历史行情备用适配器。"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import json
import os
import re
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd

from data.datasource import DataSource, DataSourceError, require_market_data_enabled


class PolygonAdapter(DataSource):
    name = "Polygon"
    # This adapter currently exposes daily aggregates only.
    delay_minutes = None

    def __init__(self) -> None:
        self.api_key = os.getenv("POLYGON_API_KEY", "")

    @staticmethod
    def _lookback_days(period: str) -> int:
        """Translate the common provider period syntax without truncating backtests."""
        value = str(period or "3mo").strip().lower()
        match = re.fullmatch(r"(\d+)\s*([dmy])", value)
        if not match:
            return 90
        amount, unit = int(match.group(1)), match.group(2)
        return amount * {"d": 1, "m": 31, "y": 366}[unit]

    def history(self, symbols: tuple[str, ...], period: str = "3mo", interval: str = "1d") -> tuple[pd.DataFrame, pd.DataFrame]:
        require_market_data_enabled()
        if interval not in {"1d", "1day"}:
            raise DataSourceError("Polygon 历史适配器目前只支持日线数据。")
        if not self.api_key:
            raise DataSourceError("POLYGON_API_KEY 尚未配置。")
        end = datetime.now(UTC).date()
        start = end - timedelta(days=self._lookback_days(period))
        closes: dict[str, pd.Series] = {}
        volumes: dict[str, pd.Series] = {}
        for symbol in symbols:
            query = urlencode({"adjusted": "true", "sort": "asc", "limit": 5000})
            url = f"https://api.polygon.io/v2/aggs/ticker/{quote(symbol, safe='')}/range/1/day/{start}/{end}?{query}"
            request = Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
            try:
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                raise DataSourceError(f"Polygon {symbol} 请求失败：{exc}") from exc
            rows = payload.get("results") or []
            if not rows:
                raise DataSourceError(f"Polygon 未返回 {symbol} 行情。")
            index = pd.to_datetime([row["t"] for row in rows], unit="ms", utc=True)
            closes[symbol] = pd.Series([row["c"] for row in rows], index=index, dtype=float)
            volumes[symbol] = pd.Series([row.get("v", 0) for row in rows], index=index, dtype=float)
        close_frame = pd.concat(closes, axis=1)
        return close_frame, pd.concat(volumes, axis=1).reindex(close_frame.index).fillna(0)
