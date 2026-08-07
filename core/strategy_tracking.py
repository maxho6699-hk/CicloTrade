# -*- coding: utf-8 -*-
"""Persistence API for saved strategies and their auditable performance history."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from core.compat import UTC
from typing import Any, Mapping

from core.database import DatabaseManager, get_database


_SOURCE_TYPES = {"template", "generated", "imported"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any, field: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain valid JSON values") from exc


def _metric(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _date(value: date | datetime | str | None) -> str:
    if value is None:
        return datetime.now(UTC).date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError("eval_date must be an ISO date") from exc


class StrategyPerformanceTracker:
    """Store user-owned strategy configurations and daily metric snapshots."""

    def __init__(self, database: DatabaseManager | None = None) -> None:
        self.db = database or get_database()

    @staticmethod
    def _id(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field} must be a positive integer")
        return value

    @staticmethod
    def _decode_strategy(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        result["is_active"] = bool(result["is_active"])
        return result

    @staticmethod
    def _decode_performance(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["equity_curve"] = json.loads(result.pop("equity_curve_json"))
        return result

    def save_strategy(
        self,
        user_id: int,
        *,
        name: str,
        source_type: str,
        config: Mapping[str, Any],
        strategy_key: str | None = None,
    ) -> dict[str, Any]:
        user_id = self._id(user_id, "user_id")
        clean_name = str(name or "").strip()
        if not clean_name or len(clean_name) > 120:
            raise ValueError("name must contain 1 to 120 characters")
        if source_type not in _SOURCE_TYPES:
            raise ValueError("source_type must be template, generated or imported")
        if not isinstance(config, Mapping):
            raise ValueError("config must be an object")
        config_json = _json(dict(config), "config")
        now = _now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO saved_strategies
                   (user_id,strategy_key,name,source_type,config_json,is_active,created_at,updated_at)
                   VALUES (?,?,?,?,?,1,?,?)""",
                (user_id, strategy_key, clean_name, source_type, config_json, now, now),
            )
            saved_id = int(cursor.lastrowid)
        return self.get(user_id, saved_id)

    def get(self, user_id: int, saved_strategy_id: int) -> dict[str, Any]:
        user_id = self._id(user_id, "user_id")
        saved_strategy_id = self._id(saved_strategy_id, "saved_strategy_id")
        row = self.db.fetch_one(
            "SELECT * FROM saved_strategies WHERE id=? AND user_id=?",
            (saved_strategy_id, user_id),
        )
        if row is None:
            raise KeyError("saved strategy not found")
        return self._decode_strategy(row)

    def list(self, user_id: int, *, active_only: bool = True) -> list[dict[str, Any]]:
        user_id = self._id(user_id, "user_id")
        sql = "SELECT * FROM saved_strategies WHERE user_id=?"
        if active_only:
            sql += " AND is_active=1"
        sql += " ORDER BY updated_at DESC,id DESC"
        return [self._decode_strategy(row) for row in self.db.fetch_all(sql, (user_id,))]

    def update_strategy(
        self,
        user_id: int,
        saved_strategy_id: int,
        *,
        name: str | None = None,
        config: Mapping[str, Any] | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        current = self.get(user_id, saved_strategy_id)
        assignments, values = [], []
        if name is not None:
            clean_name = str(name).strip()
            if not clean_name or len(clean_name) > 120:
                raise ValueError("name must contain 1 to 120 characters")
            assignments.append("name=?")
            values.append(clean_name)
        if config is not None:
            if not isinstance(config, Mapping):
                raise ValueError("config must be an object")
            assignments.append("config_json=?")
            values.append(_json(dict(config), "config"))
        if active is not None:
            assignments.append("is_active=?")
            values.append(int(bool(active)))
        if assignments:
            assignments.append("updated_at=?")
            values.extend((_now(), current["id"], current["user_id"]))
            self.db.execute(
                f"UPDATE saved_strategies SET {','.join(assignments)} WHERE id=? AND user_id=?",
                tuple(values),
            )
        return self.get(user_id, saved_strategy_id)

    def record_performance(
        self,
        user_id: int,
        saved_strategy_id: int,
        metrics: Mapping[str, Any],
        *,
        eval_date: date | datetime | str | None = None,
    ) -> dict[str, Any]:
        strategy = self.get(user_id, saved_strategy_id)
        if not isinstance(metrics, Mapping):
            raise ValueError("metrics must be an object")
        win_rate = _metric(metrics.get("win_rate"), "win_rate")
        if not 0 <= win_rate <= 1:
            raise ValueError("win_rate must be between 0 and 1")
        values = {
            "return_30d": _metric(metrics.get("return_30d"), "return_30d"),
            "max_drawdown": abs(_metric(metrics.get("max_drawdown"), "max_drawdown")),
            "annual_return": _metric(metrics.get("annual_return"), "annual_return"),
            "sharpe_ratio": _metric(metrics.get("sharpe_ratio"), "sharpe_ratio"),
            "win_rate": win_rate,
            "equity_curve_json": _json(metrics.get("equity_curve", []), "equity_curve"),
        }
        day, now = _date(eval_date), _now()
        self.db.execute(
            """INSERT INTO saved_strategy_performance
               (saved_strategy_id,eval_date,return_30d,max_drawdown,annual_return,
                sharpe_ratio,win_rate,equity_curve_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(saved_strategy_id,eval_date) DO UPDATE SET
                 return_30d=excluded.return_30d,
                 max_drawdown=excluded.max_drawdown,
                 annual_return=excluded.annual_return,
                 sharpe_ratio=excluded.sharpe_ratio,
                 win_rate=excluded.win_rate,
                 equity_curve_json=excluded.equity_curve_json""",
            (
                strategy["id"], day, values["return_30d"], values["max_drawdown"],
                values["annual_return"], values["sharpe_ratio"], values["win_rate"],
                values["equity_curve_json"], now,
            ),
        )
        return self.history(user_id, saved_strategy_id, limit=1, until=day)[0]

    def history(
        self,
        user_id: int,
        saved_strategy_id: int,
        *,
        limit: int = 365,
        until: date | datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        strategy = self.get(user_id, saved_strategy_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise ValueError("limit must be between 1 and 2000")
        params: list[Any] = [strategy["id"]]
        sql = "SELECT * FROM saved_strategy_performance WHERE saved_strategy_id=?"
        if until is not None:
            sql += " AND eval_date<=?"
            params.append(_date(until))
        sql += " ORDER BY eval_date DESC,id DESC LIMIT ?"
        params.append(limit)
        return [self._decode_performance(row) for row in self.db.fetch_all(sql, tuple(params))]


StrategyTracker = StrategyPerformanceTracker
