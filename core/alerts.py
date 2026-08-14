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
from core.entitlement_consumer import verified_can
from core.membership import authoritative_membership_user
from core.plans import alert_limit, effective_plan


CONDITION_TYPES = {"price", "volume", "volume_ratio", "rsi", "macd", "ma", "change"}
OPERATORS = {">=", "<=", ">", "<", "="}
TRIGGER_MODES = {"at_or_above", "at_or_below", "crosses_above", "crosses_below"}
REPEAT_MODES = {"once", "repeat"}
ALERT_CHANNELS = {"website", "telegram"}
ALERT_MARKETS = {"US", "CN"}
_US_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,11}")
_CN_SYMBOL = re.compile(r"\d{6}")


def normalize_alert_instrument(symbol: Any, market: Any = None) -> tuple[str, str]:
    """Return the canonical (market, symbol) pair for persisted alerts."""
    if not isinstance(symbol, str):
        raise ValueError("预警标的代码必须是字符串。")
    normalized_symbol = symbol.strip().upper()
    if market is None:
        normalized_market = "CN" if _CN_SYMBOL.fullmatch(normalized_symbol) else "US"
    elif not isinstance(market, str):
        raise ValueError("预警市场必须是 US 或 CN。")
    else:
        normalized_market = market.strip().upper()
    if normalized_market not in ALERT_MARKETS:
        raise ValueError("预警市场必须是 US 或 CN。")
    if normalized_market == "CN" and normalized_symbol.endswith((".SS", ".SZ")):
        normalized_symbol = normalized_symbol[:-3]
    pattern = _CN_SYMBOL if normalized_market == "CN" else _US_SYMBOL
    if not pattern.fullmatch(normalized_symbol):
        expected = "6 位数字" if normalized_market == "CN" else "美股 ticker"
        raise ValueError(f"{normalized_market} 市场标的代码无效，应为{expected}。")
    return normalized_market, normalized_symbol


def _market_value_map(values: dict[Any, Any] | None) -> dict[tuple[str, str], Any]:
    """Normalize legacy SYMBOL and unambiguous MARKET:SYMBOL evaluation keys."""
    normalized: dict[tuple[str, str], Any] = {}
    for raw_key, value in (values or {}).items():
        key = str(raw_key).strip().upper()
        if ":" in key:
            raw_market, raw_symbol = key.split(":", 1)
            try:
                market, symbol = normalize_alert_instrument(raw_symbol, raw_market)
            except ValueError:
                continue
            normalized[(market, symbol)] = value
            continue
        try:
            inferred_market, symbol = normalize_alert_instrument(key)
        except ValueError:
            continue
        # Bare legacy keys are safe because the canonical CN and US symbol
        # formats are disjoint; an explicit composite key still takes priority.
        normalized.setdefault((inferred_market, symbol), value)
    return normalized


def _metadata_table(database: DatabaseManager) -> None:
    """Create the optional alert metadata table for old and new databases."""
    database.execute(
        """CREATE TABLE IF NOT EXISTS price_alert_metadata (
               alert_id INTEGER PRIMARY KEY,
               trigger_mode TEXT NOT NULL DEFAULT 'at_or_above',
               repeat_mode TEXT NOT NULL DEFAULT 'once',
               expires_at TEXT,
               channels TEXT NOT NULL DEFAULT '[\"website\"]',
               notify_only INTEGER NOT NULL DEFAULT 1,
               last_state INTEGER NOT NULL DEFAULT 0,
               has_observation INTEGER NOT NULL DEFAULT 0,
               last_value REAL,
               FOREIGN KEY (alert_id) REFERENCES price_alerts(id)
           )"""
    )
    columns = {str(row["name"]) for row in database.fetch_all("PRAGMA table_info(price_alert_metadata)")}
    if "has_observation" not in columns:
        database.execute("ALTER TABLE price_alert_metadata ADD COLUMN has_observation INTEGER NOT NULL DEFAULT 0")
    if "last_value" not in columns:
        database.execute("ALTER TABLE price_alert_metadata ADD COLUMN last_value REAL")


def _normalize_trigger(value: Any, *, operator: str | None = None) -> str:
    aliases = {
        "达到": "at_or_above", "到达": "at_or_above", "at": "at_or_above",
        "at_or_above": "at_or_above", "above": "at_or_above", ">=": "at_or_above", ">": "at_or_above", "=": "at_or_above",
        "跌破": "at_or_below", "低于": "at_or_below", "at_or_below": "at_or_below",
        "below": "at_or_below", "<=": "at_or_below", "<": "at_or_below",
        "上穿": "crosses_above", "上破": "crosses_above", "crosses_above": "crosses_above",
        "cross_above": "crosses_above", "cross_up": "crosses_above",
        "下穿": "crosses_below", "下破": "crosses_below", "crosses_below": "crosses_below",
        "cross_below": "crosses_below", "cross_down": "crosses_below",
    }
    if value is None or str(value).strip() == "":
        value = operator
    key = str(value).strip().lower()
    normalized = aliases.get(key)
    if normalized is None:
        raise ValueError("预警触发方式无效。可选：达到、跌破、上穿或下穿。")
    return normalized


def _normalize_repeat(value: Any) -> str:
    aliases = {"一次": "once", "单次": "once", "once": "once", "repeat": "repeat", "重复": "repeat", "持续": "repeat"}
    normalized = aliases.get(str(value if value is not None else "once").strip().lower())
    if normalized is None:
        raise ValueError("预警重复方式无效。可选：一次或重复。")
    return normalized


def _normalize_channels(value: Any) -> list[str]:
    if value is None:
        value = ["website"]
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list) or not value:
        raise ValueError("通知渠道至少需要选择网站或 Telegram。")
    aliases = {"web": "website", "site": "website", "网站": "website", "telegram": "telegram", "tg": "telegram", "电报": "telegram"}
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("通知渠道必须是 website 或 telegram。")
        channel = aliases.get(item.strip().lower(), item.strip().lower())
        if channel not in ALERT_CHANNELS:
            raise ValueError("通知渠道只能是 website 或 telegram。")
        if channel not in result:
            result.append(channel)
    return [channel for channel in ("website", "telegram") if channel in result]


def _normalize_expiry(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("预警有效期必须是 ISO 日期时间。")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("预警有效期必须是有效的 ISO 日期时间。") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed <= datetime.now(UTC):
        raise ValueError("预警有效期必须晚于现在。")
    return parsed.isoformat(timespec="seconds")


def normalize_alert_metadata(
    *, trigger_mode: Any = None, repeat_mode: Any = "once", expires_at: Any = None,
    channels: Any = None, operator: str | None = None,
) -> dict[str, Any]:
    return {
        "trigger_mode": _normalize_trigger(trigger_mode, operator=operator),
        "repeat_mode": _normalize_repeat(repeat_mode),
        "expires_at": _normalize_expiry(expires_at),
        "channels": _normalize_channels(channels),
        "notify_only": True,
    }


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
        _metadata_table(self.db)

    def _user_plan(self, user_id: int, supplied: str | None = None) -> str:
        user = self.db.fetch_one(
            "SELECT plan_type,subscription_expire FROM users WHERE id=? AND is_active=1", (user_id,)
        )
        if not user:
            raise ValueError("用户不存在或已停用。")
        # Keep the argument for old callers, but never trust it for authorization.
        return effective_plan(authoritative_membership_user(self.db, user))

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
        trigger_mode: str | None = None,
        repeat_mode: str = "once",
        expires_at: str | None = None,
        channels: list[str] | None = None,
        market: str | None = None,
    ) -> None:
        market, symbol = normalize_alert_instrument(symbol, market)
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
        metadata = normalize_alert_metadata(
            trigger_mode=trigger_mode,
            repeat_mode=repeat_mode,
            expires_at=expires_at,
            channels=channels,
            operator=first.get("operator"),
        )
        # Legacy callers had no channel field. Preserve the historical delivery
        # pipeline; dispatch still applies membership and consent at send time.
        if channels is None:
            metadata["channels"] = ["website", "telegram"]
        if metadata["trigger_mode"].startswith("crosses_") and not any(
            item.get("type") == "price" for item in normalized
        ):
            raise ValueError("上穿或下穿只适用于价格条件。")
        if "telegram" in metadata["channels"]:
            with self.db.transaction() as conn:
                if not verified_can(conn, actual_plan, "tg_stock_signal"):
                    raise ValueError("当前会员策略不能使用 Telegram 价格预警，请升级后再选择该渠道。")
        if first.get("type") == "price":
            if metadata["trigger_mode"] in {"at_or_above", "crosses_above"} and first.get("operator") not in {">=", ">", "="}:
                raise ValueError("达到或上穿预警必须使用 >= 或 > 条件。")
            if metadata["trigger_mode"] in {"at_or_below", "crosses_below"} and first.get("operator") not in {"<=", "<", "="}:
                raise ValueError("跌破或下穿预警必须使用 <= 或 < 条件。")
        now = datetime.now(UTC).isoformat(timespec="seconds")
        payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        metadata_channels = json.dumps(metadata["channels"], ensure_ascii=False, separators=(",", ":"))
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute(
                """SELECT a.*,m.trigger_mode,m.repeat_mode,m.expires_at,m.channels,m.notify_only
                   FROM price_alerts a LEFT JOIN price_alert_metadata m ON m.alert_id=a.id
                   WHERE a.user_id=? AND a.is_active=1""", (user_id,)
            ).fetchall()
            active_rows = []
            for row in count:
                expiry = row["expires_at"]
                if expiry:
                    try:
                        when = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=UTC)
                        if when <= datetime.now(UTC):
                            continue
                    except ValueError:
                        continue
                active_rows.append(row)
            count_value = len(active_rows)
            canonical_conditions = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in active_rows:
                try:
                    existing_conditions = normalize_conditions(json.loads(row["conditions"] or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    existing_conditions = normalize_conditions(operator=row["operator"], target=row["target_price"])
                existing_metadata = normalize_alert_metadata(
                    trigger_mode=row["trigger_mode"], repeat_mode=row["repeat_mode"],
                    expires_at=row["expires_at"], channels=json.loads(row["channels"] or "[\"website\",\"telegram\"]"),
                    operator=existing_conditions[0].get("operator"),
                )
                if (
                    str(row["market"] or "US").upper() == market
                    and str(row["symbol"]).upper() == symbol
                    and str(row["logic"] or "AND").upper() == logic
                    and json.dumps(existing_conditions, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == canonical_conditions
                    and existing_metadata["trigger_mode"] == metadata["trigger_mode"]
                    and existing_metadata["repeat_mode"] == metadata["repeat_mode"]
                    and existing_metadata["expires_at"] == metadata["expires_at"]
                    and existing_metadata["channels"] == metadata["channels"]
                ):
                    return
            if limit is not None and count_value >= limit:
                raise ValueError(f"{actual_plan}最多可启用 {limit} 条预警，请先停用旧预警或升级方案。")
            cursor = conn.execute(
                """INSERT INTO price_alerts
                   (user_id,market,symbol,operator,target_price,conditions,logic,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (user_id, market, symbol, first.get("operator", "="), float(first.get("value", 0) or 0), payload, logic, now),
            )
            alert_id = cursor.lastrowid
            conn.execute(
                """INSERT INTO price_alert_metadata
                   (alert_id,trigger_mode,repeat_mode,expires_at,channels,notify_only,last_state,has_observation)
                   VALUES (?,?,?,?,?,1,0,0)""",
                (alert_id, metadata["trigger_mode"], metadata["repeat_mode"], metadata["expires_at"], metadata_channels),
            )
            conn.execute(
                "INSERT INTO strategy_action_logs (user_id,strategy_name,action,params,result,created_at) VALUES (?,?,?,?,?,?)",
                (user_id, "价格预警", "ALERT_CREATE", json.dumps({"market": market, "symbol": symbol, "conditions": normalized, "logic": logic}, ensure_ascii=False), "success", now),
            )

    def list(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT a.*,m.trigger_mode,m.repeat_mode,m.expires_at,m.channels,m.notify_only,
                      m.last_state,m.has_observation,m.last_value
               FROM price_alerts a LEFT JOIN price_alert_metadata m ON m.alert_id=a.id
               WHERE a.user_id=? ORDER BY a.created_at DESC""", (user_id,)
        )
        for row in rows:
            # SQLite exposes BOOLEAN-compatible columns as 0/1 integers.  Keep
            # the public alert contract typed so browser clients cannot mistake
            # 0 for an active alert (`0 !== false` in JavaScript).
            row["is_active"] = bool(row.get("is_active"))
            if row.get("channels") is None:
                row["channels"] = '["website","telegram"]'
            try:
                row["channels"] = _normalize_channels(json.loads(row["channels"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                row["channels"] = ["website", "telegram"]
            row["trigger_mode"] = _normalize_trigger(row.get("trigger_mode"), operator=row.get("operator"))
            row["repeat_mode"] = _normalize_repeat(row.get("repeat_mode"))
            row["notify_only"] = True
            if row.get("expires_at"):
                try:
                    expiry = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=UTC)
                    if expiry <= datetime.now(UTC) and row.get("is_active"):
                        self.db.execute("UPDATE price_alerts SET is_active=0 WHERE id=? AND is_active=1", (row["id"],))
                        row["is_active"] = 0
                except ValueError:
                    pass
            row["market"], row["symbol"] = normalize_alert_instrument(
                str(row.get("symbol") or ""), row.get("market")
            )
            try:
                row["conditions_list"] = normalize_conditions(json.loads(row.get("conditions") or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                row["conditions_list"] = normalize_conditions(operator=row["operator"], target=row["target_price"])
            logic = str(row.get("logic") or "AND").upper()
            row["logic"] = logic if logic in {"AND", "OR"} else "AND"
            row["preview"] = condition_preview(row["symbol"], row["conditions_list"], row["logic"])
            row["metadata"] = {
                "trigger_mode": row["trigger_mode"], "repeat_mode": row["repeat_mode"],
                "expires_at": row.get("expires_at"), "channels": row["channels"], "notify_only": True,
            }
        return rows

    def deactivate(self, user_id: int, alert_id: int) -> None:
        self.db.execute("UPDATE price_alerts SET is_active=0 WHERE id=? AND user_id=?", (alert_id, user_id))

    def evaluate(
        self,
        user_id: int,
        prices: dict[str, float],
        metrics: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        prices_by_instrument = _market_value_map(prices)
        metrics_by_instrument = _market_value_map(
            {key: value for key, value in (metrics or {}).items() if isinstance(value, dict)}
        )
        triggered = []
        for alert in self.list(user_id):
            instrument = (alert["market"], alert["symbol"])
            if not alert["is_active"] or instrument not in prices_by_instrument:
                continue
            try:
                current_price = _finite(prices_by_instrument[instrument])
            except (TypeError, ValueError):
                continue
            instrument_metrics = metrics_by_instrument.get(instrument, {})
            values = {
                **(instrument_metrics if isinstance(instrument_metrics, dict) else {}),
                "price": current_price,
            }
            checks = []
            current_value: float | None = current_price
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
            trigger_mode = str(alert.get("trigger_mode") or "at_or_above")
            repeat_mode = str(alert.get("repeat_mode") or "once")
            has_observation = bool(alert.get("has_observation"))
            previous_value = alert.get("last_value")
            try:
                previous_value = _finite(previous_value) if previous_value is not None else None
            except (TypeError, ValueError):
                previous_value = None
            expired = False
            if alert.get("expires_at"):
                try:
                    expires_at = datetime.fromisoformat(str(alert["expires_at"]).replace("Z", "+00:00"))
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=UTC)
                    expired = expires_at <= datetime.now(UTC)
                except ValueError:
                    expired = True
            if expired:
                self.db.execute("UPDATE price_alerts SET is_active=0 WHERE id=? AND is_active=1", (alert["id"],))
                continue
            # Crossing alerts require an actual previous observation. Repeat
            # alerts fire once per false-to-true transition; once alerts close.
            threshold = next((float(item["value"]) for item in alert["conditions_list"] if item["type"] == "price"), None)
            crossed = False
            if threshold is not None and previous_value is not None:
                crossed = (
                    trigger_mode == "crosses_above" and previous_value < threshold <= current_price
                ) or (
                    trigger_mode == "crosses_below" and previous_value > threshold >= current_price
                )
            should_trigger = crossed if trigger_mode.startswith("crosses_") else bool(hit)
            if repeat_mode == "repeat" and not trigger_mode.startswith("crosses_"):
                should_trigger = bool(hit) and (not has_observation or not bool(alert.get("last_state")))
            if repeat_mode == "repeat" and trigger_mode.startswith("crosses_"):
                should_trigger = crossed
            # Persist the latest observation even when no notification is sent.
            self.db.execute(
                """INSERT OR IGNORE INTO price_alert_metadata
                   (alert_id,trigger_mode,repeat_mode,expires_at,channels,notify_only,last_state,has_observation,last_value)
                   VALUES (?,?,?,?,?,1,?,?,?)""",
                (
                    alert["id"], trigger_mode, repeat_mode, alert.get("expires_at"),
                    json.dumps(alert.get("channels") or ["website"], ensure_ascii=False, separators=(",", ":")),
                    int(bool(hit)), 1, current_value,
                ),
            )
            if not should_trigger:
                self.db.execute(
                    "UPDATE price_alert_metadata SET last_state=?,has_observation=1,last_value=? WHERE alert_id=?",
                    (int(bool(hit)), current_value, alert["id"]),
                )
                continue
            if should_trigger:
                now = datetime.now(UTC).isoformat(timespec="seconds")
                content = f"{alert['preview']}，当前价格 {current_price:.2f}"
                with self.db.transaction() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    updated = conn.execute(
                        "UPDATE price_alerts SET is_active=CASE WHEN ?='once' THEN 0 ELSE is_active END,last_triggered=? WHERE id=? AND is_active=1",
                        (repeat_mode, now, alert["id"]),
                    ).rowcount
                    if updated:
                        notification_id = conn.execute(
                            """INSERT INTO notifications(msg_type,title,content,push_status,created_at)
                               VALUES ('PRICE_ALERT','价格预警已触发',?,'pending',?)""",
                            (content, now),
                        ).lastrowid
                        if "telegram" in (alert.get("channels") or []):
                            delivery = conn.execute(
                                "SELECT id,status FROM price_alert_deliveries WHERE alert_id=?", (alert["id"],)
                            ).fetchone()
                            if delivery:
                                conn.execute(
                                    """UPDATE price_alert_deliveries SET user_id=?,notification_id=?,status='pending',
                                       attempts=0,next_attempt_at=?,last_error=NULL,updated_at=?,sent_at=NULL
                                       WHERE alert_id=?""",
                                    (user_id, notification_id, now, now, alert["id"]),
                                )
                            else:
                                conn.execute(
                                    """INSERT INTO price_alert_deliveries
                                       (alert_id,user_id,notification_id,status,attempts,next_attempt_at,created_at,updated_at)
                                       VALUES (?,?,?,'pending',0,?,?,?)""",
                                    (alert["id"], user_id, notification_id, now, now, now),
                                )
                        else:
                            conn.execute("UPDATE notifications SET push_status='skipped' WHERE id=?", (notification_id,))
                        conn.execute(
                            "UPDATE price_alert_metadata SET last_state=?,has_observation=1,last_value=? WHERE alert_id=?",
                            (int(bool(hit)), current_value, alert["id"]),
                        )
                if updated:
                    alert["current_price"] = current_price
                    triggered.append(alert)
        return triggered
