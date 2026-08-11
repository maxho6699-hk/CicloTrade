# -*- coding: utf-8 -*-
"""Futu OpenD adapter for US bars and option-chain analytics."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import os
import re
import socket
from zoneinfo import ZoneInfo

import pandas as pd

from data.datasource import DataSource, DataSourceError, require_market_data_enabled
from data.opend_control import probe_opend_status


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

_OPTION_CONTRACT_PATTERN = re.compile(
    r"US\.[A-Z][A-Z0-9.-]{0,11}\d{6}[CP]\d{6}"
)


class OptionExpiryUnavailableError(DataSourceError):
    """The requested, syntactically valid expiry is absent from OpenD's chain."""


class OpenDAdapter(DataSource):
    name = "Futu OpenD"
    supports_realtime = True
    _US_MARKET_TIMEZONE = ZoneInfo("America/New_York")

    def __init__(self) -> None:
        self.host = os.getenv("OPEND_HOST", "127.0.0.1").strip()
        try:
            self.port = int(os.getenv("OPEND_PORT", "11111"))
        except ValueError as exc:
            raise DataSourceError("OPEND_PORT 配置无效。") from exc
        if not self.host or not 1 <= self.port <= 65535:
            raise DataSourceError("OpenD 主机或端口配置无效。")

    def _context(self):
        status = probe_opend_status(self.host, self.port)
        if not status.ready:
            if status.state in {"verification_required", "phone_verification_required"}:
                phase = "图形验证" if status.state == "verification_required" else "手机验证"
                raise DataSourceError(f"OpenD 正在等待登录或{phase}。")
            raise DataSourceError("OpenD 暂时无法连接。")
        try:
            from futu import OpenQuoteContext
        except ImportError as exc:
            raise DataSourceError("尚未安装 futu-api。") from exc
        try:
            return OpenQuoteContext(host=self.host, port=self.port)
        except Exception as exc:
            raise DataSourceError("无法连接 Futu OpenD。") from exc

    def available(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=0.5):
                pass
            return probe_opend_status(self.host, self.port).ready
        except OSError:
            return False

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
        # OpenD returns US market timestamps without an offset.  Treating
        # those values as the web server's local time shifts candles for every
        # non-US deployment, so attach the exchange timezone before exposing
        # the index to API consumers.
        if getattr(frame["time_key"].dt, "tz", None) is None:
            frame["time_key"] = frame["time_key"].dt.tz_localize(self._US_MARKET_TIMEZONE)
        else:
            frame["time_key"] = frame["time_key"].dt.tz_convert(self._US_MARKET_TIMEZONE)
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

    @staticmethod
    def _snapshot_number(row: pd.Series, column: str) -> float | None:
        try:
            value = float(row.get(column))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    @staticmethod
    def _snapshot_text(row: pd.Series, column: str) -> str | None:
        value = row.get(column)
        if value is None:
            return None
        text = str(value).strip()
        return text if text and text.casefold() not in {"nan", "nat", "<na>"} else None

    @classmethod
    def _us_market_timestamp(cls, value: object) -> str | None:
        """Normalize OpenD's naive US exchange timestamp to ISO-8601."""
        text = str(value or "").strip()
        if not text or text.casefold() in {"nan", "nat", "<na>"}:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=cls._US_MARKET_TIMEZONE)
        return parsed.isoformat()

    @staticmethod
    def _is_realtime_right(value: object) -> bool:
        return str(value or "").strip().upper() in {"LV1", "LV2", "LV3"}

    @staticmethod
    def _unknown_quote_rights() -> dict[str, object]:
        return {
            "us_qot_right": "N/A",
            "us_option_qot_right": "N/A",
            "us_realtime_entitlement": False,
            "us_option_realtime_entitlement": False,
        }

    @staticmethod
    def _quote_rights_from_context(context: object) -> dict[str, object]:
        """Ask OpenD for the account's actual quote entitlements.

        Environment flags only enable a feature; they never prove that the
        logged-in Futu account owns a real-time market-data right.
        """
        from futu import RET_OK, UserInfoField

        ret, data = context.get_user_info([UserInfoField.QOTRIGHT])
        if ret != RET_OK or not isinstance(data, dict):
            raise DataSourceError(f"OpenD 行情权限查询失败：{str(data)[:240]}")
        us_right = str(data.get("us_qot_right") or "N/A").strip().upper()
        option_right = str(data.get("us_option_qot_right") or "N/A").strip().upper()
        return {
            "us_qot_right": us_right,
            "us_option_qot_right": option_right,
            "us_realtime_entitlement": OpenDAdapter._is_realtime_right(us_right),
            "us_option_realtime_entitlement": OpenDAdapter._is_realtime_right(option_right),
        }

    def quote_rights(self) -> dict[str, object]:
        """Read server-side OpenD quote rights without returning user identity."""
        require_market_data_enabled()
        context = self._context()
        try:
            return self._quote_rights_from_context(context)
        finally:
            context.close()

    def stock_quote(self, symbol: str) -> dict[str, object]:
        """Read and normalize one US equity snapshot directly from OpenD."""
        require_market_data_enabled()
        from futu import RET_OK

        code = self._code(symbol)
        context = self._context()
        try:
            try:
                rights = self._quote_rights_from_context(context)
            except (AttributeError, DataSourceError, TypeError, ValueError):
                # A permissions probe must fail closed without discarding an
                # otherwise usable research snapshot from an older OpenD SDK.
                rights = self._unknown_quote_rights()
            ret, snapshot = context.get_market_snapshot([code])
        finally:
            context.close()
        if ret != RET_OK or snapshot.empty:
            raise DataSourceError(f"OpenD 正股快照请求失败：{str(snapshot)[:240]}")
        row = snapshot.iloc[0]
        bid, ask = self._snapshot_number(row, "bid_price"), self._snapshot_number(row, "ask_price")
        last = self._snapshot_number(row, "last_price")
        quote_at = self._us_market_timestamp(self._snapshot_text(row, "update_time"))
        return {
            "symbol": code.removeprefix("US."),
            "last": last,
            "bid": bid,
            "ask": ask,
            "spread": ask - bid if bid is not None and ask is not None else None,
            "open": self._snapshot_number(row, "open_price"),
            "high": self._snapshot_number(row, "high_price"),
            "low": self._snapshot_number(row, "low_price"),
            "prev_close": self._snapshot_number(row, "prev_close_price"),
            "volume": self._snapshot_number(row, "volume"),
            "quote_at": quote_at,
            "source": "OpenD",
            **rights,
            "actionable_snapshot": bool(
                rights["us_realtime_entitlement"]
                and last is not None and bid is not None and ask is not None and quote_at
            ),
        }

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
        """Compatibility wrapper for callers that do not need the expiry picker."""
        selected, _, calls, puts = self.option_chain_with_expiries(symbol, expiry)
        return selected, calls, puts

    def option_chain_with_expiries(
        self, symbol: str, expiry: str | None = None
    ) -> tuple[str, list[str], pd.DataFrame, pd.DataFrame]:
        """Fetch all available expiries and one selected option chain from OpenD."""
        require_market_data_enabled()
        from futu import RET_OK

        context = self._context()
        try:
            # Do not filter at the provider: we must expose every expiry and
            # reject a valid-looking but unavailable request truthfully.
            ret, chain = context.get_option_chain(self._code(symbol))
            if ret != RET_OK or chain.empty:
                raise DataSourceError(f"OpenD 期权链请求失败：{str(chain)[:240]}")
            expiries = sorted(str(value) for value in chain["strike_time"].dropna().unique())
            if not expiries:
                raise DataSourceError("OpenD 没有返回可用的期权到期日。")
            if expiry is not None and expiry not in expiries:
                raise OptionExpiryUnavailableError("请求的期权到期日不在 OpenD 可用列表中。")
            selected = expiry or expiries[0]
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
        return (
            selected,
            expiries,
            renamed.loc[option_type == "CALL", columns].reset_index(drop=True),
            renamed.loc[option_type == "PUT", columns].reset_index(drop=True),
        )

    def option_bars(self, contract_code: str, period: str, interval: str) -> pd.DataFrame:
        """Read historical bars for one US option contract without any fallback feed."""
        require_market_data_enabled()
        code = str(contract_code).strip().upper()
        if not _OPTION_CONTRACT_PATTERN.fullmatch(code):
            raise DataSourceError("OpenD 期权合约代码无效。")
        return self._bars(code, period, interval)
