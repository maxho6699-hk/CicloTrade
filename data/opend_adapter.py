# -*- coding: utf-8 -*-
"""Futu OpenD adapter for US bars and option-chain analytics."""

from __future__ import annotations

from datetime import date, timedelta
import os

import pandas as pd

from data.datasource import DataSource, DataSourceError, require_market_data_enabled


_PERIOD_DAYS = {
    "5d": 10,
    "1mo": 35,
    "3mo": 100,
    "6mo": 190,
    "1y": 370,
    "2y": 740,
    "3y": 1_110,
    "5y": 1_850,
    "10y": 3_700,
    "max": 7_500,
}


class OpenDAdapter(DataSource):
    name = "Futu OpenD"
    supports_realtime = True

    def __init__(self) -> None:
        self.host = os.getenv("OPEND_HOST", "127.0.0.1").strip()
        try:
            self.port = int(os.getenv("OPEND_PORT", "11111"))
        except ValueError as exc:
            raise DataSourceError("OPEND_PORT 配置无效。") from exc
        if not self.host or not 1 <= self.port <= 65535:
            raise DataSourceError("OpenD 主机或端口配置无效。")

    def _context(self):
        try:
            from futu import OpenQuoteContext
        except ImportError as exc:
            raise DataSourceError("尚未安装 futu-api。") from exc
        try:
            return OpenQuoteContext(host=self.host, port=self.port)
        except Exception as exc:
            raise DataSourceError("无法连接 Futu OpenD。") from exc

    @staticmethod
    def _code(symbol: str) -> str:
        value = symbol.strip().upper()
        if not value or value.isdigit() or not all(char.isalnum() or char in ".-" for char in value):
            raise DataSourceError("OpenD 当前只接收美股代码。")
        return value if value.startswith("US.") else f"US.{value}"

    @staticmethod
    def _ktype(interval: str):
        from futu import KLType

        mapping = {
            "1m": KLType.K_1M,
            "5m": KLType.K_5M,
            "15m": KLType.K_15M,
            "30m": KLType.K_30M,
            "60m": KLType.K_60M,
            "1h": KLType.K_60M,
            "1d": KLType.K_DAY,
            "1wk": KLType.K_WEEK,
            "1mo": KLType.K_MON,
        }
        if interval not in mapping:
            raise DataSourceError(f"OpenD 当前不支持 {interval} K 线。")
        return mapping[interval]

    def _bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        from futu import AuType, RET_OK

        end = date.today()
        start = end - timedelta(days=_PERIOD_DAYS.get(period, 100))
        context = self._context()
        pages = []
        page_key = None
        try:
            while True:
                ret, data, page_key = context.request_history_kline(
                    self._code(symbol),
                    start=start.isoformat(),
                    end=end.isoformat(),
                    ktype=self._ktype(interval),
                    autype=AuType.QFQ,
                    max_count=1000,
                    page_req_key=page_key,
                )
                if ret != RET_OK:
                    raise DataSourceError(f"OpenD K 线请求失败：{str(data)[:240]}")
                pages.append(data)
                if not page_key:
                    break
        finally:
            context.close()
        frame = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()
        required = {"time_key", "open", "high", "low", "close", "volume"}
        if frame.empty or not required.issubset(frame.columns):
            raise DataSourceError(f"{symbol} 没有可用的 OpenD 行情。")
        frame["time_key"] = pd.to_datetime(frame["time_key"], errors="coerce")
        frame = frame.dropna(subset=["time_key"]).drop_duplicates("time_key").set_index("time_key").sort_index()
        return frame.rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        )

    def history(
        self, symbols: tuple[str, ...], period: str = "3mo", interval: str = "1d"
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        require_market_data_enabled()
        frames = {symbol.upper(): self._bars(symbol, period, interval) for symbol in symbols}
        closes = pd.concat({symbol: frame["Close"] for symbol, frame in frames.items()}, axis=1).sort_index().ffill()
        volumes = pd.concat({symbol: frame["Volume"] for symbol, frame in frames.items()}, axis=1).reindex(closes.index).fillna(0)
        return closes.dropna(how="all"), volumes

    def bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        require_market_data_enabled()
        return self._bars(symbol, period, interval)

    def search(self, query: str, market: str = "美股", max_results: int = 8) -> list[dict[str, str]]:
        require_market_data_enabled()
        if market != "美股" or not query.strip():
            return []
        from futu import Market, RET_OK, SecurityType

        context = self._context()
        try:
            ret, securities = context.get_stock_basicinfo(Market.US, SecurityType.STOCK)
        finally:
            context.close()
        if ret != RET_OK:
            raise DataSourceError(f"OpenD 证券目录请求失败：{str(securities)[:240]}")
        needle = query.strip().casefold()
        matches = []
        for row in securities.to_dict("records"):
            code = str(row.get("code") or "").removeprefix("US.")
            name = str(row.get("name") or code)
            if needle not in code.casefold() and needle not in name.casefold():
                continue
            score = 0 if code.casefold() == needle else 1 if code.casefold().startswith(needle) else 2 if name.casefold().startswith(needle) else 3
            matches.append((score, code, {"symbol": code, "name": name, "exchange": "Futu OpenD", "type": "股票"}))
        matches.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in matches[: max(1, min(int(max_results), 50))]]

    def option_chain(self, symbol: str, expiry: str | None = None) -> tuple[str, pd.DataFrame, pd.DataFrame]:
        require_market_data_enabled()
        from futu import RET_OK

        context = self._context()
        try:
            kwargs = {"start": expiry, "end": expiry} if expiry else {}
            ret, chain = context.get_option_chain(self._code(symbol), **kwargs)
            if ret != RET_OK or chain.empty:
                raise DataSourceError(f"OpenD 期权链请求失败：{str(chain)[:240]}")
            expiries = sorted(str(value) for value in chain["strike_time"].dropna().unique())
            selected = expiry if expiry in expiries else expiries[0]
            chain = chain[chain["strike_time"].astype(str) == selected].copy()
            snapshots = []
            codes = chain["code"].astype(str).tolist()
            for offset in range(0, len(codes), 400):
                ret, batch = context.get_market_snapshot(codes[offset : offset + 400])
                if ret != RET_OK:
                    raise DataSourceError(f"OpenD 期权快照请求失败：{str(batch)[:240]}")
                snapshots.append(batch)
        finally:
            context.close()
        snapshot = pd.concat(snapshots, ignore_index=True) if snapshots else pd.DataFrame(columns=["code"])
        merged = chain.merge(snapshot, on="code", how="left", suffixes=("", "_snapshot"))
        renamed = merged.rename(
            columns={
                "code": "contractSymbol",
                "strike_price": "strike",
                "update_time": "lastTradeDate",
                "last_price": "lastPrice",
                "bid_price": "bid",
                "ask_price": "ask",
                "option_open_interest": "openInterest",
                "option_implied_volatility": "impliedVolatility",
                "option_delta": "delta",
                "option_gamma": "gamma",
                "option_theta": "theta",
                "option_vega": "vega",
                "option_rho": "rho",
            }
        )
        columns = [
            "contractSymbol", "lastTradeDate", "strike", "lastPrice", "bid", "ask", "volume",
            "openInterest", "impliedVolatility", "delta", "gamma", "theta", "vega", "rho",
        ]
        for column in columns:
            if column not in renamed:
                renamed[column] = pd.NA
        option_type = renamed["option_type"].astype(str).str.upper()
        return selected, renamed.loc[option_type == "CALL", columns].reset_index(drop=True), renamed.loc[option_type == "PUT", columns].reset_index(drop=True)
