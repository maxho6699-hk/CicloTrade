# -*- coding: utf-8 -*-
"""Five-dimension strategy scoring, ranking and lifecycle persistence."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from numbers import Integral
from typing import Any, Iterable, Mapping

from core.database import DatabaseManager, get_database


WEIGHTS = {
    "total_return": 0.25,
    "max_drawdown": 0.25,
    "sharpe_ratio": 0.20,
    "profit_loss_ratio": 0.15,
    "consecutive_losses": 0.15,
}
LOWER_IS_BETTER = {"max_drawdown", "consecutive_losses"}


def _eval_date(value: date | datetime | str | None) -> str:
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


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _normalised(values: list[float], *, lower_is_better: bool) -> list[float]:
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [50.0] * len(values)
    if lower_is_better:
        return [(high - value) / (high - low) * 100 for value in values]
    return [(value - low) / (high - low) * 100 for value in values]


def _dimension_scores(rows: list[dict[str, Any]]) -> None:
    for field in WEIGHTS:
        values = [float(row[field]) for row in rows]
        scores = _normalised(values, lower_is_better=field in LOWER_IS_BETTER)
        for row, score in zip(rows, scores, strict=True):
            row.setdefault("dimension_scores", {})[field] = round(score, 4)
    for row in rows:
        row["weighted_score"] = round(
            sum(row["dimension_scores"][field] * weight for field, weight in WEIGHTS.items()),
            4,
        )


class StrategyScorer:
    """Rank a batch of real backtest metrics and persist its daily snapshot."""

    def __init__(self, database: DatabaseManager | None = None) -> None:
        self.db = database or get_database()

    def score(self, metrics: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        metric_rows = list(metrics)
        definitions = self.db.fetch_all(
            "SELECT id,strategy_key,name FROM strategy_definitions WHERE is_active=1"
        )
        by_key = {row["strategy_key"]: row for row in definitions}
        by_id = {int(row["id"]): row for row in definitions}
        rows = []
        strategy_ids: set[int] = set()
        for metric in metric_rows:
            if not isinstance(metric, Mapping):
                raise ValueError("each metric must be an object")
            if metric.get("strategy_key"):
                strategy = by_key.get(str(metric["strategy_key"]))
            else:
                strategy_id = metric.get("strategy_id")
                if isinstance(strategy_id, bool) or not isinstance(strategy_id, Integral):
                    raise ValueError("each metric requires strategy_key or integer strategy_id")
                strategy = by_id.get(int(strategy_id))
            if strategy is None:
                raise ValueError("strategy is missing or inactive")
            strategy_id = int(strategy["id"])
            if strategy_id in strategy_ids:
                raise ValueError("a strategy can only appear once in a scoring batch")
            strategy_ids.add(strategy_id)
            consecutive = metric.get("consecutive_losses")
            if isinstance(consecutive, bool) or not isinstance(consecutive, Integral) or consecutive < 0:
                raise ValueError("consecutive_losses must be a non-negative integer")
            profit_loss_ratio = _number(metric.get("profit_loss_ratio"), "profit_loss_ratio")
            if profit_loss_ratio < 0:
                raise ValueError("profit_loss_ratio must be non-negative")
            rows.append(
                {
                    **strategy,
                    "strategy_id": strategy_id,
                    "total_return": _number(metric.get("total_return"), "total_return"),
                    "max_drawdown": abs(_number(metric.get("max_drawdown"), "max_drawdown")),
                    "sharpe_ratio": _number(metric.get("sharpe_ratio"), "sharpe_ratio"),
                    "profit_loss_ratio": profit_loss_ratio,
                    "consecutive_losses": int(consecutive),
                }
            )
        if not rows:
            raise ValueError("at least one strategy metric is required")
        _dimension_scores(rows)
        rows.sort(key=lambda row: (-row["weighted_score"], row["strategy_key"]))
        candidate_count = len(rows)
        for rank, row in enumerate(rows, 1):
            row["rank_position"] = rank
            row["candidate_count"] = candidate_count
        return rows

    def _last_place_streak(self, strategy_id: int, current_date: str, is_last: bool) -> int:
        if not is_last:
            return 0
        streak = 1
        history = self.db.fetch_all(
            """SELECT rank_position,candidate_count FROM strategy_scores
               WHERE strategy_id=? AND eval_date<? ORDER BY eval_date DESC""",
            (strategy_id, current_date),
        )
        for row in history:
            if int(row["rank_position"]) != int(row["candidate_count"]):
                break
            streak += 1
        return streak

    def evaluate(
        self,
        metrics: Iterable[Mapping[str, Any]],
        *,
        eval_date: date | datetime | str | None = None,
        persist: bool = True,
    ) -> list[dict[str, Any]]:
        evaluated = self.score(metrics)
        day = _eval_date(eval_date)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        for row in evaluated:
            streak = self._last_place_streak(
                row["strategy_id"], day, row["rank_position"] == row["candidate_count"]
            )
            row["last_place_streak"] = streak
            if row["rank_position"] <= min(3, row["candidate_count"]):
                row["lifecycle_status"] = "top3"
            elif streak >= 60:
                row["lifecycle_status"] = "retire_pending"
            elif streak >= 30:
                row["lifecycle_status"] = "watch"
            else:
                row["lifecycle_status"] = "active"
            row["eval_date"] = day
        if persist:
            with self.db.transaction() as conn:
                conn.executemany(
                    """INSERT INTO strategy_scores
                       (strategy_id,eval_date,total_return,max_drawdown,sharpe_ratio,
                        profit_loss_ratio,consecutive_losses,weighted_score,rank_position,
                        candidate_count,lifecycle_status,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(strategy_id,eval_date) DO UPDATE SET
                         total_return=excluded.total_return,
                         max_drawdown=excluded.max_drawdown,
                         sharpe_ratio=excluded.sharpe_ratio,
                         profit_loss_ratio=excluded.profit_loss_ratio,
                         consecutive_losses=excluded.consecutive_losses,
                         weighted_score=excluded.weighted_score,
                         rank_position=excluded.rank_position,
                         candidate_count=excluded.candidate_count,
                         lifecycle_status=excluded.lifecycle_status""",
                    [
                        (
                            row["strategy_id"], day, row["total_return"], row["max_drawdown"],
                            row["sharpe_ratio"], row["profit_loss_ratio"], row["consecutive_losses"],
                            row["weighted_score"], row["rank_position"], row["candidate_count"],
                            row["lifecycle_status"], now,
                        )
                        for row in evaluated
                    ],
                )
        return evaluated

    def latest(self, *, family: str | None = None) -> list[dict[str, Any]]:
        if family is not None and family not in {"option", "equity"}:
            raise ValueError("family must be option or equity")
        sql = """SELECT s.*,d.strategy_key,d.name,d.family,d.risk_level,d.scenario
                 FROM strategy_scores s JOIN strategy_definitions d ON d.id=s.strategy_id
                 WHERE d.is_active=1
                   AND s.eval_date=(SELECT MAX(eval_date) FROM strategy_scores)"""
        sql += " ORDER BY s.rank_position,d.strategy_key"
        rows = self.db.fetch_all(sql)
        if rows:
            _dimension_scores(rows)
        if family:
            rows = [row for row in rows if row["family"] == family]
        return rows

    def top_three(self, *, family: str | None = None) -> list[dict[str, Any]]:
        return [row for row in self.latest(family=family) if row["lifecycle_status"] == "top3"][:3]
