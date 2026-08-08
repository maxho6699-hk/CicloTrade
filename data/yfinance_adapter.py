# -*- coding: utf-8 -*-
"""Yahoo Finance 免费行情适配器。"""

from __future__ import annotations

import time

import pandas as pd
import yfinance as yf

from data.datasource import DataSource, DataSourceError, require_market_data_enabled


class YFinanceAdapter(DataSource):
    name = "Yahoo Finance"
    delay_minutes = 15

    @staticmethod
    def search(query: str, market: str = "美股", max_results: int = 8) -> list[dict[str, str]]:
        """Search Yahoo's equity/ETF directory and keep only the requested market."""
        value = query.strip()
        if not value:
            return []
        require_market_data_enabled()
        try:
            quotes = yf.Search(value, max_results=max(max_results * 3, 12), news_count=0, timeout=10).quotes
        except Exception as exc:
            raise DataSourceError(f"证券搜索暂时不可用：{exc}") from exc

        results: list[dict[str, str]] = []
        seen: set[str] = set()
        for quote in quotes:
            symbol = str(quote.get("symbol") or "").upper()
            if not symbol or str(quote.get("quoteType") or "").upper() not in {"EQUITY", "ETF"}:
                continue
            is_a_share = symbol.endswith((".SS", ".SZ"))
            if (market == "A股") != is_a_share or (market == "美股" and "." in symbol):
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            results.append(
                {
                    "symbol": symbol,
                    "name": str(quote.get("longname") or quote.get("shortname") or symbol),
                    "exchange": str(quote.get("exchDisp") or quote.get("exchange") or "Yahoo Finance"),
                    "type": "ETF" if str(quote.get("quoteType") or "").upper() == "ETF" else "股票",
                }
            )
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        value = symbol.strip().upper()
        if value.isdigit() and len(value) == 6:
            return f"{value}.SS" if value.startswith(("5", "6", "9")) else f"{value}.SZ"
        return value

    def history(self, symbols: tuple[str, ...], period: str = "3mo", interval: str = "1d") -> tuple[pd.DataFrame, pd.DataFrame]:
        require_market_data_enabled()
        normalized = {raw_symbol: self.normalize_symbol(raw_symbol) for raw_symbol in symbols}
        if len(normalized) > 1:
            try:
                bulk = yf.download(
                    list(normalized.values()),
                    period=period,
                    interval=interval,
                    auto_adjust=True,
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    timeout=12,
                )
            except Exception as exc:
                raise DataSourceError("批量行情请求暂时不可用。") from exc
            closes: dict[str, pd.Series] = {}
            volumes: dict[str, pd.Series] = {}
            for raw_symbol, symbol in normalized.items():
                try:
                    frame = bulk[symbol] if isinstance(bulk.columns, pd.MultiIndex) else bulk
                except KeyError:
                    continue
                if frame.empty or "Close" not in frame:
                    continue
                closes[raw_symbol.upper()] = frame["Close"].dropna()
                volumes[raw_symbol.upper()] = frame["Volume"].reindex(frame.index).fillna(0)
            if not closes:
                raise DataSourceError("批量行情没有返回有效价格。")
            close_frame = pd.concat(closes, axis=1).sort_index().ffill().dropna(how="all")
            volume_frame = pd.concat(volumes, axis=1).reindex(close_frame.index).fillna(0)
            return close_frame, volume_frame

        closes: dict[str, pd.Series] = {}
        volumes: dict[str, pd.Series] = {}
        for raw_symbol, symbol in normalized.items():
            frame = pd.DataFrame()
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    frame = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
                    if not frame.empty:
                        break
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(2**attempt)
            if frame.empty or "Close" not in frame:
                detail = f"：{last_error}" if last_error else ""
                raise DataSourceError(f"{symbol} 暂无可用行情{detail}")
            closes[raw_symbol.upper()] = frame["Close"].dropna()
            volumes[raw_symbol.upper()] = frame["Volume"].reindex(frame.index).fillna(0)
        close_frame = pd.concat(closes, axis=1).sort_index().ffill().dropna(how="all")
        volume_frame = pd.concat(volumes, axis=1).reindex(close_frame.index).fillna(0)
        return close_frame, volume_frame

    def bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        require_market_data_enabled()
        frame = yf.Ticker(self.normalize_symbol(symbol)).history(
            period=period, interval=interval, auto_adjust=False, prepost=False
        )
        required = {"Open", "High", "Low", "Close", "Volume"}
        if frame.empty or not required.issubset(frame.columns):
            raise DataSourceError(f"{symbol} 没有可用的 {interval} 行情。")
        return frame.dropna(subset=["Open", "High", "Low", "Close"])

    def option_chain(self, symbol: str, expiry: str | None = None) -> tuple[str, pd.DataFrame, pd.DataFrame]:
        require_market_data_enabled()
        ticker = yf.Ticker(self.normalize_symbol(symbol))
        expiries = tuple(ticker.options)
        if not expiries:
            raise DataSourceError(f"{symbol} 没有可用的 Yahoo Finance 期权到期日。")
        selected = expiry if expiry in expiries else expiries[0]
        chain = ticker.option_chain(selected)
        return selected, chain.calls.copy(), chain.puts.copy()
