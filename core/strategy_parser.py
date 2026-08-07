# -*- coding: utf-8 -*-
"""Natural-language strategy input to a constrained, auditable rule DSL."""

from __future__ import annotations

import json
import os
import re
from urllib import error, request


SYMBOL_ALIASES = {"蘋果": "AAPL", "苹果": "AAPL", "特斯拉": "TSLA", "英偉達": "NVDA", "英伟达": "NVDA"}
FUTURE_TERMS = ("未來價格", "未来价格", "明日收盤價", "明日收盘价", "下一根收盤價", "下一根收盘价")


def _symbol(text: str) -> str:
    for name, symbol in SYMBOL_ALIASES.items():
        if name in text:
            return symbol
    matches = re.findall(r"(?<![A-Z])([A-Z]{1,5}(?:\.[A-Z])?|\d{6})(?![A-Z])", text.upper())
    return matches[0] if matches else ""


def _rules(clause: str) -> list[dict]:
    rules: list[dict] = []
    for direction, period in re.findall(r"(突破|上穿|跌破|下穿)\s*(\d{1,3})\s*日?(?:均線|均线|MA)", clause, re.I):
        rules.append({"indicator": "price", "operator": "cross_above_ma" if direction in {"突破", "上穿"} else "cross_below_ma", "period": int(period)})
    rsi_pattern = re.compile(r"RSI(?:\s*\(?(?P<period>\d{1,2})\)?)?\s*(?P<op>低於|低于|小於|小于|高於|高于|大於|大于|<|>)\s*(?P<value>\d+(?:\.\d+)?)", re.I)
    for match in rsi_pattern.finditer(clause):
        op = match.group("op")
        rules.append({"indicator": "rsi", "operator": "lt" if op in {"低於", "低于", "小於", "小于", "<"} else "gt", "period": int(match.group("period") or 14), "value": float(match.group("value"))})
    return rules


def _local_parse(text: str) -> dict:
    symbol = _symbol(text)
    if not symbol:
        raise ValueError("請在策略描述中提供美股代碼或 6 位 A 股代碼。")
    entries: list[dict] = []
    exits: list[dict] = []
    for clause in re.split(r"[，,；;。]", text):
        found = _rules(clause)
        if not found:
            continue
        if any(word in clause for word in ("賣出", "卖出", "止損", "止损", "平倉", "平仓")):
            exits.extend(found)
        else:
            entries.extend(found)
    if not entries or not exits:
        raise ValueError("策略必須同時包含可識別的買入與賣出條件。")
    return {
        "symbol": symbol,
        "market": "CN" if symbol.isdigit() else "US",
        "entry": entries,
        "exit": exits,
        "condition_count": len(entries) + len(exits),
    }


def _remote_parse(text: str) -> dict | None:
    endpoint = os.getenv("STRATEGY_LLM_ENDPOINT", "").strip()
    if not endpoint:
        return None
    token = os.getenv("STRATEGY_LLM_API_KEY", "").strip()
    timeout = min(max(float(os.getenv("STRATEGY_LLM_TIMEOUT", "12")), 1), 30)
    payload = json.dumps({"text": text, "schema": "ciclotrade.strategy.v1"}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with request.urlopen(request.Request(endpoint, data=payload, headers=headers, method="POST"), timeout=timeout) as response:
            parsed = json.loads(response.read(65_536).decode("utf-8"))
    except (error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("策略解析服務暫時不可用，請稍後再試。") from exc
    return parsed if isinstance(parsed, dict) else None


def parse_strategy(text: str, *, max_conditions: int = 20, use_remote: bool = True) -> dict:
    cleaned = " ".join(str(text).split())
    if not 12 <= len(cleaned) <= 1_000:
        raise ValueError("策略描述長度必須介於 12 與 1,000 個字元。")
    if any(term in cleaned for term in FUTURE_TERMS):
        raise ValueError("策略包含未來資料條件，無法進行有效回測。")
    parsed = _remote_parse(cleaned) if use_remote else None
    if parsed is None:
        parsed = _local_parse(cleaned)
    required = {"symbol", "market", "entry", "exit"}
    if not required.issubset(parsed):
        raise ValueError("策略解析結果缺少必要欄位。")
    # Remote output is normalized through the same deterministic parser until a
    # separately versioned DSL validator is configured.
    if use_remote and os.getenv("STRATEGY_LLM_ENDPOINT"):
        local = _local_parse(cleaned)
        parsed = {**local, "llm_preview": parsed}
    count = len(parsed["entry"]) + len(parsed["exit"])
    if max(len(parsed["entry"]), len(parsed["exit"])) > max_conditions:
        raise PermissionError(f"目前會員等級每個買入或賣出階段最多支援 {max_conditions} 個條件。")
    parsed["condition_count"] = count
    parsed["source_text"] = cleaned
    parsed["execution_timing"] = "next_bar_open"
    parsed["disclaimer"] = "基於歷史數據回測，不代表未來表現"
    return parsed
