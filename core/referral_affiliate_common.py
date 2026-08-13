# -*- coding: utf-8 -*-
"""Immutable referral attribution, cash commission ledger and manual payouts."""
# ruff: noqa: E701, E702

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
import json
import re
import secrets
from typing import Any
import unicodedata
from zoneinfo import ZoneInfo

from core.database import DatabaseManager, get_database



CURRENCY = "HKD"
POLICY_VERSION = "cash-affiliate-v1"
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
_ID_PATTERN = re.compile(r"[A-Z]{3}[A-F0-9]{24}")


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat(timespec="seconds")


def _hkt(value: str | datetime | None) -> str | None:
    if value is None or value == "":
        return None
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(HONG_KONG).isoformat(timespec="seconds")


def _public_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(12).upper()}"


def _invite_code() -> str:
    return secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")


def _control(conn: Any, key: str, default: str) -> str:
    row = conn.execute(
        "SELECT control_value FROM platform_controls WHERE control_key=?", (key,)
    ).fetchone()
    return str(row[0]) if row else default


def _int_control(conn: Any, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_control(conn, key, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"推广参数 {key} 无效。") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"推广参数 {key} 超出允许范围。")
    return value


def _enabled(conn: Any) -> bool:
    return _control(conn, "referral_cash_enabled", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _audit(
    conn: Any,
    *,
    actor_user_id: int | None,
    actor_kind: str,
    action: str,
    entity_type: str,
    entity_public_id: str,
    details: dict[str, Any],
    now: datetime,
) -> None:
    conn.execute(
        """INSERT INTO referral_audit_events
           (event_id,actor_user_id,actor_kind,action,entity_type,entity_public_id,details_json,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            _public_id("AUD"), actor_user_id, actor_kind, action, entity_type,
            entity_public_id,
            json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            _iso(now),
        ),
    )


def _ledger_batch(
    conn: Any,
    *,
    user_id: int,
    legs: list[tuple[str, int]],
    entry_type: str,
    group_key: str,
    reference_type: str,
    reference_id: str,
    batch_key: str,
    now: datetime,
) -> bool:
    if not legs or any(int(amount) == 0 for _, amount in legs):
        raise ValueError("推广账本分录无效。")
    existing = conn.execute(
        "SELECT * FROM referral_journal_batches WHERE batch_key=?", (batch_key,)
    ).fetchone()
    if existing:
        rows = conn.execute(
            """SELECT account_kind,user_id,bucket,amount_minor,entry_type,group_key,
                      reference_type,reference_id
               FROM referral_ledger_entries WHERE batch_id=? ORDER BY bucket,account_kind,amount_minor""",
            (existing["id"],),
        ).fetchall()
        expected = sorted(
            (
                account_kind, int(user_id) if account_kind == "user" else None,
                bucket, int(amount if account_kind == "user" else -amount), entry_type,
                group_key, reference_type, reference_id,
            )
            for bucket, amount in legs
            for account_kind in ("user", "platform")
        )
        actual = sorted(tuple(row) for row in rows)
        if existing["status"] != "finalized" or existing["group_key"] != group_key or actual != expected:
            raise RuntimeError("推广账本幂等回执不一致。")
        return False
    batch = conn.execute(
        """INSERT INTO referral_journal_batches(batch_key,group_key,status,created_at)
           VALUES (?,?,'open',?)""",
        (batch_key, group_key, _iso(now)),
    )
    batch_id = int(batch.lastrowid)
    for index, (bucket, amount_minor) in enumerate(legs):
        for account_kind, line_user_id, amount, suffix in (
            ("user", int(user_id), int(amount_minor), "user"),
            ("platform", None, -int(amount_minor), "platform"),
        ):
            conn.execute(
                """INSERT INTO referral_ledger_entries
                   (public_id,batch_id,account_kind,user_id,bucket,amount_minor,currency,entry_type,
                    group_key,reference_type,reference_id,idempotency_key,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _public_id("LED"), batch_id, account_kind, line_user_id, bucket, amount,
                    CURRENCY, entry_type, group_key, reference_type, reference_id,
                    f"{batch_key}:{index}:{suffix}", _iso(now),
                ),
            )
    conn.execute(
        "UPDATE referral_journal_batches SET status='finalized',finalized_at=? WHERE id=? AND status='open'",
        (_iso(now), batch_id),
    )
    return True


class ReferralProgramService:
    """One-way release gate for the cash referral program."""

    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def enable(self, actor_id: int, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            from core.admin_service import AdminService
            AdminService._require_super_admin_in_transaction(conn, int(actor_id))
            raw_enabled = _control(conn, "referral_cash_enabled", "").strip()
            cutover = _control(conn, "referral_cash_cutover_at", "").strip()
            if raw_enabled == "1":
                if not cutover:
                    raise ValueError("推广计划控制状态不一致。")
                return {"enabled": True, "cutover_at": cutover}
            if raw_enabled != "0" or cutover:
                raise ValueError("推广计划控制状态无效，禁止启用。")
            controls = {
                "first_rate_bps": _int_control(conn, "referral_first_rate_bps", 2000, 0, 10000),
                "repeat_rate_bps": _int_control(conn, "referral_repeat_rate_bps", 1000, 0, 10000),
                "upgrade_rate_bps": _int_control(conn, "referral_upgrade_rate_bps", 1000, 0, 10000),
                "hold_days": _int_control(conn, "referral_hold_days", 14, 0, 365),
                "minimum_withdrawal_minor": _int_control(
                    conn, "referral_min_withdraw_minor", 10000, 1, 100_000_000
                ),
            }
            dirty = conn.execute(
                """SELECT 1 FROM referral_attributions
                   WHERE referrer_user_id=referred_user_id LIMIT 1"""
            ).fetchone()
            if dirty:
                raise ValueError("历史推广关系未通过启用前检查。")
            cutover = _iso(moment)
            enabled_update = conn.execute(
                """UPDATE platform_controls SET control_value='1',updated_by=?,updated_at=?
                   WHERE control_key='referral_cash_enabled' AND control_value='0'""",
                (int(actor_id), cutover),
            )
            cutover_update = conn.execute(
                """UPDATE platform_controls SET control_value=?,updated_by=?,updated_at=?
                   WHERE control_key='referral_cash_cutover_at' AND control_value=''""",
                (cutover, int(actor_id), cutover),
            )
            if enabled_update.rowcount != 1 or cutover_update.rowcount != 1:
                raise ValueError("推广计划控制状态已变更，请重试。")
            _audit(
                conn, actor_user_id=int(actor_id), actor_kind="admin",
                action="REFERRAL_PROGRAM_ENABLED", entity_type="program",
                entity_public_id="REFERRAL_CASH_V1",
                details={"cutover_at": cutover, **controls}, now=moment,
            )
            return {"enabled": True, "cutover_at": cutover}


def _balance_rows(conn: Any, user_id: int) -> dict[str, int]:
    rows = conn.execute(
        """SELECT bucket,COALESCE(SUM(amount_minor),0) balance
           FROM referral_ledger_entries l JOIN referral_journal_batches b ON b.id=l.batch_id
           WHERE l.account_kind='user' AND l.user_id=? AND l.currency=? AND b.status='finalized'
           GROUP BY bucket""",
        (int(user_id), CURRENCY),
    ).fetchall()
    result = {"pending": 0, "available": 0, "reserved": 0, "paid": 0}
    for row in rows:
        result[str(row["bucket"])] = int(row["balance"] or 0)
    return result


def _canonical_payout_reference(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    canonical = re.sub(r"[\s\-_.:/]+", "", normalized)
    if not re.fullmatch(r"[A-Z0-9]{6,64}", canonical):
        raise ValueError("付款参考编号必须为 6 到 64 个英文字母或数字。")
    return canonical


def _mask_email(value: str) -> str:
    local, _, domain = str(value or "").partition("@")
    host, dot, suffix = domain.partition(".")
    if not local or not host:
        return "已隐藏用户"
    return f"{local[:1]}***@{host[:1]}***{dot}{suffix}"[:80]
