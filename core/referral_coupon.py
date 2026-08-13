"""Server-authoritative membership coupon and referral promotion snapshots."""
# ruff: noqa: E701, E702

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import re
import secrets
from typing import TYPE_CHECKING, Any

from core.compat import UTC
from core.database import DatabaseManager, get_database
if TYPE_CHECKING:
    from payment.promotion_adapter import PlanCommercePolicy


POLICY_KEY = "membership_promotions_v2"
POLICY_VERSION = "membership-promotions-v2"
REFERRAL_DISCOUNT_BPS = 500
REFERRAL_COMMISSION_BPS = 1000
_CODE = re.compile(r"[A-Z0-9_-]{3,64}")


def _iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def _public_id() -> str:
    return f"CPN{secrets.token_hex(12).upper()}"


def _canonical_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if not _CODE.fullmatch(code):
        raise ValueError("优惠码格式无效。")
    return code


class ReferralCouponService:
    def __init__(
        self,
        database: DatabaseManager | None = None,
        *,
        plan_policy: PlanCommercePolicy | None = None,
    ):
        self.db = database or get_database()
        self.plan_policy = plan_policy

    @staticmethod
    def _policy(conn: Any) -> tuple[int, dict[str, Any]]:
        row = conn.execute("SELECT version,value_json,config_sha256 FROM referral_coupon_policy_versions WHERE policy_key=? AND datetime(effective_at)<=datetime(?) ORDER BY version DESC LIMIT 1", (POLICY_KEY, _iso())).fetchone()
        if not row:
            raise ValueError("推广政策未初始化。")
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("推广政策无效。") from exc
        if not isinstance(value, dict):
            raise ValueError("推广政策无效。")
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if not hmac.compare_digest(hashlib.sha256(canonical.encode()).hexdigest(), str(row["config_sha256"])):
            raise ValueError("推广政策审计摘要不一致。")
        normalized = dict(value)
        normalized.setdefault("withdrawal_paused", False)
        return int(row["version"]), normalized

    @staticmethod
    def quote_in_transaction(conn: Any, *, user_id: int, plan: str, cycle: str, list_price_minor: int, coupon_code: str | None, now: datetime) -> dict[str, Any]:
        policy_version, policy = ReferralCouponService._policy(conn)
        conn.execute(
            """UPDATE membership_coupon_redemptions SET status='released'
               WHERE status='reserved' AND datetime(expires_at)<=datetime(?)
                 AND NOT EXISTS (SELECT 1 FROM manual_payment_claims c
                                 WHERE c.order_no=membership_coupon_redemptions.order_no
                                   AND c.status='submitted')""",
            (_iso(now),),
        )
        list_price = int(list_price_minor)
        if list_price < 1:
            raise ValueError("订单金额无效。")
        coupon_discount = 0
        coupon: Any = None
        normalized = _canonical_code(coupon_code) if coupon_code else None
        if normalized:
            coupon = conn.execute("SELECT * FROM membership_coupons WHERE code=? COLLATE NOCASE", (normalized,)).fetchone()
            if not coupon or not int(coupon["enabled"]):
                raise ValueError("优惠码无效或已暂停。")
            try:
                starts_at = datetime.fromisoformat(str(coupon["starts_at"]))
                expires_at = datetime.fromisoformat(str(coupon["expires_at"]))
                starts_at = starts_at.replace(tzinfo=UTC) if starts_at.tzinfo is None else starts_at.astimezone(UTC)
                expires_at = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at.astimezone(UTC)
            except ValueError as exc:
                raise ValueError("优惠码时间无效。") from exc
            if not (starts_at <= now.astimezone(UTC) < expires_at):
                raise ValueError("优惠码未开始或已过期。")
            plans, cycles = json.loads(coupon["applicable_plans_json"]), json.loads(coupon["applicable_cycles_json"])
            if plan not in plans or cycle not in cycles or list_price < int(coupon["min_spend_minor"]):
                raise ValueError("优惠码不适用于此订单。")
            if coupon["discount_type"] == "fixed_hkd" and int(coupon["discount_value"]) > min(list_price * 15 // 100, 100_000):
                raise ValueError("固定优惠超过当前套餐的15%安全上限。")
            used = int(conn.execute("SELECT COUNT(*) FROM membership_coupon_redemptions WHERE coupon_id=? AND status IN ('reserved','consumed')", (coupon["id"],)).fetchone()[0])
            personal = int(conn.execute("SELECT COUNT(*) FROM membership_coupon_redemptions WHERE coupon_id=? AND user_id=? AND status IN ('reserved','consumed')", (coupon["id"], user_id)).fetchone()[0])
            if used >= int(coupon["total_use_limit"]) or personal >= int(coupon["per_user_limit"]):
                raise ValueError("优惠码使用次数已用尽。")
            coupon_discount = list_price * int(coupon["discount_value"]) // 10_000 if coupon["discount_type"] == "percent" else int(coupon["discount_value"])
            if coupon["max_discount_minor"] is not None: coupon_discount = min(coupon_discount, int(coupon["max_discount_minor"]))
            coupon_discount = min(list_price - 1, max(0, coupon_discount))
        after_coupon = list_price - coupon_discount
        paid_before = int(conn.execute("SELECT COUNT(*) FROM subscription_orders WHERE user_id=? AND status IN ('paid','refunded')", (user_id,)).fetchone()[0])
        attribution = conn.execute("SELECT * FROM referral_attributions WHERE referred_user_id=?", (user_id,)).fetchone()
        eligibility = conn.execute("SELECT 1 FROM referral_discount_eligibilities e JOIN referral_attributions a ON a.id=e.attribution_id WHERE a.referred_user_id=?", (user_id,)).fetchone()
        pending_eligible = int(conn.execute(
            "SELECT COUNT(*) FROM subscription_orders WHERE user_id=? AND status='pending' AND referral_eligible_snapshot=1",
            (user_id,),
        ).fetchone()[0])
        # Only one outstanding discount candidate may exist.  This makes the later
        # paid transition an unambiguous first-successful-payment claim without
        # recalculating the stored money snapshot.
        enabled = conn.execute(
            "SELECT control_value FROM platform_controls WHERE control_key='referral_cash_enabled'"
        ).fetchone()
        program_enabled = bool(enabled and str(enabled["control_value"]).lower() in {"1", "true", "yes", "on"})
        eligible = bool(program_enabled and attribution and eligibility and paid_before == 0 and pending_eligible == 0)
        referral_bps = REFERRAL_DISCOUNT_BPS if eligible else 0
        referral_discount = after_coupon * referral_bps // 10_000
        final_amount = after_coupon - referral_discount
        minimum = max(1, int(policy.get("minimum_final_amount_minor", 1)))
        if final_amount < minimum:
            referral_discount = max(0, after_coupon - minimum)
            final_amount = after_coupon - referral_discount
        quote = {"list_price_minor": list_price, "coupon_discount_minor": coupon_discount, "referral_discount_minor": referral_discount, "final_amount_minor": final_amount, "coupon_code_snapshot": normalized, "coupon_version_snapshot": int(coupon["version"]) if coupon else None, "coupon_id": int(coupon["id"]) if coupon else None, "referral_eligible_snapshot": int(eligible), "referral_policy_version": f"{POLICY_VERSION}:{policy_version}", "commission_rate_bps": REFERRAL_COMMISSION_BPS if eligible else 0, "commission_cap_minor": int(policy.get("commission_cap_minor", 100_000)) if eligible else 0, "hold_days": int(policy.get("hold_days", 30)) if eligible else 0, "bonus_policy_snapshot": json.dumps({"enabled": bool(policy.get("bonus_enabled", False)), "tiers": policy.get("bonus_tiers", []), "hold_days": int(policy.get("hold_days", 30)), "version": policy_version}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}
        from payment.promotion_adapter import PromotionOrderAdapter

        quote["promotion_snapshot_sha256"] = PromotionOrderAdapter.promotion_snapshot_sha256(quote)
        return quote

    @staticmethod
    def redeem_in_transaction(conn: Any, *, quote: dict[str, Any], user_id: int, order_no: str, now: datetime) -> None:
        if quote.get("coupon_id") is None:
            return
        conn.execute("INSERT INTO membership_coupon_redemptions(coupon_id,user_id,order_no,coupon_version,discount_minor,status,expires_at,redeemed_at) VALUES (?,?,?,?,?,'reserved',?,?)", (quote["coupon_id"], user_id, order_no, quote["coupon_version_snapshot"], quote["coupon_discount_minor"], _iso(now + timedelta(hours=24)), _iso(now)))

    @staticmethod
    def _require_admin(conn: Any, actor_id: int) -> None:
        from core.admin_service import AdminService
        if AdminService._require_super_admin_in_transaction(conn, int(actor_id)) != "super_admin":
            raise PermissionError("仅超级管理员可管理优惠码。")

    @staticmethod
    def _event(conn: Any, actor_id: int, action: str, entity_type: str, entity_public_id: str, key: str, payload: dict[str, Any], now: datetime) -> bool:
        if not 8 <= len(key) <= 128:
            raise ValueError("管理幂等键无效。")
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        existing = conn.execute("SELECT request_sha256 FROM membership_promotion_admin_events WHERE actor_id=? AND idempotency_key=?", (actor_id, key)).fetchone()
        if existing:
            if existing["request_sha256"] != digest: raise ValueError("幂等键已用于不同的管理操作。")
            return False
        conn.execute("INSERT INTO membership_promotion_admin_events(public_id,actor_id,action,entity_type,entity_public_id,idempotency_key,request_sha256,details_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (_public_id(), actor_id, action, entity_type, entity_public_id, key, digest, json.dumps(payload, ensure_ascii=False, sort_keys=True), _iso(now)))
        return True

    def create_coupon(self, actor_id: int, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        required = {"code","campaign_name","discount_type","discount_value","max_discount_minor","starts_at","expires_at","min_spend_minor","total_use_limit","per_user_limit","applicable_plans","applicable_cycles","enabled"}
        if set(payload) != required or payload["discount_type"] not in {"percent","fixed_hkd"} or not isinstance(payload["applicable_plans"], list) or not isinstance(payload["applicable_cycles"], list) or not isinstance(payload["enabled"], bool): raise ValueError("优惠码字段无效。")
        code = _canonical_code(payload["code"]); now = datetime.now(UTC)
        if payload["discount_type"] == "percent" and not 1 <= int(payload["discount_value"]) <= 1_500: raise ValueError("优惠码超过15%需要独立财务批准。")
        if payload["discount_type"] == "fixed_hkd" and (not isinstance(payload["max_discount_minor"], int) or payload["max_discount_minor"] > 100_000): raise ValueError("固定优惠超过平台安全上限。")
        try:
            starts_at = datetime.fromisoformat(str(payload["starts_at"])); expires_at = datetime.fromisoformat(str(payload["expires_at"]))
            if starts_at.tzinfo is None or expires_at.tzinfo is None or expires_at.astimezone(UTC) <= starts_at.astimezone(UTC): raise ValueError
        except ValueError as exc: raise ValueError("优惠码时间范围无效。") from exc
        if any(not isinstance(payload[k], int) or isinstance(payload[k], bool) for k in ("discount_value","min_spend_minor","total_use_limit","per_user_limit")) or int(payload["min_spend_minor"]) < 0 or int(payload["total_use_limit"]) < 1 or int(payload["per_user_limit"]) < 1: raise ValueError("优惠码金额或次数无效。")
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE"); self._require_admin(conn, actor_id)
            from payment.promotion_adapter import PromotionOrderAdapter

            PromotionOrderAdapter(self.plan_policy).validate_coupon_plans(
                conn,
                plans=payload["applicable_plans"],
                cycles=payload["applicable_cycles"],
                now=now,
            )
            public_id = _public_id()
            created = self._event(conn, actor_id, "COUPON_CREATED", "coupon", public_id, idempotency_key, payload, now)
            if created:
                conn.execute("INSERT INTO membership_coupons(public_id,code,campaign_name,discount_type,discount_value,max_discount_minor,min_spend_minor,total_use_limit,per_user_limit,applicable_plans_json,applicable_cycles_json,starts_at,expires_at,enabled,created_by,created_at,updated_by,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (public_id, code, str(payload["campaign_name"]).strip(), payload["discount_type"], int(payload["discount_value"]), payload["max_discount_minor"], int(payload["min_spend_minor"]), int(payload["total_use_limit"]), int(payload["per_user_limit"]), json.dumps(payload["applicable_plans"], ensure_ascii=False), json.dumps(payload["applicable_cycles"]), str(payload["starts_at"]), str(payload["expires_at"]), int(payload["enabled"]), actor_id, _iso(now), actor_id, _iso(now)))
            row = conn.execute("SELECT * FROM membership_coupons WHERE public_id=?" if created else "SELECT * FROM membership_coupons WHERE code=? COLLATE NOCASE", (public_id if created else code,)).fetchone()
            return dict(row)

    def list_coupons(self, actor_id: int) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            self._require_admin(conn, actor_id)
            return [dict(row) for row in conn.execute("SELECT * FROM membership_coupons ORDER BY created_at DESC,id DESC").fetchall()]

    def pause_coupon(self, actor_id: int, public_id: str, expected_version: int, idempotency_key: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE"); self._require_admin(conn, actor_id)
            row = conn.execute("SELECT * FROM membership_coupons WHERE public_id=?", (public_id,)).fetchone()
            if not row: raise ValueError("优惠码不存在。")
            payload = {"enabled": False, "expected_version": expected_version, "coupon_id": public_id}
            if self._event(conn, actor_id, "COUPON_PAUSED", "coupon", public_id, idempotency_key, payload, now):
                changed = conn.execute("UPDATE membership_coupons SET enabled=0,version=version+1,updated_by=?,updated_at=? WHERE public_id=? AND version=?", (actor_id, _iso(now), public_id, int(expected_version))).rowcount
                if changed != 1: raise ValueError("优惠码版本已变更，请刷新后重试。")
            return dict(conn.execute("SELECT * FROM membership_coupons WHERE public_id=?", (public_id,)).fetchone())

    def policy(self, actor_id: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            self._require_admin(conn, actor_id); version, value = self._policy(conn)
            return {"version": version, "policy": value}

    def update_policy(self, actor_id: int, value: dict[str, Any], expected_version: int, idempotency_key: str) -> dict[str, Any]:
        allowed = {"commission_rate_bps","referral_discount_bps","minimum_final_amount_minor","commission_cap_minor","hold_days","withdrawal_min_minor","withdrawal_max_minor","withdrawal_daily_limit","withdrawal_monthly_limit","withdrawal_open_limit","withdrawal_cooldown_days","automatic_payout_review_threshold_minor","withdrawal_paused","bonus_enabled","bonus_tiers"}
        numeric = allowed - {"withdrawal_paused", "bonus_enabled", "bonus_tiers"}
        amounts = {"minimum_final_amount_minor", "commission_cap_minor", "withdrawal_min_minor", "withdrawal_max_minor", "automatic_payout_review_threshold_minor"}
        limits = {"withdrawal_daily_limit", "withdrawal_monthly_limit", "withdrawal_open_limit"}
        tiers = value.get("bonus_tiers") if isinstance(value, dict) else None
        valid_tiers = isinstance(tiers, list) and 1 <= len(tiers) <= 10
        if valid_tiers:
            previous_count = previous_amount = 0
            for tier in tiers:
                if set(tier) != {"qualified_count", "cumulative_amount_minor"} or any(
                    not isinstance(tier[key], int) or isinstance(tier[key], bool)
                    for key in ("qualified_count", "cumulative_amount_minor")
                ):
                    valid_tiers = False
                    break
                count, amount = int(tier["qualified_count"]), int(tier["cumulative_amount_minor"])
                if not 1 <= count <= 100_000 or not 1 <= amount <= 100_000_000 or count <= previous_count or amount <= previous_amount:
                    valid_tiers = False
                    break
                previous_count, previous_amount = count, amount
        if (
            set(value) != allowed
            or not all(isinstance(value[k], int) and not isinstance(value[k], bool) for k in numeric)
            or not isinstance(value["withdrawal_paused"], bool)
            or not isinstance(value["bonus_enabled"], bool)
            or not valid_tiers
            or not 0 <= value["commission_rate_bps"] <= 10_000
            or value["commission_rate_bps"] != REFERRAL_COMMISSION_BPS
            or value["referral_discount_bps"] != REFERRAL_DISCOUNT_BPS
            or not 0 <= value["hold_days"] <= 365
            or not 0 <= value["withdrawal_cooldown_days"] <= 3_650
            or any(not 1 <= int(value[key]) <= 100_000_000 for key in amounts)
            or any(not 1 <= int(value[key]) <= 100_000 for key in limits)
            or value["withdrawal_max_minor"] < value["withdrawal_min_minor"]
        ):
            raise ValueError("推广政策字段无效。")
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE"); self._require_admin(conn, actor_id)
            if self._event(conn, actor_id, "REFERRAL_POLICY_UPDATED", "policy", POLICY_KEY, idempotency_key, {"policy": value, "expected_version": expected_version}, now):
                current, _ = self._policy(conn)
                if current != int(expected_version): raise ValueError("推广政策版本已变更，请刷新后重试。")
                serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(serialized.encode()).hexdigest()
                conn.execute("INSERT INTO referral_coupon_policy_versions(policy_key,version,value_json,config_sha256,effective_at,created_by,created_at) VALUES (?,?,?,?,?,?,?)", (POLICY_KEY, current + 1, serialized, digest, _iso(now), actor_id, _iso(now)))
            version, policy = self._policy(conn); return {"version": version, "policy": policy}

    def attribution_summary(self, actor_id: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            self._require_admin(conn, actor_id)
            coupons = conn.execute("SELECT COUNT(*) coupons,COALESCE(SUM(discount_minor),0) coupon_cost_minor FROM membership_coupon_redemptions").fetchone()
            orders = conn.execute("""SELECT COUNT(*) orders,COALESCE(SUM(coupon_discount_minor),0) coupon_cost_minor,
                                   COALESCE(SUM(referral_discount_minor),0) referral_cost_minor,
                                   COALESCE(SUM(MAX(0,final_amount_minor-refunded_minor)),0) revenue_minor
                                   FROM subscription_orders WHERE status IN ('paid','refunded')""").fetchone()
            return {"coupons": int(coupons["coupons"]), "orders": int(orders["orders"]), "coupon_cost_minor": int(orders["coupon_cost_minor"]), "referral_cost_minor": int(orders["referral_cost_minor"]), "revenue_minor": int(orders["revenue_minor"])}

    def analytics(
        self, actor_id: int, *, coupon_code: str | None = None, campaign: str | None = None,
        status: str | None = None, started_at: str | None = None, ended_at: str | None = None,
        promotion_type: str | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            self._require_admin(conn, actor_id)
            extended = promotion_type is not None
            clauses = ["1=1" if extended else "o.coupon_code_snapshot IS NOT NULL"]
            params: list[Any] = []
            if coupon_code:
                clauses.append("o.coupon_code_snapshot=? COLLATE NOCASE")
                params.append(_canonical_code(coupon_code))
            if campaign:
                clauses.append("c.campaign_name=?")
                params.append(str(campaign).strip())
            if status:
                if status not in {"pending", "paid", "refunded", "cancelled", "failed"}:
                    raise ValueError("订单状态筛选无效。")
                clauses.append("o.status=?")
                params.append(status)
            if extended and promotion_type != "all":
                conditions = {
                    "coupon_only": "COALESCE(o.coupon_discount_minor,0)>0 AND COALESCE(o.referral_discount_minor,0)=0",
                    "referral_only": "COALESCE(o.coupon_discount_minor,0)=0 AND COALESCE(o.referral_discount_minor,0)>0",
                    "stacked": "COALESCE(o.coupon_discount_minor,0)>0 AND COALESCE(o.referral_discount_minor,0)>0",
                    "none": "COALESCE(o.coupon_discount_minor,0)=0 AND COALESCE(o.referral_discount_minor,0)=0",
                }
                if promotion_type not in conditions:
                    raise ValueError("推广归因类型筛选无效。")
                clauses.append(conditions[promotion_type])
            for value, operator in ((started_at, ">="), (ended_at, "<=")):
                if value:
                    moment = datetime.fromisoformat(str(value))
                    if moment.tzinfo is None:
                        raise ValueError("分析时间必须带时区。")
                    clauses.append(f"datetime(o.created_at){operator}datetime(?)")
                    params.append(moment.astimezone(UTC).isoformat(timespec="seconds"))
            where = " AND ".join(clauses)
            cost_columns = """
                           COALESCE((SELECT SUM(MAX(0,rc.commission_amount_minor-rc.clawed_back_minor))
                                     FROM referral_commissions rc WHERE rc.source_order_no=o.order_no),0)
                             commission_cost_minor,
                           COALESCE((SELECT SUM(MAX(0,ba.award_delta_minor-ba.reversed_amount_minor))
                                     FROM referral_bonus_award_events ba
                                     WHERE instr(ba.idempotency_key,':up:'||o.order_no||':')>0),0)
                             bonus_cost_minor
            """ if extended else "0 commission_cost_minor, 0 bonus_cost_minor"
            row_limit = " LIMIT 501" if extended else ""
            rows = [dict(row) for row in conn.execute(
                f"""SELECT o.user_id,o.order_no,o.status,o.created_at,o.paid_at,o.refunded_at,
                           o.list_price_minor,o.coupon_discount_minor,o.referral_discount_minor,
                           o.final_amount_minor,o.refunded_minor,o.coupon_code_snapshot,c.campaign_name,
                           rp.public_id customer_reference,
                           {cost_columns}
                    FROM subscription_orders o
                    LEFT JOIN membership_coupons c ON c.code=o.coupon_code_snapshot COLLATE NOCASE
                    LEFT JOIN referral_profiles rp ON rp.user_id=o.user_id
                    WHERE {where} ORDER BY o.created_at DESC,o.id DESC{row_limit}""",
                params,
            ).fetchall()]
            if extended and len(rows) > 500:
                raise ValueError("推广分析范围过大，请增加筛选条件。")
            items = []
            for row in rows:
                paid = int(row["final_amount_minor"] or 0) if row["status"] in {"paid", "refunded"} else 0
                refunded = int(row["refunded_minor"] or 0)
                coupon_discount = int(row["coupon_discount_minor"] or 0)
                referral_discount = int(row["referral_discount_minor"] or 0)
                commission_cost = int(row["commission_cost_minor"] or 0)
                bonus_cost = int(row["bonus_cost_minor"] or 0)
                promotion_kind = (
                    "stacked" if coupon_discount and referral_discount else
                    "coupon_only" if coupon_discount else
                    "referral_only" if referral_discount else "none"
                )
                item = {
                    "coupon_code": row["coupon_code_snapshot"], "campaign": row["campaign_name"],
                    "customer": row["customer_reference"] or f"USR{hashlib.sha256(str(row['user_id']).encode()).hexdigest()[:24].upper()}", "order_id": row["order_no"],
                    "status": row["status"],
                    "list_price_minor": int(row["list_price_minor"] or 0),
                    "coupon_discount_minor": coupon_discount,
                    "referral_discount_minor": referral_discount,
                    "paid_revenue_minor": paid, "refund_or_chargeback_minor": refunded,
                    "net_revenue_minor": paid - refunded,
                    "discount_cost_minor": coupon_discount + referral_discount,
                    "created_at": row["created_at"], "paid_at": row["paid_at"], "refunded_at": row["refunded_at"],
                }
                if extended:
                    item.update({
                        "promotion_type": promotion_kind,
                        "commission_cost_minor": commission_cost,
                        "bonus_cost_minor": bonus_cost,
                        "promotion_cost_minor": coupon_discount + referral_discount + commission_cost + bonus_cost,
                    })
                items.append(item)
            summary = {
                "orders": len(items),
                "list_price_minor": sum(i["list_price_minor"] for i in items),
                "coupon_cost_minor": sum(i["coupon_discount_minor"] for i in items),
                "referral_cost_minor": sum(i["referral_discount_minor"] for i in items),
                "paid_revenue_minor": sum(i["paid_revenue_minor"] for i in items),
                "refund_or_chargeback_minor": sum(i["refund_or_chargeback_minor"] for i in items),
                "net_revenue_minor": sum(i["net_revenue_minor"] for i in items),
            }
            if extended:
                summary.update({
                    "customers": len({i["customer"] for i in items}),
                    "coupon_only_orders": sum(i["promotion_type"] == "coupon_only" for i in items),
                    "referral_only_orders": sum(i["promotion_type"] == "referral_only" for i in items),
                    "stacked_orders": sum(i["promotion_type"] == "stacked" for i in items),
                    "unattributed_orders": sum(i["promotion_type"] == "none" for i in items),
                    "commission_cost_minor": sum(i["commission_cost_minor"] for i in items),
                    "bonus_cost_minor": sum(i["bonus_cost_minor"] for i in items),
                    "promotion_cost_minor": sum(i["promotion_cost_minor"] for i in items),
                })
            return {"items": items, "summary": summary}
