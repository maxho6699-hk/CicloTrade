from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from typing import Any

from core.compat import UTC
from core.database import DatabaseManager, get_database
from core.referral_affiliate_common import (
    CURRENCY,
    _audit,
    _balance_rows,
    _canonical_payout_reference,
    _enabled,
    _hkt,
    _iso,
    _ledger_batch,
    _public_id,
)
from core.referral_commission import ReferralCommissionService


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
            from core.referral_coupon import ReferralCouponService
            _policy_version, policy = ReferralCouponService._policy(conn)
            existing = conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE user_id=? AND idempotency_key=?",
                (int(user_id), key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != fingerprint:
                    raise ValueError("提款幂等键已用于不同请求。")
                return dict(existing)
            if bool(policy.get("withdrawal_paused", False)):
                raise PermissionError("推广提款目前已暂停。")
            user = conn.execute("SELECT is_active FROM users WHERE id=?", (int(user_id),)).fetchone()
            if not user or not user["is_active"]:
                raise PermissionError("账户不存在或已停用。")
            recent = int(conn.execute(
                """SELECT COUNT(*) FROM referral_withdrawal_requests
                   WHERE user_id=? AND datetime(submitted_at)>=datetime(?)""",
                (int(user_id), _iso(now - timedelta(days=1))),
            ).fetchone()[0])
            if recent >= int(policy.get("withdrawal_daily_limit", 3)):
                raise ValueError("提款申请过于频繁，请稍后再试。")
            monthly = int(conn.execute(
                """SELECT COUNT(*) FROM referral_withdrawal_requests
                   WHERE user_id=? AND strftime('%Y-%m',submitted_at)=strftime('%Y-%m',?)""",
                (int(user_id), _iso(now)),
            ).fetchone()[0])
            if monthly >= int(policy.get("withdrawal_monthly_limit", 2)):
                raise ValueError("本自然月提款次数已达上限。")
            open_count = int(conn.execute(
                "SELECT COUNT(*) FROM referral_withdrawal_requests WHERE user_id=? AND status IN ('submitted','approved')",
                (int(user_id),),
            ).fetchone()[0])
            if open_count >= int(policy.get("withdrawal_open_limit", 1)):
                raise ValueError("已有待处理提款申请。")
            cooldown_days = int(policy.get("withdrawal_cooldown_days", 0))
            if cooldown_days:
                previous = conn.execute(
                    """SELECT submitted_at FROM referral_withdrawal_requests WHERE user_id=?
                       ORDER BY datetime(submitted_at) DESC,id DESC LIMIT 1""",
                    (int(user_id),),
                ).fetchone()
                if previous and datetime.fromisoformat(str(previous["submitted_at"])).astimezone(UTC) > now - timedelta(days=cooldown_days):
                    raise ValueError("提款冷却期尚未结束。")
            ReferralCommissionService.release_due_in_transaction(conn, int(user_id), now)
            minimum = int(policy.get("withdrawal_min_minor", 20_000))
            maximum = int(policy.get("withdrawal_max_minor", 500_000))
            available = _balance_rows(conn, int(user_id))["available"]
            if amount_minor < minimum:
                raise ValueError(f"最低提款金额为 {minimum} 分。")
            if amount_minor > maximum:
                raise ValueError(f"单笔提款最高金额为 {maximum} 分。")
            if available < amount_minor:
                raise ValueError("可提款余额不足。")
            public_id = _public_id("WDR")
            enhanced_review = int(amount_minor >= int(policy.get("automatic_payout_review_threshold_minor", 100_000_000)))
            try:
                conn.execute(
                    """INSERT INTO referral_withdrawal_requests
                       (public_id,user_id,amount_minor,currency,status,idempotency_key,
                        request_fingerprint,submitted_at,enhanced_review_required)
                       VALUES (?,?,?,?,'submitted',?,?,?,?)""",
                    (public_id, int(user_id), amount_minor, CURRENCY, key, fingerprint, _iso(now), enhanced_review),
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
            if target == "approved" and int(row["enhanced_review_required"]):
                if not 1 <= len(str(reason).strip()) <= 500:
                    raise ValueError("高金额提款批准必须填写增强审核说明。")
                if AdminService._require_super_admin_in_transaction(conn, int(actor_id)) != "super_admin":
                    raise PermissionError("高金额提款须由超级管理员完成增强审核。")
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
