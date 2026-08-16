from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
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

    @staticmethod
    def withdrawal_eligibility_in_transaction(
        conn: Any,
        user_id: int,
        *,
        now: datetime,
        amount_minor: int | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Project withdrawal gates without reserving funds or creating rows."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("提现资格检查时间必须包含时区。")
        from core.referral_coupon import ReferralCouponService

        active_policy = policy or ReferralCouponService._policy(conn)[1]
        minimum = int(active_policy.get("withdrawal_min_minor", 20_000))
        maximum = int(active_policy.get("withdrawal_max_minor", 500_000))
        moment = now.astimezone(UTC)
        evaluated_at = _iso(moment)
        balances = _balance_rows(conn, int(user_id))
        available = int(balances["available"])

        def result(status: str, reason: str, next_eligible_at: str | None = None) -> dict[str, Any]:
            return {
                "status": status,
                "reason_code": status,
                "reason": reason,
                "min_minor": minimum,
                "max_minor": maximum,
                "available_minor": max(0, available),
                "next_eligible_at": next_eligible_at,
                "evaluated_at": evaluated_at,
            }

        if not _enabled(conn):
            return result("ineligible", "推广现金计划尚未启用。")
        user = conn.execute("SELECT is_active FROM users WHERE id=?", (int(user_id),)).fetchone()
        if not user or not user["is_active"]:
            return result("ineligible", "账户不存在或已停用。")
        if bool(active_policy.get("withdrawal_paused", False)):
            return result("paused", "推广提款目前已暂停。")
        if available < 0:
            return result("debt", "账户存在待抵扣负余额，暂不可提现。")

        daily_limit = int(active_policy.get("withdrawal_daily_limit", 3))
        recent_rows = conn.execute(
            """SELECT submitted_at FROM referral_withdrawal_requests
               WHERE user_id=? AND datetime(submitted_at)>=datetime(?)
               ORDER BY datetime(submitted_at),id""",
            (int(user_id), _iso(moment - timedelta(days=1))),
        ).fetchall()
        if len(recent_rows) >= daily_limit:
            next_at = datetime.fromisoformat(str(recent_rows[0]["submitted_at"])) + timedelta(days=1)
            return result("daily_limit", "24 小时内的提现申请次数已达上限。", _iso(next_at.astimezone(UTC)))

        monthly_limit = int(active_policy.get("withdrawal_monthly_limit", 2))
        monthly_rows = conn.execute(
            """SELECT submitted_at FROM referral_withdrawal_requests
               WHERE user_id=? AND strftime('%Y-%m',submitted_at)=strftime('%Y-%m',?)
               ORDER BY datetime(submitted_at),id""",
            (int(user_id), _iso(moment)),
        ).fetchall()
        if len(monthly_rows) >= monthly_limit:
            next_month = (moment.replace(day=28) + timedelta(days=4)).replace(day=1)
            return result("monthly_limit", "本自然月提现次数已达上限。", _iso(next_month))

        open_limit = int(active_policy.get("withdrawal_open_limit", 1))
        open_count = int(conn.execute(
            "SELECT COUNT(*) FROM referral_withdrawal_requests WHERE user_id=? AND status IN ('submitted','approved')",
            (int(user_id),),
        ).fetchone()[0])
        if open_count >= open_limit:
            return result("open_limit", "已有待处理提现申请。")

        cooldown_days = int(active_policy.get("withdrawal_cooldown_days", 0))
        if cooldown_days:
            previous = conn.execute(
                """SELECT submitted_at FROM referral_withdrawal_requests WHERE user_id=?
                   ORDER BY datetime(submitted_at) DESC,id DESC LIMIT 1""",
                (int(user_id),),
            ).fetchone()
            if previous:
                submitted_at = datetime.fromisoformat(str(previous["submitted_at"])).astimezone(UTC)
                next_at = submitted_at + timedelta(days=cooldown_days)
                if next_at > moment:
                    return result("cooldown", "提现冷却期尚未结束。", _iso(next_at))

        if amount_minor is not None:
            if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor < 1:
                return result("ineligible", "提现金额必须使用正整数分。")
            if amount_minor < minimum:
                return result("below_min", f"最低提现金额为 {minimum} 分。")
            if amount_minor > maximum:
                return result("above_max", f"单笔提现最高金额为 {maximum} 分。")
            if amount_minor > available:
                return result("ineligible", "可提现余额不足。")
        elif available < minimum:
            return result("below_min", f"当前可提现余额低于最低提现金额 {minimum} 分。")
        return result("eligible", "当前提现条件已满足。")

    @staticmethod
    def _admin_action_receipt(
        conn: Any,
        *,
        actor_id: int,
        action: str,
        public_id: str,
        idempotency_key: str,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        key = str(idempotency_key or "").strip()
        if not 8 <= len(key) <= 128:
            raise ValueError("提款管理操作必须提供 8 至 128 字符的幂等键。")
        digest = hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event = conn.execute(
            """SELECT * FROM membership_promotion_admin_events
               WHERE idempotency_key=?
                 AND (actor_id=? OR entity_type='withdrawal_admin_action')""",
            (key, int(actor_id)),
        ).fetchone()
        if not event:
            return None
        if (
            int(event["actor_id"]) != int(actor_id)
            or event["action"] != action
            or event["entity_type"] != "withdrawal_admin_action"
            or event["entity_public_id"] != public_id
            or event["request_sha256"] != digest
        ):
            raise ValueError("提款管理幂等键已用于不同请求。")
        try:
            receipt = json.loads(event["details_json"])["receipt"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("提款管理操作回执无效。") from exc
        if not isinstance(receipt, dict):
            raise ValueError("提款管理操作回执无效。")
        return receipt

    @staticmethod
    def _record_admin_action_receipt(
        conn: Any,
        *,
        actor_id: int,
        action: str,
        public_id: str,
        idempotency_key: str,
        request: dict[str, Any],
        receipt: dict[str, Any],
        now: datetime,
    ) -> None:
        key = str(idempotency_key or "").strip()
        digest = hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        conn.execute(
            """INSERT INTO membership_promotion_admin_events
               (public_id,actor_id,action,entity_type,entity_public_id,idempotency_key,
                request_sha256,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                _public_id("WAC"), int(actor_id), action, "withdrawal_admin_action",
                public_id, key, digest,
                json.dumps({"receipt": receipt}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                _iso(now),
            ),
        )

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
            user = conn.execute("SELECT is_active FROM users WHERE id=?", (int(user_id),)).fetchone()
            if not user or not user["is_active"]:
                raise PermissionError("账户不存在或已停用。")
            ReferralCommissionService.release_due_in_transaction(conn, int(user_id), now)
            eligibility = self.withdrawal_eligibility_in_transaction(
                conn, int(user_id), now=now, amount_minor=amount_minor, policy=policy,
            )
            if eligibility["status"] == "paused":
                raise PermissionError("推广提款目前已暂停。")
            if eligibility["status"] == "daily_limit":
                raise ValueError("提款申请过于频繁，请稍后再试。")
            if eligibility["status"] == "monthly_limit":
                raise ValueError("本自然月提款次数已达上限。")
            if eligibility["status"] == "open_limit":
                raise ValueError("已有待处理提款申请。")
            if eligibility["status"] == "cooldown":
                raise ValueError("提款冷却期尚未结束。")
            if eligibility["status"] == "below_min":
                raise ValueError(f"最低提款金额为 {eligibility['min_minor']} 分。")
            if eligibility["status"] == "above_max":
                raise ValueError(f"单笔提款最高金额为 {eligibility['max_minor']} 分。")
            if eligibility["status"] == "debt" or eligibility["status"] != "eligible":
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
        self, actor_id: int, public_id: str, decision: str, reason: str, idempotency_key: str
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("提款审核决定无效。")
        public_id = str(public_id or "").strip().upper()
        reason = str(reason).strip()
        if decision == "reject" and not 1 <= len(reason) <= 500:
            raise ValueError("拒绝提款必须填写原因。")
        action = "WITHDRAWAL_APPROVED" if decision == "approve" else "WITHDRAWAL_REJECTED"
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
                if not 1 <= len(reason) <= 500:
                    raise ValueError("高金额提款批准必须填写增强审核说明。")
                if AdminService._require_super_admin_in_transaction(conn, int(actor_id)) != "super_admin":
                    raise PermissionError("高金额提款须由超级管理员完成增强审核。")
            request = {
                "action": "review", "actor_id": int(actor_id), "withdrawal_id": public_id,
                "decision": decision, "reason": reason,
            }
            replay = self._admin_action_receipt(
                conn, actor_id=int(actor_id), action=action,
                public_id=public_id, idempotency_key=idempotency_key, request=request,
            )
            if replay is not None:
                return replay
            if row["status"] != target:
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
                        (int(actor_id), _iso(now), reason, row["id"]),
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
                    entity_public_id=public_id, details={"reason": reason}, now=now,
                )
            receipt = dict(conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE id=?", (row["id"],)
            ).fetchone())
            self._record_admin_action_receipt(
                conn, actor_id=int(actor_id), action=action,
                public_id=public_id, idempotency_key=idempotency_key, request=request,
                receipt=receipt, now=now,
            )
            return receipt

    def confirm_paid(
        self, actor_id: int, public_id: str, payout_method: str, payout_reference: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        method = str(payout_method or "").strip().lower()
        if method not in {"fps", "bank", "other"}:
            raise ValueError("付款方式无效。")
        reference = _canonical_payout_reference(payout_reference)
        public_id = str(public_id or "").strip().upper()
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
            if int(row["approved_by"] or 0) == int(actor_id):
                raise PermissionError("批准人与确认付款人必须不同。")
            request = {
                "action": "paid", "actor_id": int(actor_id), "withdrawal_id": public_id,
                "payout_method": method, "payout_reference": reference,
            }
            replay = self._admin_action_receipt(
                conn, actor_id=int(actor_id), action="WITHDRAWAL_PAID", public_id=public_id,
                idempotency_key=idempotency_key, request=request,
            )
            if replay is not None:
                return replay
            if row["status"] == "paid":
                raise ValueError("提款已经确认付款。")
            if row["status"] != "approved":
                raise ValueError("只有已批准提款可以确认付款。")
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
            receipt = dict(conn.execute(
                "SELECT * FROM referral_withdrawal_requests WHERE id=?", (row["id"],)
            ).fetchone())
            self._record_admin_action_receipt(
                conn, actor_id=int(actor_id), action="WITHDRAWAL_PAID", public_id=public_id,
                idempotency_key=idempotency_key, request=request, receipt=receipt, now=now,
            )
            return receipt

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
