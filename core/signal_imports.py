# -*- coding: utf-8 -*-
"""Validated, idempotent CSV and JSON signal imports."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
import hashlib
from io import StringIO
import json
import math
import re
import sqlite3
from typing import Any

from core.database import DatabaseManager, get_database
from core.plans import can, csv_import_limit


MAX_SIGNALS = 500
MAX_CSV_BYTES = 262_144
SIGNAL_FIELDS = {
    "signal_id", "symbol", "action", "quantity", "price", "timestamp",
    "strategy", "confidence", "disclaimer",
}
REQUIRED_SIGNAL_FIELDS = SIGNAL_FIELDS - {"confidence"}
DISCLAIMER = "僅供參考，不構成投資建議"
SYMBOL_PATTERN = re.compile(r"(?:[A-Z][A-Z0-9.-]{0,11}|\d{6})\Z")


def _finite_number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} 必須是數值。")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} 必須是數值。") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} 必須是有限正數。" if positive else f"{name} 必須是有限數值。")
    return number


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("timestamp 必須是含時區的 ISO 8601 字串。")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp 必須是有效的 ISO 8601 時間。") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp 必須包含時區。")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_signal(item: object) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("每筆信號都必須是 JSON 物件。")
    unknown = set(item) - SIGNAL_FIELDS
    missing = REQUIRED_SIGNAL_FIELDS - set(item)
    if unknown:
        raise ValueError(f"信號包含未知欄位：{', '.join(sorted(unknown))}。")
    if missing:
        raise ValueError(f"信號缺少欄位：{', '.join(sorted(missing))}。")
    signal_id = str(item["signal_id"]).strip()
    symbol = str(item["symbol"]).strip().upper()
    action = str(item["action"]).strip().lower()
    strategy = str(item["strategy"]).strip()
    disclaimer = str(item["disclaimer"]).strip()
    if not signal_id or len(signal_id) > 80:
        raise ValueError("signal_id 長度必須為 1 至 80 個字元。")
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("symbol 僅支援美股代碼或 6 位 A 股代碼。")
    if action not in {"buy", "sell", "hold"}:
        raise ValueError("action 必須是 buy、sell 或 hold。")
    if not strategy or len(strategy) > 120:
        raise ValueError("strategy 長度必須為 1 至 120 個字元。")
    if not disclaimer or len(disclaimer) > 300:
        raise ValueError("disclaimer 長度必須為 1 至 300 個字元。")
    quantity = _finite_number(item["quantity"], "quantity", positive=True)
    price = _finite_number(item["price"], "price", positive=True)
    if quantity > 1_000_000_000 or price > 1_000_000_000:
        raise ValueError("quantity 或 price 超出允許範圍。")
    confidence = item.get("confidence")
    if confidence is not None:
        confidence = _finite_number(confidence, "confidence")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence 必須介於 0 與 1。")
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "price": price,
        "timestamp": _timestamp(item["timestamp"]),
        "strategy": strategy,
        "confidence": confidence,
        "disclaimer": disclaimer,
    }


def validate_signals(items: object) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise ValueError("signals 必須是非空陣列。")
    if len(items) > MAX_SIGNALS:
        raise ValueError(f"單次最多匯入 {MAX_SIGNALS} 筆信號。")
    result = [validate_signal(item) for item in items]
    ids = [item["signal_id"] for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("同一批次不得包含重複 signal_id。")
    return result


def parse_csv(content: bytes, strategy: str = "CSV 匯入") -> list[dict[str, Any]]:
    if not content or len(content) > MAX_CSV_BYTES:
        raise ValueError("CSV 檔案必須介於 1 byte 與 256 KB。")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV 必須使用 UTF-8 編碼。") from exc
    reader = csv.DictReader(StringIO(text))
    aliases = {
        "標的": "symbol", "symbol": "symbol", "日期": "timestamp", "timestamp": "timestamp",
        "操作": "action", "action": "action", "數量": "quantity", "数量": "quantity", "quantity": "quantity",
        "價格": "price", "价格": "price", "price": "price", "策略": "strategy", "strategy": "strategy",
    }
    if not reader.fieldnames:
        raise ValueError("CSV 缺少標題列。")
    mapped = {name: aliases.get(name.strip().lower(), aliases.get(name.strip())) for name in reader.fieldnames}
    required = {"symbol", "timestamp", "action", "quantity", "price"}
    if not required.issubset({value for value in mapped.values() if value}):
        raise ValueError("CSV 必須包含標的、日期、操作、數量、價格欄位。")
    action_aliases = {"買入": "buy", "买入": "buy", "賣出": "sell", "卖出": "sell", "持有": "hold"}
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(reader, 2):
        normalized = {target: row[source] for source, target in mapped.items() if target and row.get(source) is not None}
        normalized["action"] = action_aliases.get(str(normalized.get("action", "")).strip(), normalized.get("action"))
        normalized["strategy"] = str(normalized.get("strategy") or strategy)
        normalized["disclaimer"] = DISCLAIMER
        digest = hashlib.sha256(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
        normalized["signal_id"] = f"CSV-{digest}"
        try:
            rows.append(validate_signal(normalized))
        except ValueError as exc:
            raise ValueError(f"CSV 第 {index} 列：{exc}") from exc
    return validate_signals(rows)


class SignalImportService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def daily_count(self, user_id: int, import_type: str) -> int:
        today = datetime.now(UTC).date().isoformat()
        row = self.db.fetch_one(
            "SELECT COUNT(*) count FROM signal_import_jobs WHERE user_id=? AND import_type=? AND created_at LIKE ?",
            (user_id, import_type, f"{today}%"),
        )
        return int(row["count"] if row else 0)

    def _check_plan(self, user_id: int, plan: str, import_type: str, *, enforce_quota: bool = True) -> None:
        capability = {"csv": "csv_import", "code": "code_import", "api": "api_signal_import"}[import_type]
        if not can(plan, capability):
            raise PermissionError("目前會員等級不包含此匯入方式。")
        if enforce_quota and import_type == "csv":
            limit = csv_import_limit(plan)
            if limit is not None and self.daily_count(user_id, "csv") >= limit:
                raise PermissionError(f"CSV 每日匯入上限為 {limit} 次。")

    def import_signals(
        self,
        user_id: int,
        plan: str,
        items: object,
        *,
        import_type: str,
        filename: str | None = None,
    ) -> dict[str, Any]:
        if import_type not in {"csv", "api"}:
            raise ValueError("此方法僅接受 CSV 或 API 信號。")
        self._check_plan(user_id, plan, import_type, enforce_quota=False)
        signals = validate_signals(items)
        canonical = json.dumps(signals, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self.db.fetch_one(
            "SELECT id,row_count,created_at FROM signal_import_jobs WHERE user_id=? AND import_type=? AND source_hash=?",
            (user_id, import_type, source_hash),
        )
        if existing:
            return {"job_id": existing["id"], "row_count": existing["row_count"], "created": False}
        self._check_plan(user_id, plan, import_type)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    """INSERT INTO signal_import_jobs
                       (user_id,import_type,filename,status,row_count,error_message,report_json,source_hash,created_at,completed_at)
                       VALUES (?,?,?,'validated',?,NULL,?,?,?,?)""",
                    (user_id, import_type, (filename or "")[:160] or None, len(signals), json.dumps({"validated": len(signals)}, ensure_ascii=False), source_hash, now, now),
                )
                job_id = int(cursor.lastrowid)
                conn.executemany(
                    """INSERT INTO imported_signals
                       (job_id,user_id,signal_id,symbol,action,quantity,price,timestamp,strategy,confidence,disclaimer,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (job_id, user_id, s["signal_id"], s["symbol"], s["action"], s["quantity"], s["price"], s["timestamp"], s["strategy"], s["confidence"], s["disclaimer"], now)
                        for s in signals
                    ],
                )
                conn.execute(
                    "INSERT INTO system_events(event_type,component,message,details,created_at) VALUES ('IMPORT','STRATEGY',?,?,?)",
                    (f"{import_type.upper()} 信號匯入完成", json.dumps({"user_id": user_id, "job_id": job_id, "rows": len(signals)}), now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("signal_id 已存在；請使用新的唯一 ID 或重送完全相同的批次。") from exc
        return {"job_id": job_id, "row_count": len(signals), "created": True}

    def import_csv(self, user_id: int, plan: str, content: bytes, filename: str) -> dict[str, Any]:
        return self.import_signals(
            user_id, plan, parse_csv(content), import_type="csv", filename=filename
        )

    def export(self, user_id: int, limit: int = 500) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT signal_id,symbol,action,quantity,price,timestamp,strategy,confidence,disclaimer
               FROM imported_signals WHERE user_id=? ORDER BY timestamp DESC,id DESC LIMIT ?""",
            (user_id, max(1, min(int(limit), MAX_SIGNALS))),
        )
