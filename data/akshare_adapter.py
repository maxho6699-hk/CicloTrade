# -*- coding: utf-8 -*-
"""AKShare adapter for mainland China equity research data.

AKShare is deliberately limited to A-share search and OHLCV data.  It never
supplies an execution quote, option chain, or option Greeks.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from data.datasource import DataSource, DataSourceError, require_market_data_enabled

try:  # Keep imports of unrelated data providers usable before dependencies install.
    import akshare as ak
except ImportError:  # pragma: no cover - exercised through the public error path
    ak = None


_PERIOD_DAYS = {
    "1d": 3,
    "5d": 10,
    "1mo": 35,
    "3mo": 100,
    "6mo": 190,
    "1y": 370,
    "2y": 740,
    "5y": 1_850,
}
_DAILY_PERIODS = {"1d": "daily", "1wk": "weekly", "1mo": "monthly"}
_INTRADAY_PERIODS = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "60m": "60", "1h": "60"}


class AKShareAdapter(DataSource):
    """Free, delayed/research-only A-share feed."""

    name = "AKShare"

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        value = str(symbol).strip().upper()
        if value.endswith((".SS", ".SZ")):
            value = value[:-3]
        if not value.isdigit() or len(value) != 6:
            raise DataSourceError("AKShare 当前只接收六位 A 股代码。")
        return value

    @staticmethod
    def _api() -> Any:
        if ak is None:
            raise DataSourceError("尚未安装 AKShare，无法读取 A 股免费行情。")
        return ak

    @staticmethod
    def _as_number(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if pd.notna(result) else None

    def search(self, query: str, market: str = "A股", max_results: int = 8) -> list[dict[str, str]]:
        require_market_data_enabled()
        if market != "A股" or not query.strip():
            return []
        try:
            directory = self._api().stock_zh_a_spot_em()
        except Exception as exc:
            raise DataSourceError(f"A 股搜索暂时不可用：{exc}") from exc
        required = {"代码", "名称"}
        if directory.empty or not required.issubset(directory.columns):
            raise DataSourceError("AKShare 没有返回可用的 A 股证券目录。")
        needle = query.strip().casefold()
        rows: list[dict[str, str]] = []
        for row in directory.to_dict("records"):
            symbol = str(row.get("代码") or "").strip()
            name = str(row.get("名称") or symbol).strip()
            if not symbol or (needle not in symbol.casefold() and needle not in name.casefold()):
                continue
            rows.append({
                "symbol": symbol,
                "name": name,
                "exchange": "上海" if symbol.startswith(("5", "6", "9")) else "深圳",
                "type": "股票",
            })
            if len(rows) >= max(1, min(int(max_results), 50)):
                break
        return rows

    def _normalize_frame(self, frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
        aliases = {
            "日期": "time", "时间": "time", "date": "time", "time": "time",
            "开盘": "Open", "open": "Open", "最高": "High", "high": "High",
            "最低": "Low", "low": "Low", "收盘": "Close", "close": "Close",
            "成交量": "Volume", "volume": "Volume",
        }
        renamed = frame.rename(columns={column: aliases.get(str(column), str(column)) for column in frame.columns})
        required = {"time", "Open", "High", "Low", "Close", "Volume"}
        if renamed.empty or not required.issubset(renamed.columns):
            raise DataSourceError(f"{symbol} 没有可用的 AKShare K 线。")
        renamed["time"] = pd.to_datetime(renamed["time"], errors="coerce")
        for column in ("Open", "High", "Low", "Close", "Volume"):
            renamed[column] = pd.to_numeric(renamed[column], errors="coerce")
        return (
            renamed.dropna(subset=["time", "Open", "High", "Low", "Close"])
            .drop_duplicates("time").set_index("time").sort_index()[["Open", "High", "Low", "Close", "Volume"]]
        )

    def bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        require_market_data_enabled()
        code = self.normalize_symbol(symbol)
        end = date.today()
        start = end - timedelta(days=_PERIOD_DAYS.get(period, 100))
        try:
            if interval in _DAILY_PERIODS:
                frame = self._api().stock_zh_a_hist(
                    symbol=code, period=_DAILY_PERIODS[interval],
                    start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), adjust="qfq",
                )
            elif interval in _INTRADAY_PERIODS:
                frame = self._api().stock_zh_a_hist_min_em(
                    symbol=code, start_date=start.strftime("%Y-%m-%d 09:30:00"),
                    end_date=end.strftime("%Y-%m-%d 15:00:00"), period=_INTRADAY_PERIODS[interval], adjust="qfq",
                )
            else:
                raise DataSourceError(f"AKShare 当前不支持 {interval} K 线。")
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"AKShare K 线请求失败：{exc}") from exc
        return self._normalize_frame(frame, code)

    def stock_quote(self, symbol: str) -> dict[str, object]:
        """Return the latest A-share bar as a non-executable research quote."""
        frame = self.bars(symbol, "5d", "1d")
        row = frame.iloc[-1]
        timestamp = frame.index[-1]
        previous = frame["Close"].dropna()
        return {
            "symbol": self.normalize_symbol(symbol),
            "last": self._as_number(row["Close"]),
            "bid": None,
            "ask": None,
            "spread": None,
            "open": self._as_number(row["Open"]),
            "high": self._as_number(row["High"]),
            "low": self._as_number(row["Low"]),
            "prev_close": self._as_number(previous.iloc[-2]) if len(previous) >= 2 else None,
            "volume": self._as_number(row["Volume"]),
            "quote_at": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
            "source": self.name,
            "is_realtime": False,
            "actionable_quote": False,
            "freshness": "A 股免费研究报价；实时等级未验证",
            "verification": "delayed_research_quote",
        }

    def history(
        self, symbols: tuple[str, ...], period: str = "3mo", interval: str = "1d"
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        frames = {symbol.upper(): self.bars(symbol, period, interval) for symbol in symbols}
        closes = pd.concat({symbol: frame["Close"] for symbol, frame in frames.items()}, axis=1).sort_index().ffill()
        volumes = pd.concat({symbol: frame["Volume"] for symbol, frame in frames.items()}, axis=1).reindex(closes.index).fillna(0)
        return closes.dropna(how="all"), volumes
