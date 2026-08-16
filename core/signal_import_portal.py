"""Owner-scoped CSV signal import portal.

This boundary is deliberately narrower than :mod:`core.signal_imports`.  It
accepts only a server-resolved ``csv_import`` capability, stores research
history, and never creates an order or sends an operational notification.
"""

from __future__ import annotations

import csv
from datetime import datetime
import hashlib
from io import StringIO
import json
import secrets
import sqlite3
from typing import Any, Callable, Mapping

from core.compat import UTC
from core.database import DatabaseManager, get_database
from core.signal_imports import MAX_CSV_BYTES, MAX_SIGNALS, parse_csv


CAPABILITY = "csv_import"
SAFETY_BOUNDARY = {
    "scope": "research_history_only",
    "creates_orders": False,
    "triggers_telegram": False,
    "touches_official_or_live": False,
}


class SignalImportPortalError(ValueError):
    status_code = 400

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        if status is not None:
            self.status_code = status


class SignalImportForbidden(SignalImportPortalError):
    status_code = 403


class SignalImportNotFound(SignalImportPortalError):
    status_code = 404


class SignalImportConflict(SignalImportPortalError):
    status_code = 409


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_signals(signals: list[dict[str, Any]]) -> str:
    return json.dumps(signals, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _public_job(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project a job without exposing its integer database id."""
    return {
        "public_id": row["public_id"],
        "import_type": row["import_type"],
        "filename": row.get("filename"),
        "status": row["status"],
        "row_count": int(row["row_count"] or 0),
        "error_message": row.get("error_message"),
        "created_at": row.get("created_at"),
        "completed_at": row.get("completed_at"),
        "source_sha256": row.get("source_hash"),
        "request_sha256": row.get("request_sha256"),
        "provenance_sha256": row.get("provenance_sha256"),
        "replayed": bool(row.get("replayed", False)),
        "safety_boundary": dict(SAFETY_BOUNDARY),
    }


def _public_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": row["signal_id"],
        "symbol": row["symbol"],
        "action": row["action"],
        "quantity": row["quantity"],
        "price": row["price"],
        "timestamp": row["timestamp"],
        "strategy": row["strategy"],
        "confidence": row["confidence"],
        "disclaimer": row["disclaimer"],
    }


def _formula_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _parse_csv_compat(content: bytes) -> list[dict[str, Any]]:
    """Keep the legacy traditional header while accepting the UI's simplified alias."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Delegate the canonical UTF-8 error wording to the existing parser.
        return parse_csv(content)
    first, separator, remainder = text.partition("\n")
    if separator:
        first = first.replace("标的", "標的")
        content = (first + separator + remainder).encode("utf-8")
    return parse_csv(content)


class SignalImportPortalService:
    """Persistence and policy service for the CSV research-history portal."""

    def __init__(
        self,
        database: DatabaseManager | None = None,
        *,
        authorize: Callable[[int, str], Any] | None = None,
        quota_resolver: Callable[[int], int | None] | None = None,
    ) -> None:
        self.db = database or get_database()
        self.authorize = authorize or (lambda _owner_id, _capability: False)
        self.quota_resolver = quota_resolver or (lambda _owner_id: 3)

    def _allowed(self, owner_id: int) -> bool:
        try:
            result = self.authorize(owner_id, CAPABILITY)
        except Exception:
            return False
        if isinstance(result, Mapping):
            return bool(result.get("allowed") and result.get("verified", True))
        return bool(result)

    def _require_allowed(self, owner_id: int) -> None:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
            raise SignalImportForbidden("账户身份无效。")
        if not self._allowed(owner_id):
            raise SignalImportForbidden("当前账户未获得已验证的 CSV 股票记录导入权限。")

    def _quota(self, owner_id: int) -> tuple[int, int | None]:
        try:
            limit = self.quota_resolver(owner_id)
        except Exception as exc:
            raise SignalImportPortalError("CSV 导入额度暂时不可用。", 503) from exc
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
            raise SignalImportPortalError("CSV 导入额度配置无效。", 503)
        today = datetime.now(UTC).date().isoformat()
        row = self.db.fetch_one(
            "SELECT COUNT(*) AS count FROM signal_import_jobs WHERE user_id=? AND import_type='csv' AND created_at LIKE ?",
            (owner_id, f"{today}%"),
        )
        return int(row["count"] if row else 0), limit

    def readiness(self, owner_id: int) -> dict[str, Any]:
        self._require_allowed(owner_id)
        used, limit = self._quota(owner_id)
        return {
            "capability": CAPABILITY,
            "allowed": True,
            "quota": {"used": used, "limit": limit, "remaining": None if limit is None else max(0, limit - used)},
            "limits": {"max_bytes": MAX_CSV_BYTES, "max_rows": MAX_SIGNALS},
            "safety_boundary": dict(SAFETY_BOUNDARY),
        }

    def _existing_by_key(self, owner_id: int, key: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            "SELECT * FROM signal_import_jobs WHERE user_id=? AND import_type='csv' AND idempotency_key=?",
            (owner_id, key),
        )

    def _existing_by_source(self, owner_id: int, source_hash: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            "SELECT * FROM signal_import_jobs WHERE user_id=? AND import_type='csv' AND source_hash=?",
            (owner_id, source_hash),
        )

    def import_csv(
        self,
        owner_id: int,
        content: bytes,
        filename: str,
        idempotency_key: str,
        *,
        request_sha256: str | None = None,
    ) -> dict[str, Any]:
        self._require_allowed(owner_id)
        if not isinstance(content, bytes) or not content or len(content) > MAX_CSV_BYTES:
            raise SignalImportPortalError("CSV 文件必须介于 1 byte 与 256 KB。")
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 160 or any(char in filename for char in ("/", "\\", "\x00")):
            raise SignalImportPortalError("CSV 文件名无效。")
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
            raise SignalImportPortalError("缺少有效的 Idempotency-Key。")
        request_hash = _sha256(content)
        if request_sha256 is not None and request_sha256 != request_hash:
            raise SignalImportConflict("请求内容校验失败。")
        existing_key = self._existing_by_key(owner_id, idempotency_key)
        if existing_key:
            if existing_key.get("request_sha256") != request_hash:
                raise SignalImportConflict("同一 Idempotency-Key 不可用于不同 CSV 内容。")
            result = _public_job(existing_key)
            result["replayed"] = True
            return result
        try:
            signals = _parse_csv_compat(content)
        except ValueError as exc:
            raise SignalImportPortalError(str(exc).replace("標的", "股票")) from exc
        source_hash = _sha256(_canonical_signals(signals).encode("utf-8"))
        existing_source = self._existing_by_source(owner_id, source_hash)
        if existing_source:
            # A duplicate batch is a safe historical replay and never consumes quota.
            result = _public_job(existing_source)
            result["replayed"] = True
            return result
        used, limit = self._quota(owner_id)
        if limit is not None and used >= limit:
            raise SignalImportForbidden(f"CSV 每日导入上限为 {limit} 次。")
        provenance = json.dumps(
            {"filename": filename, "import_type": "csv", "request_sha256": request_hash, "source_sha256": source_hash, "row_count": len(signals)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        provenance_hash = _sha256(provenance.encode("utf-8"))
        now = _now()
        public_id = "sigjob_" + secrets.token_urlsafe(18)
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    """INSERT INTO signal_import_jobs
                       (user_id,import_type,filename,status,row_count,error_message,report_json,source_hash,created_at,completed_at,public_id,idempotency_key,request_sha256,provenance_sha256)
                       VALUES (?,?,?,'validated',?,NULL,?,?,?, ?,?,?,?,?)""",
                    (owner_id, "csv", filename.strip()[:160], len(signals), json.dumps({"validated": len(signals), "safety_boundary": SAFETY_BOUNDARY}, ensure_ascii=False), source_hash, now, now, public_id, idempotency_key, request_hash, provenance_hash),
                )
                job_id = int(cursor.lastrowid)
                conn.executemany(
                    """INSERT INTO imported_signals
                       (job_id,user_id,signal_id,symbol,action,quantity,price,timestamp,strategy,confidence,disclaimer,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(job_id, owner_id, s["signal_id"], s["symbol"], s["action"], s["quantity"], s["price"], s["timestamp"], s["strategy"], s["confidence"], s["disclaimer"], now) for s in signals],
                )
                conn.execute(
                    "INSERT INTO system_events(event_type,component,message,details,created_at) VALUES ('IMPORT','STRATEGY',?,?,?)",
                    ("CSV 股票记录导入完成", json.dumps({"user_id": owner_id, "job_public_id": public_id, "rows": len(signals), "safety_boundary": SAFETY_BOUNDARY}, ensure_ascii=False), now),
                )
        except sqlite3.IntegrityError as exc:
            # A concurrent identical request can safely re-read its committed row.
            existing = self._existing_by_key(owner_id, idempotency_key) or self._existing_by_source(owner_id, source_hash)
            if existing and existing.get("request_sha256") == request_hash:
                result = _public_job(existing)
                result["replayed"] = True
                return result
            raise SignalImportConflict("CSV 导入请求发生冲突。") from exc
        row = self.db.fetch_one("SELECT * FROM signal_import_jobs WHERE public_id=? AND user_id=?", (public_id, owner_id))
        return _public_job(row or {"public_id": public_id, "import_type": "csv", "status": "validated", "row_count": len(signals), "created_at": now, "completed_at": now, "source_hash": source_hash, "request_sha256": request_hash, "provenance_sha256": provenance_hash})

    def list_jobs(self, owner_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        self._require_allowed(owner_id)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise SignalImportPortalError("查询数量无效。")
        limit = max(1, min(limit, 100))
        return [_public_job(row) for row in self.db.fetch_all("SELECT * FROM signal_import_jobs WHERE user_id=? AND import_type='csv' ORDER BY created_at DESC,id DESC LIMIT ?", (owner_id, limit))]

    def get_job(self, owner_id: int, public_id: str) -> dict[str, Any]:
        self._require_allowed(owner_id)
        row = self.db.fetch_one("SELECT * FROM signal_import_jobs WHERE user_id=? AND import_type='csv' AND public_id=?", (owner_id, public_id))
        if not row:
            raise SignalImportNotFound("CSV 导入记录不存在。")
        result = _public_job(row)
        result["signal_count"] = int(row["row_count"] or 0)
        return result

    def list_signals(self, owner_id: int, public_id: str, *, limit: int = MAX_SIGNALS) -> list[dict[str, Any]]:
        self.get_job(owner_id, public_id)
        rows = self.db.fetch_all("SELECT signal_id,symbol,action,quantity,price,timestamp,strategy,confidence,disclaimer FROM imported_signals WHERE user_id=? AND job_id=(SELECT id FROM signal_import_jobs WHERE user_id=? AND public_id=?) ORDER BY timestamp DESC,id DESC LIMIT ?", (owner_id, owner_id, public_id, max(1, min(int(limit), MAX_SIGNALS))))
        return [_public_signal(row) for row in rows]

    def export_csv(self, owner_id: int, public_id: str | None = None) -> bytes:
        self._require_allowed(owner_id)
        if public_id is None:
            rows = self.db.fetch_all("SELECT signal_id,symbol,action,quantity,price,timestamp,strategy,confidence,disclaimer FROM imported_signals WHERE user_id=? ORDER BY timestamp DESC,id DESC LIMIT ?", (owner_id, MAX_SIGNALS))
        else:
            self.get_job(owner_id, public_id)
            rows = self.db.fetch_all("SELECT signal_id,symbol,action,quantity,price,timestamp,strategy,confidence,disclaimer FROM imported_signals WHERE user_id=? AND job_id=(SELECT id FROM signal_import_jobs WHERE user_id=? AND public_id=?) ORDER BY timestamp DESC,id DESC LIMIT ?", (owner_id, owner_id, public_id, MAX_SIGNALS))
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        fields = ["股票代码", "操作", "数量", "价格", "时间", "策略", "信心", "免责声明"]
        writer.writerow(fields)
        for row in rows:
            writer.writerow([_formula_safe(row[key]) for key in ("symbol", "action", "quantity", "price", "timestamp", "strategy", "confidence", "disclaimer")])
        return output.getvalue().encode("utf-8-sig")

    # Stable descriptive aliases for adapters and callers that model the
    # portal as jobs/detail/signals/export resources.
    jobs = list_jobs
    detail = get_job
    signals = list_signals
    safe_csv_export = export_csv


# Explicit aliases make the boundary easy to discover without reusing the
# legacy plan-driven service.
SignalImportService = SignalImportPortalService

__all__ = [
    "CAPABILITY", "SAFETY_BOUNDARY", "SignalImportPortalError", "SignalImportForbidden",
    "SignalImportNotFound", "SignalImportConflict", "SignalImportPortalService", "SignalImportService",
]
