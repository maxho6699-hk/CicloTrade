from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from core.compat import UTC
from core.database import DatabaseManager, get_database
from core.referral_affiliate_common import (
    CURRENCY,
    POLICY_VERSION,
    _audit,
    _control,
    _enabled,
    _int_control,
    _iso,
    _ledger_batch,
    _public_id,
)
from core.referral_bonus import ReferralBonusService


class ReferralCommissionService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def record_settlement(
        conn: Any, order: dict[str, Any], pre_membership: dict[str, Any], now: datetime
    ) -> dict[str, Any] | None:
        if order.get("referral_policy_version"):
            from payment.promotion_adapter import PromotionOrderAdapter

            PromotionOrderAdapter.assert_snapshot_integrity(order)
        if not _enabled(conn) and not order.get("referral_policy_version"):
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
        if not order.get("referral_policy_version"):
            # Legacy commission rules only apply to immutable orders created
            # before the cash-program cutover.  New orders must always carry a
            # V2 promotion snapshot, even when imported through a legacy
            # provider rail; source strings never grant legacy rates.
            cutover = _control(conn, "referral_cash_cutover_at", _iso(now))
            created_at = str(order.get("created_at") or "")
            legacy_order = bool(created_at and conn.execute(
                "SELECT datetime(?) < datetime(?)", (created_at, cutover)
            ).fetchone()[0])
            if not legacy_order:
                return None
        sequence = int(conn.execute(
            """SELECT COUNT(*) FROM subscription_orders
               WHERE user_id=? AND status IN ('paid','refunded') AND paid_at IS NOT NULL AND
                     (datetime(paid_at)<datetime(?) OR (paid_at=? AND id<=?))""",
            (int(order["user_id"]), _iso(now), _iso(now), int(order["id"])),
        ).fetchone()[0])
        sequence = max(1, sequence)
        # V2 only commissions a qualified browser-link attribution's first
        # successful paid order.  Existing pre-V2 records retain their legacy
        # snapshot behaviour; historical rows are never re-priced.
        is_v2 = bool(order.get("referral_policy_version"))
        if is_v2:
            if sequence != 1 or not int(order.get("referral_eligible_snapshot") or 0):
                return None
            kind = "initial_purchase"
            policy_version = str(order["referral_policy_version"])
            rate_bps = int(order.get("referral_commission_rate_bps_snapshot") or 0)
        else:
            previous_plan = str(pre_membership.get("plan_type") or "免费版")
            target_plan = str(order["plan_type"])
            if sequence == 1:
                kind, rate_key, default_rate = "initial_purchase", "referral_first_rate_bps", 2000
            elif previous_plan == target_plan:
                kind, rate_key, default_rate = "renewal", "referral_repeat_rate_bps", 1000
            else:
                kind, rate_key, default_rate = "upgrade", "referral_upgrade_rate_bps", 1000
            rate_bps = _int_control(conn, rate_key, default_rate, 0, 10000)
            policy_version = POLICY_VERSION
        gross = int(order.get("final_amount_minor") or order.get("amount_minor") or round(float(order["amount"]) * 100))
        if gross <= 0 or str(order.get("currency") or "").upper() != CURRENCY:
            return None
        commission_minor = gross * rate_bps // 10000
        if is_v2:
            commission_minor = min(commission_minor, int(order.get("referral_commission_cap_minor_snapshot") or 0))
            hold_days = int(order.get("referral_hold_days_snapshot") or 0)
        else:
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
                rate_bps, commission_minor, CURRENCY, policy_version, _iso(now),
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
        ReferralBonusService.record_qualification(conn, order=order, attribution=dict(attribution), now=now)
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
        return released + ReferralBonusService.release_due_in_transaction(conn, user_id, now)

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
        if order.get("referral_policy_version"):
            from payment.promotion_adapter import PromotionOrderAdapter

            PromotionOrderAdapter.assert_snapshot_integrity(order)
        commission = conn.execute(
            "SELECT * FROM referral_commissions WHERE source_order_no=?", (order["order_no"],)
        ).fetchone()
        if not commission:
            return False
        if conn.execute(
            "SELECT 1 FROM referral_reversal_events WHERE event_key=?", (event_key,)
        ).fetchone():
            return False
        amount = int(amount_minor)
        remaining_gross = int(commission["gross_amount_minor"]) - int(commission["reversed_amount_minor"])
        if amount < 1 or amount > remaining_gross:
            raise ValueError("推广佣金逆转金额无效。")
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
                from core.referral_wallet import ReferralWalletService

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
        # Any verified financial reversal invalidates this order as a qualified
        # referral for milestone counting.  The contributor transition is
        # idempotent, while commission clawback remains proportional to the
        # verified refunded amount.
        ReferralBonusService.record_reversal(
            conn, source_order_no=str(order["order_no"]), now=now
        )
        return True

