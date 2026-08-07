# -*- coding: utf-8 -*-
"""持久化、多条件价格预警与统一权限校验。"""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
import json
import math
import re
from typing import Any

from core.database import DatabaseManager, get_database
from core.plans import alert_limit, effective_plan


CONDITION_TYPES = {"price", "volume", "volume_ratio", "rsi", "macd", "ma", "change"}
OPERATORS = {">=", "<=", ">", "<", "="}


def _condition_type(value: str) -> str:
    aliases = {
        "价格": "price", "成交量": "volume", "成交量比": "volume_ratio",
        "量比": "volume_ratio", "涨跌幅": "change", "變動": "change",
        "均线": "ma", "均線": "ma", "MACD": "macd",
    }
    return aliases.get(str(value).strip(), str(value).strip().lower())


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("预警数值必须是有限数字。")
    return result


def normalize_conditions(
    conditions: list[dict[str, Any]] | None = None,
    *,
    operator: str | None = None,
    target: float | None = None,
) -> list[dict[str, Any]]:
    """Normalize legacy price arguments and validate the persisted shape."""
    if conditions is None:
        if operator is None or target is None:
            raise ValueError("至少需要一条预警条件。")
        conditions = [{"type": "price", "operator": operator, "value": target}]
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("至少需要一条预警条件。")
    normalized: list[dict[str, Any]] = []
    for raw in conditions:
        if not isinstance(raw, dict):
            raise ValueError("预警条件格式无效。")
        kind = _condition_type(raw.get("type", ""))
        if kind not in CONDITION_TYPES:
            raise ValueError(f"不支持的预警条件：{raw.get('type')}")
        item: dict[str, Any] = {"type": kind}
        if kind in {"macd", "ma"}:
            value = str(raw.get("value", raw.get("signal", ""))).strip().lower()
            allowed = {"golden_cross", "death_cross"} if kind == "macd" else {"ma20_breakout", "ma50_breakout"}
            if value not in allowed:
                raise ValueError(f"{kind} 条件选项无效。")
            item["value"] = value
        else:
            op = str(raw.get("operator", "")).strip()
            if op not in OPERATORS:
                raise ValueError("预警运算符无效。")
            value = _finite(raw.get("value", raw.get("target")))
            if kind == "price" and value <= 0:
                raise ValueError("目标价格必须大于 0。")
            if kind in {"volume", "volume_ratio"} and value < 0:
                raise ValueError("成交量与量比不能为负数。")
            if kind == "rsi" and not 0 <= value <= 100:
                raise ValueError("RSI 必须介于 0 与 100 之间。")
            item.update(operator=op, value=value)
        normalized.append(item)
    return normalized


def _compare(actual: float, operator: str, expected: float) -> bool:
    return {
        ">=": actual >= expected,
        "<=": actual <= expected,
        ">": actual > expected,
        "<": actual < expected,
        "=": math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9),
    }[operator]


def condition_preview(symbol: str, conditions: list[dict[str, Any]], logic: str = "AND") -> str:
    labels = {
        "price": "价格", "volume": "成交量", "volume_ratio": "量比", "rsi": "RSI",
        "change": "涨跌幅", "macd": "MACD", "ma": "均线",
    }
    bits = []
    for item in conditions:
        kind = item["type"]
        value = item.get("value")
        if kind in {"macd", "ma"}:
            text = {"golden_cross": "金叉", "death_cross": "死叉", "ma20_breakout": "突破 MA20", "ma50_breakout": "突破 MA50"}.get(value, str(value))
        else:
            text = f"{item.get('operator', '')} {float(value):g}"
        bits.append(f"{labels.get(kind, kind)} {text}")
    connector = f" {str(logic).upper()} "
    return f"{symbol.upper()} {connector.join(bits)}"


class AlertService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def _user_plan(self, user_id: int, supplied: str | None = None) -> str:
        user = self.db.fetch_one(
            "SELECT plan_type,subscription_expire FROM users WHERE id=? AND is_active=1", (user_id,)
        )
        if not user:
            raise ValueError("用户不存在或已停用。")
        # Keep the argument for old callers, but never trust it for authorization.
        return effective_plan(user)

    def create(
        self,
        user_id: int,
        plan: str | None = None,
        symbol: str | None = None,
        operator: str | None = None,
        target: float | None = None,
        *,
        conditions: list[dict[str, Any]] | None = None,
        logic: str = "AND",
    ) -> None:
        symbol = str(symbol or "").strip().upper()
        valid_symbol = bool(re.fullmatch(r"(?:[A-Z][A-Z0-9.-]{0,11}|\d{6})", symbol))
        if not valid_symbol:
            raise ValueError("标的代码无效。")
        logic = str(logic).upper().strip()
        if logic not in {"AND", "OR"}:
            raise ValueError("条件逻辑只能是 AND 或 OR。")
        normalized = normalize_conditions(conditions, operator=operator, target=target)
        actual_plan = self._user_plan(user_id, plan)
        limit = alert_limit(actual_plan)
        max_conditions = 1 if actual_plan == "免费版" else 3 if actual_plan == "标准版" else 5
        if len(normalized) > max_conditions:
            raise ValueError(f"{actual_plan}每条预警最多 {max_conditions} 个条件。")
        if actual_plan == "免费版" and normalized[0]["type"] != "price":
            raise ValueError("免费版仅支持价格单条件预警。")
        first = normalized[0]
        now = datetime.now(UTC).isoformat(timespec="seconds")
        payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        with self.db.transaction() as conn:
            count = conn.execute(
                "SELECT COUNT(*) count FROM price_alerts WHERE user_id=? AND is_active=1", (user_id,)
            ).fetchone()["count"]
            if limit is not None and int(count) >= limit:
                raise ValueError(f"{actual_plan}最多可启用 {limit} 条预警，请先停用旧预警或升级方案。")
            conn.execute(
                """INSERT INTO price_alerts
                   (user_id,symbol,operator,target_price,conditions,logic,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (user_id, symbol, first.get("operator", "="), float(first.get("value", 0) or 0), payload, logic, now),
            )
            conn.execute(
                "INSERT INTO strategy_action_logs (user_id,strategy_name,action,params,result,created_at) VALUES (?,?,?,?,?,?)",
                (user_id, "价格预警", "ALERT_CREATE", json.dumps({"symbol": symbol, "conditions": normalized, "logic": logic}, ensure_ascii=False), "success", now),
            )

    def list(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.db.fetch_all("SELECT * FROM price_alerts WHERE user_id=? ORDER BY created_at DESC", (user_id,))
        for row in rows:
            row["symbol"] = str(row.get("symbol") or "").upper()
            try:
                row["conditions_list"] = normalize_conditions(json.loads(row.get("conditions") or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                row["conditions_list"] = normalize_conditions(operator=row["operator"], target=row["target_price"])
            logic = str(row.get("logic") or "AND").upper()
            row["logic"] = logic if logic in {"AND", "OR"} else "AND"
            row["preview"] = condition_preview(row["symbol"], row["conditions_list"], row["logic"])
        return rows

    def deactivate(self, user_id: int, alert_id: int) -> None:
        self.db.execute("UPDATE price_alerts SET is_active=0 WHERE id=? AND user_id=?", (alert_id, user_id))

    def evaluate(
        self,
        user_id: int,
        prices: dict[str, float],
        metrics: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        prices = {str(symbol).upper(): value for symbol, value in prices.items()}
        metrics = {str(symbol).upper(): value for symbol, value in (metrics or {}).items() if isinstance(value, dict)}
        triggered = []
        for alert in self.list(user_id):
            if not alert["is_active"] or alert["symbol"] not in prices:
                continue
            try:
                current_price = _finite(prices[alert["symbol"]])
            except (TypeError, ValueError):
                continue
            values = {**metrics.get(alert["symbol"], {}), "price": current_price}
            checks = []
            for item in alert["conditions_list"]:
                kind = item["type"]
                if kind in {"macd", "ma"}:
                    checks.append(bool(values.get(item["value"], values.get(f"{kind}_{item['value']}", False))))
                elif kind in values:
                    try:
                        checks.append(_compare(_finite(values[kind]), item["operator"], float(item["value"])))
                    except (TypeError, ValueError):
                        checks.append(False)
                else:
                    checks.append(False)
            hit = all(checks) if (alert.get("logic") or "AND") == "AND" else any(checks)
            if hit:
                now = datetime.now(UTC).isoformat(timespec="seconds")
                content = f"{alert['preview']}，当前价格 {current_price:.2f}"
                with self.db.transaction() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    updated = conn.execute(
                        "UPDATE price_alerts SET is_active=0,last_triggered=? WHERE id=? AND is_active=1",
                        (now, alert["id"]),
                    ).rowcount
                    if updated:
                        notification_id = conn.execute(
                            """INSERT INTO notifications(msg_type,title,content,push_status,created_at)
                               VALUES ('PRICE_ALERT','价格预警已触发',?,'pending',?)""",
                            (content, now),
                        ).lastrowid
                        conn.execute(
                            """INSERT OR IGNORE INTO price_alert_deliveries
                               (alert_id,user_id,notification_id,status,attempts,next_attempt_at,created_at,updated_at)
                               VALUES (?,?,?,'pending',0,?,?,?)""",
                            (alert["id"], user_id, notification_id, now, now, now),
                        )
                if updated:
                    alert["current_price"] = current_price
                    triggered.append(alert)
        return triggered
