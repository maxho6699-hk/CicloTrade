from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import re
import secrets
from typing import Any

from core.compat import UTC
from core.database import DatabaseManager, get_database
from core.referral_affiliate_common import (
    CURRENCY,
    HONG_KONG,
    _audit,
    _balance_rows,
    _control,
    _enabled,
    _hkt,
    _invite_code,
    _iso,
    _mask_email,
    _public_id,
)
from core.referral_commission import ReferralCommissionService
from core.referral_coupon import ReferralCouponService


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

    def issue_link_claim(self, invite_code: str, fingerprint_hash: str, now: datetime | None = None) -> str:
        """Issue a short lived opaque proof; a plain invite code is insufficient."""
        code = str(invite_code or "").strip()
        fingerprint = str(fingerprint_hash or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("推广链接证明无效。")
        moment = now or datetime.now(UTC)
        raw = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profile = conn.execute("SELECT user_id FROM referral_profiles WHERE invite_code=? AND disabled_at IS NULL", (code,)).fetchone()
            if not profile:
                raise ValueError("推广码无效。")
            conn.execute("INSERT INTO referral_link_claim_intents(claim_hash,invite_code,fingerprint_hash,issued_at,expires_at) VALUES (?,?,?,?,?)", (digest, code, fingerprint, _iso(moment), _iso(moment + timedelta(minutes=20))))
        return raw

    @staticmethod
    def consume_link_claim_in_transaction(
        conn: Any, attribution_id: int, claim: str, fingerprint_hash: str, now: datetime
    ) -> bool:
        digest = hashlib.sha256(str(claim or "").encode()).hexdigest()
        intent = conn.execute("SELECT * FROM referral_link_claim_intents WHERE claim_hash=? AND consumed_at IS NULL", (digest,)).fetchone()
        if not intent:
            return False
        expiry = datetime.fromisoformat(str(intent["expires_at"]))
        expiry = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry.astimezone(UTC)
        if expiry <= now:
            return False
        attribution = conn.execute(
            "SELECT invite_code_snapshot,source FROM referral_attributions WHERE id=?", (attribution_id,)
        ).fetchone()
        if not attribution or attribution["source"] != "web" or attribution["invite_code_snapshot"] != intent["invite_code"]:
            return False
        if not hmac.compare_digest(str(intent["fingerprint_hash"]), str(fingerprint_hash or "")):
            return False
        claim_row = conn.execute("INSERT INTO referral_link_claims(attribution_id,claim_hash,issued_at,expires_at,consumed_at) VALUES (?,?,?,?,?)", (attribution_id, digest, intent["issued_at"], intent["expires_at"], _iso(now)))
        consumed = conn.execute("UPDATE referral_link_claim_intents SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (_iso(now), intent["id"])).rowcount
        if consumed != 1:
            raise ValueError("推广链接证明已被使用。")
        conn.execute("INSERT INTO referral_discount_eligibilities(attribution_id,link_claim_id,eligible_at) VALUES (?,?,?)", (attribution_id, claim_row.lastrowid, _iso(now)))
        return True

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
            from core.referral_coupon import POLICY_VERSION as PROMOTION_POLICY_VERSION
            policy_version, policy = ReferralCouponService._policy(conn)
            minimum = int(policy.get("withdrawal_min_minor", 20_000))
            hold_days = int(policy.get("hold_days", 30))
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
                        "currency": CURRENCY,
                        "policy_version": f"{PROMOTION_POLICY_VERSION}:{policy_version}",
                        "hold_days": hold_days,
                        "minimum_withdrawal_minor": minimum,
                        "maximum_withdrawal_minor": int(policy.get("withdrawal_max_minor", 500_000)),
                        "withdrawal_paused": bool(policy.get("withdrawal_paused", False)),
                        "referral_discount_bps": int(policy.get("referral_discount_bps", 500)),
                        "referrer_commission_bps": int(policy.get("commission_rate_bps", 1000)),
                        "subsequent_order_commission_bps": 0,
                        # The public portal currently understands percentage tiers only.
                        # Fixed-amount milestone details remain on the authoritative admin policy
                        # until the shared Web DTO is upgraded; never mislabel amounts as basis points.
                        "bonus_tiers": []},
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

