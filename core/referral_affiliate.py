# -*- coding: utf-8 -*-
"""Immutable referral attribution, cash commission ledger and manual payouts."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import hashlib
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


class ReferralService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def ensure_profile_in_transaction(conn: Any, user_id: int, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        row = conn.execute(
            "SELECT * FROM referral_profiles WHERE user_id=?", (int(user_id),)
        ).fetchone()
        if row:
            return dict(row)
        for _ in range(8):
            code = _invite_code()
            try:
                conn.execute(
                    "INSERT INTO referral_profiles(user_id,public_id,invite_code,created_at) VALUES (?,?,?,?)",
                    (int(user_id), _public_id("USR"), code, _iso(moment)),
                )
                return dict(conn.execute(
                    "SELECT * FROM referral_profiles WHERE user_id=?", (int(user_id),)
                ).fetchone())
            except Exception as exc:
                if "UNIQUE constraint failed: referral_profiles.invite_code" not in str(exc):
                    raise
        raise RuntimeError("无法建立唯一推广码。")

    def ensure_profile(self, user_id: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute("SELECT is_active FROM users WHERE id=?", (int(user_id),)).fetchone()
            if not user or not user["is_active"]:
                raise PermissionError("账户不存在或已停用。")
            return self.ensure_profile_in_transaction(conn, int(user_id))

    def record_visit(
        self, invite_code: str, fingerprint_hash: str, rate_key_hash: str,
        now: datetime | None = None,
    ) -> bool:
        code = str(invite_code or "").strip()
        fingerprint = str(fingerprint_hash or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("推广访问指纹无效。")
        rate_key = str(rate_key_hash or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", rate_key):
            raise ValueError("推广访问限流键无效。")
        moment = now or datetime.now(UTC)
        day_bucket = moment.astimezone(HONG_KONG).date().isoformat()
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profile = conn.execute(
                "SELECT user_id FROM referral_profiles WHERE invite_code=? AND disabled_at IS NULL",
                (code,),
            ).fetchone()
            if not profile:
                raise ValueError("推广码无效。")
            conn.execute(
                "DELETE FROM referral_visit_daily WHERE datetime(expires_at)<datetime(?)",
                (_iso(moment),),
            )
            conn.execute(
                "DELETE FROM referral_visit_rate_limits WHERE datetime(expires_at)<datetime(?)",
                (_iso(moment),),
            )
            rate = conn.execute(
                "SELECT * FROM referral_visit_rate_limits WHERE rate_key_hash=?", (rate_key,)
            ).fetchone()
            if rate and datetime.fromisoformat(str(rate["window_started_at"])).replace(
                tzinfo=UTC
            ) > moment - timedelta(hours=1):
                if int(rate["attempts"]) >= 60:
                    raise PermissionError("推广访问过于频繁，请稍后再试。")
                conn.execute(
                    "UPDATE referral_visit_rate_limits SET attempts=attempts+1 WHERE rate_key_hash=?",
                    (rate_key,),
                )
            else:
                conn.execute(
                    """INSERT INTO referral_visit_rate_limits
                       (rate_key_hash,attempts,window_started_at,expires_at) VALUES (?,1,?,?)
                       ON CONFLICT(rate_key_hash) DO UPDATE SET attempts=1,
                         window_started_at=excluded.window_started_at,expires_at=excluded.expires_at""",
                    (rate_key, _iso(moment), _iso(moment + timedelta(hours=2))),
                )
            inserted = conn.execute(
                """INSERT OR IGNORE INTO referral_visit_daily
                   (profile_user_id,day_bucket,fingerprint_hash,created_at,expires_at)
                   VALUES (?,?,?,?,?)""",
                (profile["user_id"], day_bucket, fingerprint, _iso(moment), _iso(moment + timedelta(days=35))),
            )
            return inserted.rowcount == 1

    @staticmethod
    def attach_at_registration(
        conn: Any,
        referred_user_id: int,
        invite: str,
        *,
        source: str = "web",
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        value = str(invite or "").strip()
        if not value:
            return None
        moment = now or datetime.now(UTC)
        profile = conn.execute(
            """SELECT p.*,u.is_active FROM referral_profiles p
               JOIN users u ON u.id=p.user_id WHERE p.invite_code=?""",
            (value,),
        ).fetchone()
        if not profile and re.fullmatch(r"TAI\d{8}", value.upper()):
            legacy_id = int(value[3:])
            user = conn.execute(
                "SELECT id,is_active FROM users WHERE id=?", (legacy_id,)
            ).fetchone()
            if user and user["is_active"]:
                profile = ReferralService.ensure_profile_in_transaction(conn, legacy_id, moment)
                profile = {**profile, "is_active": 1}
        if profile is not None and not isinstance(profile, dict):
            profile = dict(profile)
        if not profile or not profile["is_active"] or profile.get("disabled_at"):
            return None
        referrer_id = int(profile["user_id"])
        referred_id = int(referred_user_id)
        if referrer_id == referred_id:
            raise ValueError("不能推荐自己。")
        cycle = conn.execute(
            """WITH RECURSIVE ancestors(user_id) AS (
                   SELECT referrer_user_id FROM referral_attributions WHERE referred_user_id=?
                   UNION ALL
                   SELECT a.referrer_user_id FROM referral_attributions a
                   JOIN ancestors x ON a.referred_user_id=x.user_id
               ) SELECT 1 FROM ancestors WHERE user_id=? LIMIT 1""",
            (referrer_id, referred_id),
        ).fetchone()
        if cycle:
            raise ValueError("推广关系不能形成循环。")
        public_id = _public_id("RFR")
        conn.execute(
            """INSERT INTO referral_attributions
               (public_id,referrer_user_id,referred_user_id,invite_code_snapshot,source,attributed_at)
               VALUES (?,?,?,?,?,?)""",
            (public_id, referrer_id, referred_id, value, source, _iso(moment)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO referrals(referrer_id,referee_id,status,created_at) VALUES (?,?,?,?)",
            (referrer_id, referred_id, "registered", _iso(moment)),
        )
        _audit(
            conn, actor_user_id=referred_id, actor_kind="user", action="REFERRAL_ATTRIBUTED",
            entity_type="attribution", entity_public_id=public_id,
            details={"referrer_user_id": referrer_id, "source": source}, now=moment,
        )
        return dict(conn.execute(
            "SELECT * FROM referral_attributions WHERE public_id=?", (public_id,)
        ).fetchone())

    def portal(self, user_id: int, *, base_url: str = "") -> dict[str, Any]:
        profile = self.ensure_profile(int(user_id))
        with self.db.transaction() as conn:
            enabled = _enabled(conn)
            cutover_at = _control(conn, "referral_cash_cutover_at", "").strip() or None
            if enabled:
                ReferralCommissionService.release_due_in_transaction(conn, int(user_id), datetime.now(UTC))
            buckets = _balance_rows(conn, int(user_id))
            first_rate = _int_control(conn, "referral_first_rate_bps", 2000, 0, 10000)
            renewal_rate = _int_control(conn, "referral_repeat_rate_bps", 1000, 0, 10000)
            upgrade_rate = _int_control(conn, "referral_upgrade_rate_bps", 1000, 0, 10000)
            minimum = _int_control(conn, "referral_min_withdraw_minor", 10000, 1, 100_000_000)
            hold_days = _int_control(conn, "referral_hold_days", 14, 0, 365)
            raw_commissions = [dict(row) for row in conn.execute(
                """SELECT public_id,recharge_public_id,order_kind,gross_amount_minor,rate_bps,
                          commission_amount_minor,clawed_back_minor,currency,settled_at,available_at
                   FROM referral_commissions WHERE referrer_user_id=?
                   ORDER BY settled_at DESC,id DESC LIMIT 100""",
                (int(user_id),),
            ).fetchall()]
            pending_by_commission = {
                str(row["reference_id"]): int(row["balance"] or 0)
                for row in conn.execute(
                    """SELECT l.reference_id,SUM(l.amount_minor) balance
                       FROM referral_ledger_entries l
                       JOIN referral_journal_batches b ON b.id=l.batch_id
                       WHERE l.account_kind='user' AND l.user_id=? AND b.status='finalized'
                         AND l.reference_type='commission' AND l.bucket='pending'
                       GROUP BY reference_id""",
                    (int(user_id),),
                ).fetchall()
            }
            commissions = []
            for item in raw_commissions:
                earned = int(item["commission_amount_minor"])
                clawed = int(item["clawed_back_minor"])
                net = max(0, earned - clawed)
                pending = pending_by_commission.get(str(item["public_id"]), 0)
                status = (
                    "clawed_back" if net == 0 and clawed else
                    "partially_clawed_back" if clawed else
                    "pending" if pending > 0 else "withdrawable"
                )
                commissions.append({
                    "commission_id": item["public_id"],
                    "recharge_id": item["recharge_public_id"],
                    "commission_type": item["order_kind"],
                    "gross_amount_minor": int(item["gross_amount_minor"]),
                    "rate_bps": int(item["rate_bps"]),
                    "earned_amount_minor": earned,
                    "clawed_back_minor": clawed,
                    "net_amount_minor": net,
                    "status": status,
                    "settled_at": _hkt(item["settled_at"]),
                    "available_at": _hkt(item["available_at"]),
                })
            referrals = [
                {
                    "referral_id": row["public_id"],
                    "user_masked": _mask_email(row["email"]),
                    "joined_at": _hkt(row["attributed_at"]),
                    "settled_orders": int(row["settled_orders"] or 0),
                    "last_settled_at": _hkt(row["last_settled_at"]),
                }
                for row in conn.execute(
                    """SELECT a.public_id,a.attributed_at,u.email,
                              (SELECT COUNT(*) FROM referral_commissions c
                               WHERE c.attribution_id=a.id) settled_orders,
                              (SELECT MAX(settled_at) FROM referral_commissions c
                               WHERE c.attribution_id=a.id) last_settled_at
                       FROM referral_attributions a JOIN users u ON u.id=a.referred_user_id
                       WHERE a.referrer_user_id=? ORDER BY a.attributed_at DESC,a.id DESC LIMIT 100""",
                    (int(user_id),),
                ).fetchall()
            ]
            withdrawals = [{
                "withdrawal_id": row["public_id"], "amount_minor": int(row["amount_minor"]),
                "currency": row["currency"], "status": row["status"],
                "submitted_at": _hkt(row["submitted_at"]), "reviewed_at": _hkt(row["reviewed_at"]),
                "approved_at": _hkt(row["approved_at"]), "paid_at": _hkt(row["paid_at"]),
                "rejection_reason": row["rejection_reason"],
            } for row in conn.execute(
                """SELECT public_id,amount_minor,currency,status,submitted_at,reviewed_at,
                          rejection_reason,approved_at,cancelled_at,
                          (SELECT confirmed_at FROM referral_payout_confirmations p
                           WHERE p.withdrawal_id=referral_withdrawal_requests.id) paid_at
                   FROM referral_withdrawal_requests WHERE user_id=?
                   ORDER BY submitted_at DESC,id DESC LIMIT 100""",
                (int(user_id),),
            ).fetchall()]
            earned_total = int(conn.execute(
                "SELECT COALESCE(SUM(commission_amount_minor),0) FROM referral_commissions WHERE referrer_user_id=?",
                (int(user_id),),
            ).fetchone()[0])
            clawed_total = int(conn.execute(
                "SELECT COALESCE(SUM(clawed_back_minor),0) FROM referral_commissions WHERE referrer_user_id=?",
                (int(user_id),),
            ).fetchone()[0])
            windows = []
            for days in (7, 30, 90):
                since = _iso(datetime.now(UTC) - timedelta(days=days))
                stats = conn.execute(
                    """SELECT COUNT(*) settled_orders,COUNT(DISTINCT referred_user_id) settled_referrals,
                              COALESCE(SUM(gross_amount_minor),0) gross,
                              COALESCE(SUM(commission_amount_minor),0) earned,
                              COALESCE(SUM(clawed_back_minor),0) clawed
                       FROM referral_commissions WHERE referrer_user_id=? AND datetime(settled_at)>=datetime(?)""",
                    (int(user_id), since),
                ).fetchone()
                visits = int(conn.execute(
                    "SELECT COUNT(*) FROM referral_visit_daily WHERE profile_user_id=? AND date(day_bucket)>=date(?)",
                    (int(user_id), since),
                ).fetchone()[0])
                registrations = int(conn.execute(
                    "SELECT COUNT(*) FROM referral_attributions WHERE referrer_user_id=? AND datetime(attributed_at)>=datetime(?)",
                    (int(user_id), since),
                ).fetchone()[0])
                windows.append({
                    "days": days, "visits": visits, "registrations": registrations,
                    "settled_orders": int(stats["settled_orders"] or 0),
                    "gross_amount_minor": int(stats["gross"] or 0),
                    "earned_amount_minor": int(stats["earned"] or 0),
                    "clawed_back_minor": int(stats["clawed"] or 0),
                })
            thirty = windows[1]
            settled_referrals_30d = int(conn.execute(
                """SELECT COUNT(DISTINCT referred_user_id) FROM referral_commissions
                   WHERE referrer_user_id=? AND datetime(settled_at)>=datetime(?)""",
                (int(user_id), _iso(datetime.now(UTC) - timedelta(days=30))),
            ).fetchone()[0])
            timeline = [{
                "event_id": row["event_id"],
                "event_type": {
                    "REFERRAL_ATTRIBUTED": "registration", "COMMISSION_RECORDED": "commission_pending",
                    "COMMISSION_CLAWBACK": "clawback", "WITHDRAWAL_SUBMITTED": "withdrawal_submitted",
                    "WITHDRAWAL_APPROVED": "withdrawal_approved", "WITHDRAWAL_REJECTED": "withdrawal_rejected",
                    "WITHDRAWAL_PAID": "withdrawal_paid",
                }.get(row["action"], "withdrawal_cancelled"),
                "public_reference": row["entity_public_id"], "amount_minor": None,
                "occurred_at": _hkt(row["created_at"]),
            } for row in conn.execute(
                """SELECT event_id,action,entity_public_id,created_at FROM referral_audit_events
                   WHERE actor_user_id=? OR entity_public_id IN
                     (SELECT public_id FROM referral_commissions WHERE referrer_user_id=?)
                   ORDER BY created_at DESC,id DESC LIMIT 100""",
                (int(user_id), int(user_id)),
            ).fetchall()]
        prefix = str(base_url or "").rstrip("/")
        link = f"{prefix}/login?ref={profile['invite_code']}" if prefix else f"/login?ref={profile['invite_code']}"
        return {
            "program": {"enabled": enabled, "cutover_at": _hkt(cutover_at),
                        "currency": CURRENCY, "policy_version": POLICY_VERSION,
                        "first_rate_bps": first_rate, "renewal_rate_bps": renewal_rate,
                        "upgrade_rate_bps": upgrade_rate, "hold_days": hold_days,
                        "minimum_withdrawal_minor": minimum},
            "invite": {"invite_code": profile["invite_code"], "invite_link": link, "qr_payload": link},
            "balances": {"earned_total_minor": earned_total, "pending_minor": buckets["pending"],
                         "withdrawable_minor": max(0, buckets["available"]),
                         "reserved_minor": buckets["reserved"], "paid_minor": buckets["paid"],
                         "clawed_back_total_minor": clawed_total,
                         "debt_minor": max(0, -buckets["available"])},
            "trends": {"windows": windows},
            "funnel": {"visits_30d": thirty["visits"], "registrations_30d": thirty["registrations"],
                       "settled_referrals_30d": settled_referrals_30d,
                       "registration_rate_bps": (thirty["registrations"] * 10000 // thirty["visits"]) if thirty["visits"] else 0,
                       "settlement_rate_bps": (settled_referrals_30d * 10000 // thirty["registrations"]) if thirty["registrations"] else 0},
            "referrals": referrals, "commissions": commissions, "withdrawals": withdrawals,
            "timeline": timeline,
        }


class ReferralCommissionService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def record_settlement(
        conn: Any, order: dict[str, Any], pre_membership: dict[str, Any], now: datetime
    ) -> dict[str, Any] | None:
        if not _enabled(conn):
            return None
        attribution = conn.execute(
            "SELECT * FROM referral_attributions WHERE referred_user_id=?",
            (int(order["user_id"]),),
        ).fetchone()
        if not attribution:
            return None
        existing = conn.execute(
            "SELECT * FROM referral_commissions WHERE source_order_no=?", (order["order_no"],)
        ).fetchone()
        if existing:
            return dict(existing)
        cutover = _control(conn, "referral_cash_cutover_at", _iso(now))
        before_cutover = conn.execute(
            "SELECT datetime(?) < datetime(?)", (_iso(now), cutover)
        ).fetchone()[0]
        if before_cutover:
            return None
        sequence = int(conn.execute(
            """SELECT COUNT(*) FROM subscription_orders
               WHERE user_id=? AND status IN ('paid','refunded') AND paid_at IS NOT NULL AND
                     (datetime(paid_at)<datetime(?) OR (paid_at=? AND id<=?))""",
            (int(order["user_id"]), _iso(now), _iso(now), int(order["id"])),
        ).fetchone()[0])
        sequence = max(1, sequence)
        previous_plan = str(pre_membership.get("plan_type") or "免费版")
        target_plan = str(order["plan_type"])
        if sequence == 1:
            kind, rate_key, default_rate = "initial_purchase", "referral_first_rate_bps", 2000
        elif previous_plan == target_plan:
            kind, rate_key, default_rate = "renewal", "referral_repeat_rate_bps", 1000
        else:
            kind, rate_key, default_rate = "upgrade", "referral_upgrade_rate_bps", 1000
        rate_bps = _int_control(conn, rate_key, default_rate, 0, 10000)
        gross = int(order.get("amount_minor") or round(float(order["amount"]) * 100))
        if gross <= 0 or str(order.get("currency") or "").upper() != CURRENCY:
            return None
        commission_minor = gross * rate_bps // 10000
        hold_days = _int_control(conn, "referral_hold_days", 14, 0, 365)
        public_id = _public_id("COM")
        recharge_id = _public_id("RCH")
        available_at = now + timedelta(days=hold_days)
        conn.execute(
            """INSERT INTO referral_commissions
               (public_id,recharge_public_id,attribution_id,referrer_user_id,referred_user_id,
                source_order_no,settlement_sequence,order_kind,gross_amount_minor,rate_bps,
                commission_amount_minor,currency,policy_version,settled_at,available_at,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                public_id, recharge_id, attribution["id"], attribution["referrer_user_id"],
                attribution["referred_user_id"], order["order_no"], sequence, kind, gross,
                rate_bps, commission_minor, CURRENCY, POLICY_VERSION, _iso(now),
                _iso(available_at), _iso(now),
            ),
        )
        if commission_minor:
            _ledger_batch(
                conn, user_id=int(attribution["referrer_user_id"]),
                legs=[("pending", commission_minor)], entry_type="commission_award",
                group_key=f"commission:{public_id}", reference_type="commission",
                reference_id=public_id, batch_key=f"commission:{order['order_no']}:award",
                now=now,
            )
        _audit(
            conn, actor_user_id=None, actor_kind="system", action="COMMISSION_RECORDED",
            entity_type="commission", entity_public_id=public_id,
            details={"order_kind": kind, "rate_bps": rate_bps, "amount_minor": commission_minor},
            now=now,
        )
        return dict(conn.execute(
            "SELECT * FROM referral_commissions WHERE public_id=?", (public_id,)
        ).fetchone())

    @staticmethod
    def release_due_in_transaction(conn: Any, user_id: int | None, now: datetime) -> int:
        if not _enabled(conn):
            return 0
        params: tuple[Any, ...]
        clause = ""
        if user_id is None:
            params = (_iso(now),)
        else:
            clause = " AND referrer_user_id=?"
            params = (_iso(now), int(user_id))
        rows = conn.execute(
            f"""SELECT * FROM referral_commissions c
                WHERE datetime(available_at)<=datetime(?) {clause}
                  AND EXISTS (SELECT 1 FROM referral_ledger_entries l
                              JOIN referral_journal_batches b ON b.id=l.batch_id
                              WHERE l.account_kind='user' AND b.status='finalized'
                                AND l.reference_type='commission' AND l.reference_id=c.public_id
                              GROUP BY l.reference_id
                              HAVING SUM(CASE WHEN l.bucket='pending' THEN l.amount_minor ELSE 0 END)>0)
                ORDER BY id""",
            params,
        ).fetchall()
        released = 0
        for row in rows:
            pending = int(conn.execute(
                """SELECT COALESCE(SUM(l.amount_minor),0) FROM referral_ledger_entries l
                   JOIN referral_journal_batches b ON b.id=l.batch_id
                   WHERE l.account_kind='user' AND b.status='finalized'
                     AND l.reference_type='commission' AND l.reference_id=? AND l.bucket='pending'""",
                (row["public_id"],),
            ).fetchone()[0])
            if pending <= 0:
                continue
            group = f"commission:{row['public_id']}:mature"
            if _ledger_batch(
                conn, user_id=int(row["referrer_user_id"]),
                legs=[("pending", -pending), ("available", pending)],
                entry_type="commission_mature", group_key=group,
                reference_type="commission", reference_id=row["public_id"],
                batch_key=group, now=now,
            ):
                released += 1
        return released

    def release_due(self, user_id: int | None = None, now: datetime | None = None) -> int:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self.release_due_in_transaction(conn, user_id, now or datetime.now(UTC))

    @staticmethod
    def record_reversal(
        conn: Any,
        *,
        event_key: str,
        order: dict[str, Any],
        amount_minor: int,
        reason: str,
        now: datetime,
    ) -> bool:
        commission = conn.execute(
            "SELECT * FROM referral_commissions WHERE source_order_no=?", (order["order_no"],)
        ).fetchone()
        if not commission:
            return False
        if not _enabled(conn):
            raise PermissionError("推广现金计划尚未启用。")
        if conn.execute(
            "SELECT 1 FROM referral_reversal_events WHERE event_key=?", (event_key,)
        ).fetchone():
            return False
        amount = int(amount_minor)
        remaining_gross = int(commission["gross_amount_minor"]) - int(commission["reversed_amount_minor"])
        if amount != remaining_gross:
            raise ValueError("推广佣金仅支持经验证的全额逆转。")
        kind = "chargeback" if "chargeback" in reason.lower() else "dispute" if "dispute" in reason.lower() else "refund"
        reversal_id = _public_id("REV")
        conn.execute(
            """INSERT INTO referral_reversal_events
               (public_id,event_key,source_order_no,reversal_kind,amount_minor,reason,recorded_at)
               VALUES (?,?,?,?,?,?,?)""",
            (reversal_id, event_key, order["order_no"], kind, amount, reason[:160], _iso(now)),
        )
        cumulative = int(commission["reversed_amount_minor"]) + amount
        remaining_commission = (
            (int(commission["gross_amount_minor"]) - cumulative) * int(commission["rate_bps"]) // 10000
        )
        target_claw = int(commission["commission_amount_minor"]) - remaining_commission
        claw = target_claw - int(commission["clawed_back_minor"])
        conn.execute(
            """UPDATE referral_commissions
               SET reversed_amount_minor=?,clawed_back_minor=? WHERE id=?""",
            (cumulative, target_claw, commission["id"]),
        )
        if claw > 0:
            user_id = int(commission["referrer_user_id"])
            pending = int(conn.execute(
                """SELECT COALESCE(SUM(l.amount_minor),0) FROM referral_ledger_entries l
                   JOIN referral_journal_batches b ON b.id=l.batch_id
                   WHERE l.account_kind='user' AND b.status='finalized'
                     AND l.reference_type='commission' AND l.reference_id=? AND l.bucket='pending'""",
                (commission["public_id"],),
            ).fetchone()[0])
            from_pending = min(claw, max(0, pending))
            if from_pending:
                _ledger_batch(
                    conn, user_id=user_id, legs=[("pending", -from_pending)],
                    entry_type="commission_clawback", group_key=f"reversal:{reversal_id}",
                    reference_type="reversal", reference_id=reversal_id,
                    batch_key=f"reversal:{event_key}:pending", now=now,
                )
            remainder = claw - from_pending
            if remainder:
                ReferralWalletService.cancel_open_withdrawal_in_transaction(
                    conn, user_id, reason="原订单发生退款或拒付，系统已释放待付款金额。", now=now
                )
                _ledger_batch(
                    conn, user_id=user_id, legs=[("available", -remainder)],
                    entry_type="commission_clawback", group_key=f"reversal:{reversal_id}",
                    reference_type="reversal", reference_id=reversal_id,
                    batch_key=f"reversal:{event_key}:available", now=now,
                )
        _audit(
            conn, actor_user_id=None, actor_kind="system", action="COMMISSION_CLAWBACK",
            entity_type="commission", entity_public_id=commission["public_id"],
            details={"reversal_id": reversal_id, "gross_reversed_minor": amount, "clawback_minor": claw},
            now=now,
        )
        return True


class ReferralWalletService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def balances(self, user_id: int) -> dict[str, int]:
        self.release_due(int(user_id))
        with self.db.transaction() as conn:
            return _balance_rows(conn, int(user_id))

    def release_due(self, user_id: int) -> None:
        ReferralCommissionService(self.db).release_due(int(user_id))

    def request_withdrawal(
        self, user_id: int, amount_minor: int, idempotency_key: str
    ) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        if not 8 <= len(key) <= 128:
            raise ValueError("提款幂等键必须为 8 到 128 个字符。")
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
            raise ValueError("提款金额必须使用整数分。")
        fingerprint = hashlib.sha256(f"{amount_minor}:{CURRENCY}".encode()).hexdigest()
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _enabled(conn):
                raise PermissionError("推广现金计划尚未启用。")
            existing = conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE user_id=? AND idempotency_key=?",
                (int(user_id), key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != fingerprint:
                    raise ValueError("提款幂等键已用于不同请求。")
                return dict(existing)
            user = conn.execute("SELECT is_active FROM users WHERE id=?", (int(user_id),)).fetchone()
            if not user or not user["is_active"]:
                raise PermissionError("账户不存在或已停用。")
            recent = int(conn.execute(
                """SELECT COUNT(*) FROM referral_withdrawal_requests
                   WHERE user_id=? AND datetime(submitted_at)>=datetime(?)""",
                (int(user_id), _iso(now - timedelta(days=1))),
            ).fetchone()[0])
            if recent >= 3:
                raise ValueError("提款申请过于频繁，请稍后再试。")
            ReferralCommissionService.release_due_in_transaction(conn, int(user_id), now)
            minimum = _int_control(conn, "referral_min_withdraw_minor", 10000, 1, 100_000_000)
            available = _balance_rows(conn, int(user_id))["available"]
            if amount_minor < minimum:
                raise ValueError(f"最低提款金额为 {minimum} 分。")
            if available < amount_minor:
                raise ValueError("可提款余额不足。")
            public_id = _public_id("WDR")
            try:
                conn.execute(
                    """INSERT INTO referral_withdrawal_requests
                       (public_id,user_id,amount_minor,currency,status,idempotency_key,
                        request_fingerprint,submitted_at)
                       VALUES (?,?,?,?,'submitted',?,?,?)""",
                    (public_id, int(user_id), amount_minor, CURRENCY, key, fingerprint, _iso(now)),
                )
            except Exception as exc:
                if "idx_referral_withdrawal_open_user" in str(exc) or "UNIQUE constraint failed: referral_withdrawal_requests.user_id" in str(exc):
                    raise ValueError("已有提款申请正在处理。") from exc
                raise
            group = f"withdrawal:{public_id}:reserve"
            _ledger_batch(
                conn, user_id=int(user_id),
                legs=[("available", -amount_minor), ("reserved", amount_minor)],
                entry_type="withdrawal_reserved", group_key=group,
                reference_type="withdrawal", reference_id=public_id,
                batch_key=group, now=now,
            )
            _audit(
                conn, actor_user_id=int(user_id), actor_kind="user", action="WITHDRAWAL_SUBMITTED",
                entity_type="withdrawal", entity_public_id=public_id,
                details={"amount_minor": amount_minor, "currency": CURRENCY}, now=now,
            )
            return dict(conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE public_id=?", (public_id,)
            ).fetchone())

    @staticmethod
    def cancel_open_withdrawal_in_transaction(
        conn: Any, user_id: int, *, reason: str, now: datetime
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """SELECT * FROM referral_withdrawal_requests
               WHERE user_id=? AND status IN ('submitted','approved')""",
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        changed = conn.execute(
            """UPDATE referral_withdrawal_requests
               SET status='system_cancelled',rejection_reason=?,cancelled_at=?
               WHERE id=? AND status IN ('submitted','approved')""",
            (reason[:500], _iso(now), row["id"]),
        )
        if changed.rowcount != 1:
            return None
        group = f"withdrawal:{row['public_id']}:system-cancel"
        _ledger_batch(
            conn, user_id=int(user_id),
            legs=[("reserved", -int(row["amount_minor"])),
                  ("available", int(row["amount_minor"]))],
            entry_type="withdrawal_released", group_key=group, reference_type="withdrawal",
            reference_id=row["public_id"], batch_key=group, now=now,
        )
        _audit(
            conn, actor_user_id=None, actor_kind="system", action="WITHDRAWAL_SYSTEM_CANCELLED",
            entity_type="withdrawal", entity_public_id=row["public_id"],
            details={"reason": reason[:500]}, now=now,
        )
        return dict(conn.execute(
            "SELECT * FROM referral_withdrawal_requests WHERE id=?", (row["id"],)
        ).fetchone())

    def review(
        self, actor_id: int, public_id: str, decision: str, reason: str = ""
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("提款审核决定无效。")
        if decision == "reject" and not 1 <= len(str(reason).strip()) <= 500:
            raise ValueError("拒绝提款必须填写原因。")
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _enabled(conn):
                raise PermissionError("推广现金计划尚未启用。")
            from core.admin_service import AdminService
            AdminService._require_billing_in_transaction(conn, int(actor_id))
            row = conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE public_id=?", (public_id,)
            ).fetchone()
            if not row:
                raise ValueError("提款申请不存在。")
            if int(row["user_id"]) == int(actor_id):
                raise PermissionError("管理员不能审核自己的提款申请。")
            target = "approved" if decision == "approve" else "rejected"
            if row["status"] == target:
                return dict(row)
            if row["status"] not in {"submitted", "approved" if target == "rejected" else "submitted"}:
                raise ValueError("提款申请状态已变更。")
            if target == "approved":
                conn.execute(
                    """UPDATE referral_withdrawal_requests SET status='approved',reviewed_by=?,
                       reviewed_at=?,approved_by=?,approved_at=? WHERE id=? AND status='submitted'""",
                    (int(actor_id), _iso(now), int(actor_id), _iso(now), row["id"]),
                )
            else:
                conn.execute(
                    """UPDATE referral_withdrawal_requests SET status='rejected',reviewed_by=?,
                       reviewed_at=?,rejection_reason=? WHERE id=? AND status IN ('submitted','approved')""",
                    (int(actor_id), _iso(now), str(reason).strip(), row["id"]),
                )
                group = f"withdrawal:{public_id}:reject"
                _ledger_batch(
                    conn, user_id=int(row["user_id"]),
                    legs=[("reserved", -int(row["amount_minor"])),
                          ("available", int(row["amount_minor"]))],
                    entry_type="withdrawal_released",
                    group_key=group, reference_type="withdrawal", reference_id=public_id,
                    batch_key=group, now=now,
                )
            _audit(
                conn, actor_user_id=int(actor_id), actor_kind="admin",
                action=f"WITHDRAWAL_{target.upper()}", entity_type="withdrawal",
                entity_public_id=public_id, details={"reason": str(reason).strip()}, now=now,
            )
            return dict(conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE id=?", (row["id"],)
            ).fetchone())

    def confirm_paid(
        self, actor_id: int, public_id: str, payout_method: str, payout_reference: str
    ) -> dict[str, Any]:
        method = str(payout_method or "").strip().lower()
        if method not in {"fps", "bank", "other"}:
            raise ValueError("付款方式无效。")
        reference = _canonical_payout_reference(payout_reference)
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _enabled(conn):
                raise PermissionError("推广现金计划尚未启用。")
            from core.admin_service import AdminService
            AdminService._require_billing_in_transaction(conn, int(actor_id))
            row = conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE public_id=?", (public_id,)
            ).fetchone()
            if not row:
                raise ValueError("提款申请不存在。")
            if int(row["user_id"]) == int(actor_id):
                raise PermissionError("管理员不能确认自己的提款付款。")
            if row["status"] == "paid":
                confirmation = conn.execute(
                    "SELECT payout_reference FROM referral_payout_confirmations WHERE withdrawal_id=?",
                    (row["id"],),
                ).fetchone()
                if confirmation and confirmation["payout_reference"] == reference:
                    return dict(row)
                raise ValueError("提款已经确认付款。")
            if row["status"] != "approved":
                raise ValueError("只有已批准提款可以确认付款。")
            if int(row["approved_by"] or 0) == int(actor_id):
                raise PermissionError("批准人与确认付款人必须不同。")
            try:
                conn.execute(
                    """INSERT INTO referral_payout_confirmations
                       (public_id,withdrawal_id,payout_method,payout_reference,confirmed_by,confirmed_at)
                       VALUES (?,?,?,?,?,?)""",
                    (_public_id("PAY"), row["id"], method, reference, int(actor_id), _iso(now)),
                )
            except Exception as exc:
                if "payout_reference" in str(exc):
                    raise ValueError("付款参考编号已经使用。") from exc
                raise
            changed = conn.execute(
                "UPDATE referral_withdrawal_requests SET status='paid' WHERE id=? AND status='approved'",
                (row["id"],),
            )
            if changed.rowcount != 1:
                raise ValueError("提款申请状态已变更。")
            group = f"withdrawal:{public_id}:paid"
            _ledger_batch(
                conn, user_id=int(row["user_id"]),
                legs=[("reserved", -int(row["amount_minor"])),
                      ("paid", int(row["amount_minor"]))],
                entry_type="withdrawal_paid",
                group_key=group, reference_type="withdrawal", reference_id=public_id,
                batch_key=group, now=now,
            )
            _audit(
                conn, actor_user_id=int(actor_id), actor_kind="admin", action="WITHDRAWAL_PAID",
                entity_type="withdrawal", entity_public_id=public_id,
                details={"payout_method": method, "payout_reference_masked": f"***{reference[-4:]}"},
                now=now,
            )
            return dict(conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE id=?", (row["id"],)
            ).fetchone())

    def list_admin(self, actor_id: int, status: str = "submitted", limit: int = 100) -> list[dict[str, Any]]:
        from core.admin_service import AdminService
        service = AdminService(self.db)
        service._require(int(actor_id), "billing")
        if status not in {"submitted", "approved", "rejected", "paid", "system_cancelled", "all"}:
            raise ValueError("提款状态筛选无效。")
        clause, params = ("", ()) if status == "all" else ("WHERE w.status=?", (status,))
        rows = self.db.fetch_all(
            f"""SELECT w.public_id,w.amount_minor,w.currency,w.status,w.submitted_at,w.reviewed_at,
                       w.rejection_reason,w.approved_at,w.cancelled_at,p.confirmed_at paid_at,
                       rp.public_id user_reference,
                       CASE WHEN u.email LIKE '%@%' THEN substr(u.email,1,1) || '***@' ||
                            substr(substr(u.email,instr(u.email,'@')+1),1,1) || '***' ELSE '已隐藏用户' END user_masked
                FROM referral_withdrawal_requests w JOIN users u ON u.id=w.user_id
                JOIN referral_profiles rp ON rp.user_id=w.user_id
                LEFT JOIN referral_payout_confirmations p ON p.withdrawal_id=w.id
                {clause} ORDER BY w.submitted_at,w.id LIMIT ?""",
            (*params, max(1, min(int(limit), 500))),
        )
        return [{
            "withdrawal_id": row["public_id"], "user_reference": row["user_reference"],
            "user_masked": row["user_masked"], "amount_minor": int(row["amount_minor"]),
            "currency": row["currency"], "status": row["status"],
            "submitted_at": _hkt(row["submitted_at"]), "reviewed_at": _hkt(row["reviewed_at"]),
            "approved_at": _hkt(row["approved_at"]), "paid_at": _hkt(row["paid_at"]),
            "rejection_reason": row["rejection_reason"],
        } for row in rows]
