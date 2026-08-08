# -*- coding: utf-8 -*-
"""行情数据源接口与热切换工厂。"""

from __future__ import annotations

from abc import ABC, abstractmethod
import os

import pandas as pd


class DataSourceError(RuntimeError):
    pass


class DataSource(ABC):
    name = "unknown"
    # Adapters must opt in explicitly once their vendor feed and credentials
    # have been verified.  Historical adapters stay truthful by default.
    supports_realtime = False
    delay_minutes: int | None = None

    def available(self) -> bool:
        return True

    @abstractmethod
    def history(self, symbols: tuple[str, ...], period: str = "3mo", interval: str = "1d") -> tuple[pd.DataFrame, pd.DataFrame]:
        raise NotImplementedError

    def option_chain(self, symbol: str, expiry: str | None = None) -> tuple[str, pd.DataFrame, pd.DataFrame]:
        raise DataSourceError(f"{self.name} 当前不支持期权链。")

    def search(self, query: str, market: str = "美股", max_results: int = 8) -> list[dict[str, str]]:
        raise DataSourceError(f"{self.name} 当前不支持证券搜索。")

    def bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        raise DataSourceError(f"{self.name} 当前不支持 {interval} K 线。")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def require_market_data_enabled() -> None:
    if not _env_flag("MARKET_DATA_ENABLED"):
        raise DataSourceError("行情資料模組已停用；僅能由平台管理員啟用外部行情。")


def market_data_status(name: str | None = None) -> dict[str, object]:
    """Describe freshness from the selected adapter without making a request.

    A provider is shown as real-time only when both the adapter and deployment
    explicitly opt in.  This keeps UI copy correct while a commercial feed is
    being wired in and avoids treating a fast page refresh as live data.
    """
    source = get_data_source(name)
    if not _env_flag("MARKET_DATA_ENABLED"):
        return {
            "source": source.name,
            "display_source": "市场数据",
            "is_realtime": False,
            "freshness": "已停用",
            "detail": "外部行情访问已由平台关闭",
        }
    try:
        available = bool(getattr(source, "available", lambda: True)())
    except Exception:
        available = False
    if not available:
        fallback = get_data_source("yfinance")
        return {
            "source": source.name,
            "active_source": fallback.name,
            "display_source": "美国延迟市场数据",
            "is_realtime": False,
            "freshness": f"约 {fallback.delay_minutes} 分钟延迟",
            "detail": "实时通道暂不可用 · 已自动使用延迟行情",
        }
    realtime = bool(source.supports_realtime and _env_flag("MARKET_DATA_REALTIME"))
    if realtime:
        return {
            "source": source.name,
            "display_source": "美国实时市场数据",
            "is_realtime": True,
            "freshness": "实时",
            "detail": "授权实时行情 · 页面不主动延迟",
        }
    if source.delay_minutes:
        freshness = f"约 {source.delay_minutes} 分钟延迟"
        detail = "研究级行情 · 供应商可能延迟"
    else:
        freshness = "历史行情"
        detail = "当前适配器未声明实时能力"
    return {
        "source": source.name,
        "display_source": "美国延迟市场数据" if source.delay_minutes else "历史研究数据",
        "is_realtime": False,
        "freshness": freshness,
        "detail": detail,
    }


def public_market_status(name: str | None = None, market: str = "美股") -> dict[str, object]:
    """Return truthful customer-facing freshness without vendor disclosure."""
    status = dict(market_data_status(name))
    if market == "A股":
        status["display_source"] = "A 股日线市场数据"
    return status


def get_data_source(name: str | None = None) -> DataSource:
    selected = (name or os.getenv("DATA_SOURCE", "yfinance")).lower()
    if selected == "yfinance":
        from data.yfinance_adapter import YFinanceAdapter

        return YFinanceAdapter()
    if selected == "polygon":
        from data.polygon_adapter import PolygonAdapter

        return PolygonAdapter()
    if selected in {"opend", "futu"}:
        from data.opend_adapter import OpenDAdapter

        return OpenDAdapter()
    if selected == "wrdata":
        from data.wrdata_adapter import WrdataAdapter

        return WrdataAdapter()
    raise DataSourceError(f"不支持的数据源：{selected}")


def get_resilient_data_source(name: str | None = None) -> DataSource:
    """Use the configured source when healthy, otherwise real delayed history."""
    source = get_data_source(name)
    try:
        if source.available():
            return source
    except Exception:
        pass
    return get_data_source("yfinance")
