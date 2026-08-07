# -*- coding: utf-8 -*-
"""从真实价格生成可复制的研究摘要，不自动下单。"""

from __future__ import annotations

import pandas as pd


def generate_signal(strategy: str, symbol: str, closes: pd.Series) -> dict[str, float | str]:
    series = closes.dropna()
    if len(series) < 20:
        raise ValueError("至少需要 20 个交易日才能生成研究摘要。")
    entry = float(series.iloc[-1])
    trend = float(series.tail(5).mean() - series.tail(20).mean())
    bearish = strategy in {"买入 Put", "熊市价差", "现金担保看跌"}
    direction = "看跌" if bearish else "看涨" if trend >= 0 else "观望"
    stop = entry * (1.06 if bearish else 0.94)
    target = entry * (0.88 if bearish else 1.12)
    return {
        "strategy": strategy,
        "symbol": symbol.upper(),
        "direction": direction,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "disclaimer": "研究摘要仅供参考，不构成投资建议或自动交易指令。",
    }
