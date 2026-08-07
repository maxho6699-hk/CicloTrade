# -*- coding: utf-8 -*-
"""Generate reviewable Backtrader source from the constrained strategy DSL."""

from __future__ import annotations

import ast
import json
from datetime import datetime
from core.compat import UTC

from core.database import DatabaseManager, get_database


def _indicator(rule: dict, name: str) -> tuple[str, str]:
    indicator, operator = rule.get("indicator"), rule.get("operator")
    if indicator == "price" and operator in {"cross_above_ma", "cross_below_ma"}:
        period = int(rule.get("period", 0))
        if not 2 <= period <= 500:
            raise ValueError("均線週期必須介於 2 與 500。")
        declaration = f"self.{name} = bt.ind.CrossOver(self.data.close, bt.ind.SMA(self.data.close, period={period}))"
        expression = f"self.{name}[0] {'>' if operator == 'cross_above_ma' else '<'} 0"
        return declaration, expression
    if indicator == "rsi" and operator in {"lt", "gt"}:
        period = int(rule.get("period", 14))
        value = float(rule.get("value"))
        if not 2 <= period <= 100 or not 0 <= value <= 100:
            raise ValueError("RSI 週期或閾值超出範圍。")
        declaration = f"self.{name} = bt.ind.RSI(self.data.close, period={period})"
        expression = f"self.{name}[0] {'<' if operator == 'lt' else '>'} {value!r}"
        return declaration, expression
    raise ValueError("策略包含尚未支援的指標或運算子。")


def generate_backtrader(parsed: dict) -> str:
    declarations: list[str] = []
    entry_expressions: list[str] = []
    exit_expressions: list[str] = []
    for side, target in (("entry", entry_expressions), ("exit", exit_expressions)):
        rules = parsed.get(side)
        if not isinstance(rules, list) or not rules:
            raise ValueError("策略必須同時包含買入與賣出規則。")
        for index, rule in enumerate(rules):
            declaration, expression = _indicator(rule, f"{side}_{index}")
            declarations.append(f"        {declaration}")
            target.append(expression)
    source = "\n".join(
        [
            "import backtrader as bt",
            "",
            "",
            "class GeneratedStrategy(bt.Strategy):",
            "    # Market orders submitted in next() execute on the next bar by default.",
            "    def __init__(self):",
            *declarations,
            "",
            "    def next(self):",
            f"        enter = {' and '.join(entry_expressions)}",
            f"        exit_trade = {' and '.join(exit_expressions)}",
            "        if not self.position and enter:",
            "            self.buy()",
            "        elif self.position and exit_trade:",
            "            self.close()",
            "",
        ]
    )
    compile(source, "<generated_strategy>", "exec")
    return source


def validate_generated_code(source: str) -> None:
    tree = ast.parse(source, mode="exec")
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    if any(
        (isinstance(node, ast.Import) and any(alias.name != "backtrader" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module != "backtrader")
        for node in imports
    ):
        raise ValueError("生成代碼包含未允許的模組。")


class StrategyGenerationService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def save(self, user_id: int, description: str, parsed: dict) -> dict:
        source = generate_backtrader(parsed)
        validate_generated_code(source)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO strategy_generations
                   (user_id,description,parsed_json,generated_code,status,error_message,created_at)
                   VALUES (?,?,?,?, 'generated',NULL,?)""",
                (user_id, description, json.dumps(parsed, ensure_ascii=False), source, now),
            )
            generation_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO strategy_action_logs(user_id,strategy_name,action,params,result,created_at) VALUES (?,?,?,?,?,?)",
                (user_id, "一句話策略", "GENERATE", json.dumps({"generation_id": generation_id}, ensure_ascii=False), "success", now),
            )
        return {"id": generation_id, "parsed": parsed, "code": source, "status": "generated"}
