# -*- coding: utf-8 -*-
"""wrdata 可选适配器；仅在供应商 SDK 存在时启用。"""

from __future__ import annotations

import pandas as pd

from data.datasource import DataSource, DataSourceError


class WrdataAdapter(DataSource):
    name = "wrdata"

    def history(self, symbols: tuple[str, ...], period: str = "3mo", interval: str = "1d") -> tuple[pd.DataFrame, pd.DataFrame]:
        del symbols, period, interval
        try:
            import wrdata  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise DataSourceError("本机未安装可验证的 wrdata 官方 SDK，请改用 yfinance 或配置 Polygon。") from exc
        raise DataSourceError("wrdata 账户接口尚未完成供应商凭证联调，已安全停用。")
