# -*- coding: utf-8 -*-
"""Internal-only user research profiles derived from persisted behaviour."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from core.compat import UTC
import json
from typing import Any

from core.database import DatabaseManager, get_database


OPTION_NAMES = {"買入 Call", "买入 Call", "買入 Put", "买入 Put", "牛市價差", "牛市价差", "熊市價差", "熊市价差", "買入跨式", "买入跨式", "蝶式", "備兌看漲", "备兑看涨", "現金擔保看跌", "现金担保看跌"}
HEDGE_WORDS = ("對沖", "对冲", "跨式", "蝶式", "備兌", "备兑", "擔保", "担保")


def profile_tags(
    *, backtest_frequency: float, preferred_strategy: str,
    average_holding_days: float, preferred_win_rate: float,
) -> list[str]:
    tags: list[str] = []
    if preferred_strategy in OPTION_NAMES:
        tags.append("期權探索型")
    if any(word in preferred_strategy for word in HEDGE_WORDS):
        tags.append("對沖型")
    if average_holding_days and average_holding_days <= 30 and backtest_frequency >= 1:
        tags.append("短線激進型")
    if average_holding_days >= 60 or preferred_win_rate >= 0.60:
        tags.append("長線穩健型")
    return tags or ["研究成長型"]


class UserProfileService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def aggregate(self, user_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(UTC)
        cutoff = (current - timedelta(days=28)).isoformat(timespec="seconds")
        rows = self.db.fetch_all(
            """SELECT strategy_name,win_rate,params,created_at FROM backtest_records
               WHERE user_id=? ORDER BY created_at DESC""",
            (user_id,),
        )
        recent_count = sum(str(row.get("created_at", "")) >= cutoff for row in rows)
        frequency = round(recent_count / 4, 2)
        counts = Counter(str(row.get("strategy_name") or "") for row in rows if row.get("strategy_name"))
        preferred = counts.most_common(1)[0][0] if counts else ""
        holding_days: list[float] = []
        win_rates: list[float] = []
        for row in rows:
            try:
                params = json.loads(row.get("params") or "{}")
            except (TypeError, json.JSONDecodeError):
                params = {}
            value = params.get("dte", params.get("holding_days"))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                holding_days.append(float(value))
            win_rate = row.get("win_rate")
            if isinstance(win_rate, (int, float)) and not isinstance(win_rate, bool):
                win_rates.append(min(max(float(win_rate), 0), 1))
        average_holding = round(sum(holding_days) / len(holding_days), 2) if holding_days else 0.0
        preferred_win_rate = round(sum(win_rates) / len(win_rates), 4) if win_rates else 0.0
        tags = profile_tags(
            backtest_frequency=frequency,
            preferred_strategy=preferred,
            average_holding_days=average_holding,
            preferred_win_rate=preferred_win_rate,
        )
        updated_at = current.isoformat(timespec="seconds")
        self.db.execute(
            """INSERT INTO user_profiles
               (user_id,backtest_frequency,preferred_strategy,average_holding_days,preferred_win_rate,tags_json,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 backtest_frequency=excluded.backtest_frequency,
                 preferred_strategy=excluded.preferred_strategy,
                 average_holding_days=excluded.average_holding_days,
                 preferred_win_rate=excluded.preferred_win_rate,
                 tags_json=excluded.tags_json,
                 updated_at=excluded.updated_at""",
            (user_id, frequency, preferred, average_holding, preferred_win_rate, json.dumps(tags, ensure_ascii=False), updated_at),
        )
        return self.get(user_id)

    def aggregate_all(self) -> int:
        users = self.db.fetch_all("SELECT id FROM users WHERE is_active=1 ORDER BY id")
        for user in users:
            self.aggregate(int(user["id"]))
        return len(users)

    def get(self, user_id: int) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM user_profiles WHERE user_id=?", (user_id,))
        if not row:
            return {
                "user_id": user_id, "backtest_frequency": 0.0, "preferred_strategy": "",
                "average_holding_days": 0.0, "preferred_win_rate": 0.0,
                "tags": ["研究成長型"], "updated_at": None,
            }
        row["tags"] = json.loads(row.pop("tags_json"))
        return row

    def statistics(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all("SELECT tags_json FROM user_profiles")
        counts: Counter[str] = Counter()
        for row in rows:
            try:
                tags = json.loads(row.get("tags_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                tags = []
            counts.update(str(tag) for tag in tags if tag)
        return [{"標籤": tag, "用戶數": count} for tag, count in counts.most_common()]

    def matching_strategies(self, user_id: int, definitions: list[dict], limit: int = 3) -> list[dict]:
        tags = set(self.get(user_id)["tags"])
        def score(item: dict) -> tuple[int, int, str]:
            points = 0
            if "期權探索型" in tags and item.get("family") == "option":
                points += 3
            if "對沖型" in tags and any(word in str(item.get("name", "")) for word in HEDGE_WORDS):
                points += 3
            if "長線穩健型" in tags and item.get("risk") == "low":
                points += 2
            if "短線激進型" in tags and item.get("risk") == "high":
                points += 2
            return (-points, 0 if item.get("core") else 1, str(item.get("name", "")))
        return sorted(definitions, key=score)[: max(1, min(int(limit), 10))]
