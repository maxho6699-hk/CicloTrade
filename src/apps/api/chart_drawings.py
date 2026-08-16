"""Account-scoped persistence for chart annotations.

The drawing payload is deliberately a small, closed schema.  It is not a
generic JSON document store: validating it here prevents one browser from
placing unbounded or ambiguous data in another user's chart scope.
"""

from __future__ import annotations

from datetime import date, datetime
import json
import math
import re
from typing import Any

from core.compat import UTC
from core.database import DatabaseManager, get_database
from src.apps.api.read_model import BrowserIdentity


MARKETS = {"US", "CN"}
SYMBOL_RE = re.compile(r"(?:[A-Z][A-Z0-9.-]{0,11}|\d{6})")
TIMEFRAME_RE = re.compile(r"[\w\-]{1,16}", re.UNICODE)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
ACTIVE_DRAWING_LIMIT = 200
USER_TOMBSTONE_LIMIT = 2_000
SYMBOL_TOMBSTONE_LIMIT = 500

TOOL_POINTS = {
    "segment": 2, "horizontal": 1, "horizontal-segment": 2, "vertical": 1,
    "ray": 2, "straight": 2, "parallel": 3, "channel": 3, "periodic": 2,
    "info-line": 2, "smooth-top": 3, "cross": 1, "rectangle": 2,
    "triangle": 3, "parallelogram": 3, "circle": 2, "ellipse": 2, "path": 5,
    "wave3": 4, "wave5": 6, "wave8": 9, "head-shoulders": 5,
    "triangle-pattern": 5, "mw": 5, "abcd": 4, "xabcd": 5,
    "three-drive": 7, "sine": 6, "fib-retracement": 2, "fib-time": 2,
    "fib-extension": 3, "speed-resistance": 2, "gann-box": 2,
    "gann-angle": 2, "grid-line": 2, "pitchfork": 3, "schiff": 3,
    "modified-schiff": 3, "inside-pitchfork": 3, "fan": 2, "time-ruler": 2,
    "space-ruler": 2, "time-space-ruler": 2, "long-position": 2,
    "short-position": 2, "price-label": 1, "arrow": 2, "up-arrow": 2,
    "down-arrow": 2,
}


class ChartDrawingError(ValueError):
    pass


class ChartDrawingConflict(ChartDrawingError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _scope(payload: dict[str, Any]) -> tuple[str, str, str, int]:
    if set(payload) != {"market", "symbol", "timeframe", "cross_timeframe"}:
        raise ChartDrawingError("画线查询字段无效。")
    market = payload["market"]
    symbol = payload["symbol"]
    timeframe = payload["timeframe"]
    cross = payload["cross_timeframe"]
    if not isinstance(market, str) or market not in MARKETS:
        raise ChartDrawingError("画线市场必须是 US 或 CN。")
    if not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol.upper()):
        raise ChartDrawingError("画线股票代码无效。")
    if not isinstance(timeframe, str) or not TIMEFRAME_RE.fullmatch(timeframe):
        raise ChartDrawingError("画线周期无效。")
    if not isinstance(cross, bool):
        raise ChartDrawingError("跨周期标志必须为布尔值。")
    return market, symbol.upper(), timeframe, int(cross)


def query_scope(*, market: Any, symbol: Any, timeframe: Any, cross_timeframe: Any) -> tuple[str, str, str, int]:
    return _scope({"market": market, "symbol": symbol, "timeframe": timeframe, "cross_timeframe": cross_timeframe})


def _drawing(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"id", "tool", "points"}:
        raise ChartDrawingError("画线对象包含未知字段。")
    drawing_id, tool, points = value["id"], value["tool"], value["points"]
    if not isinstance(drawing_id, str) or not UUID_RE.fullmatch(drawing_id):
        raise ChartDrawingError("画线编号无效。")
    if not isinstance(tool, str) or tool not in TOOL_POINTS:
        raise ChartDrawingError("画线工具无效。")
    if not isinstance(points, list) or len(points) != TOOL_POINTS[tool]:
        raise ChartDrawingError("画线点数量无效。")
    normalized: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"time", "price"}:
            raise ChartDrawingError("画线点包含未知字段。")
        time, price = point["time"], point["price"]
        business_day = isinstance(time, dict) and set(time) == {"year", "month", "day"}
        if business_day:
            try:
                if any(isinstance(time[key], bool) or not isinstance(time[key], int) for key in time):
                    raise ValueError
                date(time["year"], time["month"], time["day"])
            except (TypeError, ValueError):
                business_day = False
        valid_date_string = False
        if isinstance(time, str) and DATE_RE.fullmatch(time):
            try:
                date.fromisoformat(time)
                valid_date_string = True
            except ValueError:
                pass
        if isinstance(time, bool) or not (
            isinstance(time, int) and 0 < time < 4_102_444_800
            or valid_date_string
            or business_day
        ):
            raise ChartDrawingError("画线时间无效。")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or abs(float(price)) > 1_000_000_000:
            raise ChartDrawingError("画线价格无效。")
        normalized.append({"time": time, "price": float(price)})
    return {"id": drawing_id.lower(), "tool": tool, "points": normalized}


def _operation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("op"), str):
        raise ChartDrawingError("画线操作无效。")
    op = value["op"]
    if op == "upsert":
        if set(value) != {"op", "origin_timeframe", "cross_timeframe", "revision", "drawing"}:
            raise ChartDrawingError("保存画线操作字段无效。")
        drawing = _drawing(value["drawing"])
        origin_timeframe, cross_timeframe = _operation_scope(value)
        revision = value["revision"]
        if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 1):
            raise ChartDrawingError("画线版本无效。")
        return {"op": op, "origin_timeframe": origin_timeframe, "cross_timeframe": cross_timeframe, "revision": revision, "drawing": drawing, "drawing_id": drawing["id"]}
    if op in {"delete", "restore"}:
        if set(value) != {"op", "origin_timeframe", "cross_timeframe", "revision", "drawing_id"}:
            raise ChartDrawingError("画线操作字段无效。")
        origin_timeframe, cross_timeframe = _operation_scope(value)
        drawing_id, revision = value["drawing_id"], value["revision"]
        if not isinstance(drawing_id, str) or not UUID_RE.fullmatch(drawing_id):
            raise ChartDrawingError("画线编号无效。")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ChartDrawingError("画线版本无效。")
        return {"op": op, "origin_timeframe": origin_timeframe, "cross_timeframe": cross_timeframe, "revision": revision, "drawing_id": drawing_id.lower()}
    raise ChartDrawingError("不支持的画线操作。")


def _operation_scope(value: dict[str, Any]) -> tuple[str, int]:
    timeframe, cross = value.get("origin_timeframe"), value.get("cross_timeframe")
    if not isinstance(timeframe, str) or not TIMEFRAME_RE.fullmatch(timeframe):
        raise ChartDrawingError("画线来源周期无效。")
    if not isinstance(cross, bool):
        raise ChartDrawingError("跨周期标志必须为布尔值。")
    return timeframe, int(cross)


class ChartDrawingService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def list(self, identity: BrowserIdentity, *, market: Any, symbol: Any, timeframe: Any, cross_timeframe: Any) -> dict[str, Any]:
        market, symbol, timeframe, cross = query_scope(market=market, symbol=symbol, timeframe=timeframe, cross_timeframe=cross_timeframe)
        if cross:
            rows = self.db.fetch_all(
                """SELECT origin_timeframe,cross_timeframe,drawing_json,revision FROM user_chart_drawings
                   WHERE user_id=? AND market=? AND symbol=? AND deleted_at IS NULL
                   AND ((cross_timeframe=0 AND origin_timeframe=?) OR cross_timeframe=1)
                   ORDER BY cross_timeframe,origin_timeframe,created_at LIMIT 201""",
                (identity.id, market, symbol, timeframe),
            )
            deleted_rows = self.db.fetch_all(
                """SELECT origin_timeframe,cross_timeframe,drawing_id,revision FROM user_chart_drawings
                   WHERE user_id=? AND market=? AND symbol=? AND deleted_at IS NOT NULL
                   AND ((cross_timeframe=0 AND origin_timeframe=?) OR cross_timeframe=1)
                   ORDER BY updated_at DESC LIMIT ?""",
                (identity.id, market, symbol, timeframe, SYMBOL_TOMBSTONE_LIMIT + 1),
            )
        else:
            rows = self.db.fetch_all(
                """SELECT origin_timeframe,cross_timeframe,drawing_json,revision FROM user_chart_drawings
                   WHERE user_id=? AND market=? AND symbol=? AND origin_timeframe=?
                   AND cross_timeframe=0 AND deleted_at IS NULL ORDER BY created_at LIMIT 201""",
                (identity.id, market, symbol, timeframe),
            )
            deleted_rows = self.db.fetch_all(
                """SELECT origin_timeframe,cross_timeframe,drawing_id,revision FROM user_chart_drawings
                   WHERE user_id=? AND market=? AND symbol=? AND origin_timeframe=?
                   AND cross_timeframe=0 AND deleted_at IS NOT NULL
                   ORDER BY updated_at DESC LIMIT ?""",
                (identity.id, market, symbol, timeframe, SYMBOL_TOMBSTONE_LIMIT + 1),
            )
        items = []
        for row in rows[:ACTIVE_DRAWING_LIMIT]:
            drawing = json.loads(row["drawing_json"])
            items.append({**drawing, "origin_timeframe": row["origin_timeframe"], "cross_timeframe": bool(row["cross_timeframe"]), "revision": row["revision"]})
        tombstones = [
            {
                "drawing_id": row["drawing_id"],
                "origin_timeframe": row["origin_timeframe"],
                "cross_timeframe": bool(row["cross_timeframe"]),
                "revision": row["revision"],
            }
            for row in deleted_rows[:SYMBOL_TOMBSTONE_LIMIT]
        ]
        return {
            "items": items,
            "truncated": len(rows) > ACTIVE_DRAWING_LIMIT,
            "tombstones": tombstones,
            "tombstones_truncated": len(deleted_rows) > SYMBOL_TOMBSTONE_LIMIT,
        }

    def batch(self, identity: BrowserIdentity, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"market", "symbol", "operations"}:
            raise ChartDrawingError("画线批量请求字段无效。")
        market, symbol = payload["market"], payload["symbol"]
        if not isinstance(market, str) or market not in MARKETS:
            raise ChartDrawingError("画线市场必须是 US 或 CN。")
        if not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol.upper()):
            raise ChartDrawingError("画线股票代码无效。")
        symbol = symbol.upper()
        operations = payload["operations"]
        if not isinstance(operations, list) or not operations or len(operations) > 100:
            raise ChartDrawingError("每批画线操作必须介于 1 与 100 条。")
        normalized = [_operation(item) for item in operations]
        if len({(item["origin_timeframe"], item["cross_timeframe"], item["drawing_id"]) for item in normalized}) != len(normalized):
            raise ChartDrawingError("同一批不能重复操作同一画线。")
        now = _now()
        results: list[dict[str, Any]] = []
        with self.db.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for operation in normalized:
                row = connection.execute(
                    """SELECT drawing_json,revision,deleted_at FROM user_chart_drawings
                       WHERE user_id=? AND market=? AND symbol=? AND origin_timeframe=? AND cross_timeframe=? AND drawing_id=?""",
                    (identity.id, market, symbol, operation["origin_timeframe"], operation["cross_timeframe"], operation["drawing_id"]),
                ).fetchone()
                if operation["op"] == "upsert":
                    encoded = json.dumps(operation["drawing"], ensure_ascii=False, separators=(",", ":"))
                    if row is None:
                        if operation["revision"] is not None:
                            raise ChartDrawingConflict("画线版本冲突，请重新读取。")
                        self._require_active_capacity(connection, identity.id, market, symbol)
                        connection.execute(
                            """INSERT INTO user_chart_drawings(user_id,market,symbol,origin_timeframe,cross_timeframe,drawing_id,drawing_json,revision,deleted_at,created_at,updated_at)
                               VALUES (?,?,?,?,?,?,?,?,NULL,?,?)""",
                            (identity.id, market, symbol, operation["origin_timeframe"], operation["cross_timeframe"], operation["drawing_id"], encoded, 1, now, now),
                        )
                        revision, deleted_at = 1, None
                    elif row["deleted_at"] is None and row["drawing_json"] == encoded and operation["revision"] in {None, row["revision"], row["revision"] - 1}:
                        revision, deleted_at = row["revision"], None
                    else:
                        if operation["revision"] != row["revision"]:
                            raise ChartDrawingConflict("画线版本冲突，请重新读取。")
                        if row["deleted_at"] is not None:
                            self._require_active_capacity(connection, identity.id, market, symbol)
                        revision, deleted_at = row["revision"] + 1, None
                        connection.execute(
                            """UPDATE user_chart_drawings SET drawing_json=?,revision=?,deleted_at=NULL,updated_at=?
                               WHERE user_id=? AND market=? AND symbol=? AND origin_timeframe=? AND cross_timeframe=? AND drawing_id=?""",
                            (encoded, revision, now, identity.id, market, symbol, operation["origin_timeframe"], operation["cross_timeframe"], operation["drawing_id"]),
                        )
                else:
                    if row is None:
                        raise ChartDrawingConflict("画线不存在或版本冲突，请重新读取。")
                    desired_deleted = operation["op"] == "delete"
                    already_desired = (row["deleted_at"] is not None) == desired_deleted
                    if already_desired and operation["revision"] in {row["revision"], row["revision"] - 1}:
                        revision, deleted_at = row["revision"], row["deleted_at"]
                    else:
                        if operation["revision"] != row["revision"]:
                            raise ChartDrawingConflict("画线版本冲突，请重新读取。")
                        if not desired_deleted and row["deleted_at"] is not None:
                            self._require_active_capacity(connection, identity.id, market, symbol)
                        revision = row["revision"] + 1
                        deleted_at = now if desired_deleted else None
                        connection.execute(
                            """UPDATE user_chart_drawings SET revision=?,deleted_at=?,updated_at=?
                               WHERE user_id=? AND market=? AND symbol=? AND origin_timeframe=? AND cross_timeframe=? AND drawing_id=?""",
                            (revision, deleted_at, now, identity.id, market, symbol, operation["origin_timeframe"], operation["cross_timeframe"], operation["drawing_id"]),
                        )
                results.append({"drawing_id": operation["drawing_id"], "origin_timeframe": operation["origin_timeframe"], "cross_timeframe": bool(operation["cross_timeframe"]), "revision": revision, "deleted": deleted_at is not None})
            connection.execute(
                """DELETE FROM user_chart_drawings WHERE rowid IN (
                       SELECT rowid FROM user_chart_drawings
                       WHERE user_id=? AND market=? AND symbol=? AND deleted_at IS NOT NULL
                       ORDER BY updated_at DESC LIMIT -1 OFFSET ?
                   )""",
                (identity.id, market, symbol, SYMBOL_TOMBSTONE_LIMIT),
            )
            connection.execute(
                """DELETE FROM user_chart_drawings WHERE rowid IN (
                       SELECT rowid FROM user_chart_drawings WHERE user_id=? AND deleted_at IS NOT NULL
                       ORDER BY updated_at DESC LIMIT -1 OFFSET ?
                   )""",
                (identity.id, USER_TOMBSTONE_LIMIT),
            )
        return {"items": results}

    @staticmethod
    def _require_active_capacity(connection: Any, user_id: int, market: str, symbol: str) -> None:
        count = connection.execute(
            """SELECT COUNT(*) FROM user_chart_drawings
               WHERE user_id=? AND market=? AND symbol=? AND deleted_at IS NULL""",
            (user_id, market, symbol),
        ).fetchone()[0]
        if count >= ACTIVE_DRAWING_LIMIT:
            raise ChartDrawingError(f"每个股票最多保存 {ACTIVE_DRAWING_LIMIT} 条有效画线。")
