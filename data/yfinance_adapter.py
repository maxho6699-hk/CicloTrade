# -*- coding: utf-8 -*-
"""Yahoo Finance 免费行情适配器。"""

from __future__ import annotations

import re
import time

import pandas as pd
import yfinance as yf

from data.datasource import DataSource, DataSourceError, require_market_data_enabled


class YahooOptionExpiryUnavailableError(DataSourceError):
    """A syntactically valid expiry is absent from Yahoo's current chain."""


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

    def stock_quote(self, symbol: str) -> dict[str, object]:
        """Return a delayed research snapshot, never an executable bid/ask quote."""
        require_market_data_enabled()
        normalized = self.normalize_symbol(symbol)
        try:
            frame = yf.Ticker(normalized).history(period="5d", interval="1d", auto_adjust=False, prepost=False)
        except Exception as exc:
            raise DataSourceError(f"Yahoo Finance 研究报价暂时不可用：{exc}") from exc
        required = {"Open", "High", "Low", "Close", "Volume"}
        if frame.empty or not required.issubset(frame.columns):
            raise DataSourceError(f"{normalized} 没有可用的 Yahoo Finance 研究报价。")
        row = frame.dropna(subset=["Close"]).iloc[-1]
        timestamp = frame.dropna(subset=["Close"]).index[-1]
        quote_at = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        previous = frame["Close"].dropna()
        return {
            "symbol": symbol.strip().upper(),
            "last": float(row["Close"]),
            "bid": None,
            "ask": None,
            "spread": None,
            "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
            "high": float(row["High"]) if pd.notna(row["High"]) else None,
            "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
            "prev_close": float(previous.iloc[-2]) if len(previous) >= 2 else None,
            "volume": float(row["Volume"]) if pd.notna(row["Volume"]) else None,
            "quote_at": quote_at,
            "source": self.name,
            "is_realtime": False,
            "actionable_quote": False,
            "freshness": f"约 {self.delay_minutes} 分钟延迟的研究报价",
            "verification": "delayed_research_quote",
        }

    def option_chain_with_expiries(
        self, symbol: str, expiry: str | None = None
    ) -> tuple[str, list[str], pd.DataFrame, pd.DataFrame]:
        """Return an exact Yahoo expiry for delayed professional research."""
        require_market_data_enabled()
        normalized = self.normalize_symbol(symbol)
        try:
            ticker = yf.Ticker(normalized)
            expiries = [str(item) for item in ticker.options]
        except Exception as exc:
            raise DataSourceError(f"Yahoo Finance 期权到期日暂时不可用：{exc}") from exc
        if not expiries:
            raise DataSourceError(f"{symbol} 没有可用的 Yahoo Finance 期权到期日。")
        if expiry is not None and expiry not in expiries:
            raise YahooOptionExpiryUnavailableError("请求的期权到期日不在 Yahoo Finance 可用列表中。")
        selected = expiry or expiries[0]
        try:
            chain = ticker.option_chain(selected)
        except Exception as exc:
            raise DataSourceError(f"Yahoo Finance 期权链暂时不可用：{exc}") from exc
        return selected, expiries, chain.calls.copy(), chain.puts.copy()

    def option_chain(self, symbol: str, expiry: str | None = None) -> tuple[str, pd.DataFrame, pd.DataFrame]:
        selected, _, calls, puts = self.option_chain_with_expiries(symbol, expiry)
        return selected, calls, puts

    def option_bars(self, contract_code: str, period: str, interval: str) -> pd.DataFrame:
        """Read delayed Yahoo bars only for an exact Yahoo contract symbol."""
        require_market_data_enabled()
        code = str(contract_code).strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}\d{6}[CP]\d{8}", code):
            raise DataSourceError("Yahoo Finance 期权合约代码无效。")
        try:
            frame = yf.Ticker(code).history(
                period=period, interval=interval, auto_adjust=False, prepost=False
            )
        except Exception as exc:
            raise DataSourceError(f"Yahoo Finance 期权 K 线暂时不可用：{exc}") from exc
        required = {"Open", "High", "Low", "Close", "Volume"}
        if frame.empty or not required.issubset(frame.columns):
            raise DataSourceError(f"{code} 没有可用的 Yahoo Finance 期权 K 线。")
        return frame.dropna(subset=["Open", "High", "Low", "Close"])
