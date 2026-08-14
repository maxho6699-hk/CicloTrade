"""Frozen-policy referral milestone accounting for Promotion V2."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
from typing import Any


class ReferralBonusService:
    """Monthly HKT referral milestones; awards are incremental tier deltas."""

    @staticmethod
    def record_qualification(conn: Any, *, order: dict[str, Any], attribution: dict[str, Any], now: datetime) -> None:
        from core.referral_affiliate_common import HONG_KONG, _audit, _iso, _ledger_batch, _public_id

        try:
            snapshot = json.loads(str(order.get("referral_bonus_policy_snapshot") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if not snapshot.get("enabled") or not int(order.get("referral_eligible_snapshot") or 0):
            return
        period = now.astimezone(HONG_KONG).strftime("%Y-%m")
        hold_days = int(snapshot.get("hold_days", 30))
        canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        policy_hash = hashlib.sha256(canonical.encode()).hexdigest()
        conn.execute(
            """INSERT OR IGNORE INTO referral_bonus_periods
               (referrer_user_id,period_key,policy_version,policy_snapshot_json,policy_sha256,hold_days,locked_at)
               VALUES (?,?,?,?,?,?,?)""",
            (int(attribution["referrer_user_id"]), period, str(snapshot.get("version", "1")),
             canonical, policy_hash, hold_days, _iso(now)),
        )
        period_row = conn.execute(
            "SELECT * FROM referral_bonus_periods WHERE referrer_user_id=? AND period_key=?",
            (int(attribution["referrer_user_id"]), period),
        ).fetchone()
        period_snapshot = json.loads(str(period_row["policy_snapshot_json"]))
        digest = hashlib.sha256(json.dumps(
            period_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        if not hmac.compare_digest(digest, str(period_row["policy_sha256"])):
            raise ValueError("奖金期间政策审计摘要不一致。")
        period_hold_days = int(period_row["hold_days"])
        period_version = str(period_row["policy_version"])
        inserted = conn.execute(
            """INSERT OR IGNORE INTO referral_bonus_contributors
               (public_id,period_id,referrer_user_id,referred_user_id,source_order_no,period_key,policy_version,
                available_at,status,qualified_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (_public_id("BQC"), int(period_row["id"]), int(attribution["referrer_user_id"]), int(order["user_id"]),
             str(order["order_no"]), period, period_version,
             _iso(now + timedelta(days=period_hold_days)), "pending", _iso(now)),
        )
        if inserted.rowcount != 1:
            return
        count = int(conn.execute(
            "SELECT COUNT(*) FROM referral_bonus_contributors WHERE referrer_user_id=? AND period_key=? AND status<>'reversed'",
            (int(attribution["referrer_user_id"]), period),
        ).fetchone()[0])
        tiers = sorted(period_snapshot.get("tiers", []), key=lambda item: int(item.get("qualified_count", 0)))
        eligible = [tier for tier in tiers if count >= int(tier.get("qualified_count", 0))]
        if not eligible:
            return
        tier = eligible[-1]
        target = int(tier.get("cumulative_amount_minor", 0))
        prior = int(conn.execute(
            "SELECT COALESCE(SUM(award_delta_minor-reversed_amount_minor),0) FROM referral_bonus_award_events WHERE referrer_user_id=? AND period_key=?",
            (int(attribution["referrer_user_id"]), period),
        ).fetchone()[0])
        delta = target - prior
        if delta <= 0:
            return
        public_id = _public_id("BON")
        available = now + timedelta(days=period_hold_days)
        conn.execute(
            """INSERT INTO referral_bonus_award_events
               (public_id,period_id,referrer_user_id,period_key,policy_version,threshold_count,cumulative_target_minor,
                award_delta_minor,available_at,status,created_at,idempotency_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (public_id, int(period_row["id"]), int(attribution["referrer_user_id"]), period, period_version,
             int(tier["qualified_count"]), target, delta, _iso(available), "pending", _iso(now),
             f"period:{period_row['id']}:up:{order['order_no']}:{target}"),
        )
        conn.execute("UPDATE referral_bonus_periods SET current_target_minor=? WHERE id=?", (target, period_row["id"]))
        _ledger_batch(conn, user_id=int(attribution["referrer_user_id"]), legs=[("pending", delta)],
                      entry_type="bonus_award", group_key=f"bonus:{public_id}", reference_type="bonus",
                      reference_id=public_id, batch_key=f"bonus:{public_id}:award", now=now)
        _audit(
            conn, actor_user_id=None, actor_kind="system", action="BONUS_AWARDED",
            entity_type="bonus", entity_public_id=public_id,
            details={"source_order_no": str(order["order_no"]), "qualified_count": count,
                     "target_minor": target, "award_delta_minor": delta}, now=now,
        )

    @staticmethod
    def release_due_in_transaction(conn: Any, user_id: int | None, now: datetime) -> int:
        from core.referral_affiliate_common import _iso, _ledger_batch

        clause, params = ("", (_iso(now),)) if user_id is None else (" AND referrer_user_id=?", (_iso(now), int(user_id)))
        rows = conn.execute(
            f"SELECT * FROM referral_bonus_award_events WHERE status='pending' AND datetime(available_at)<=datetime(?) {clause}", params
        ).fetchall()
        released = 0
        for row in rows:
            remaining = int(row["award_delta_minor"]) - int(row["reversed_amount_minor"])
            if remaining <= 0:
                conn.execute("UPDATE referral_bonus_award_events SET status='reversed' WHERE id=? AND status='pending'", (row["id"],))
                continue
            if conn.execute("UPDATE referral_bonus_award_events SET status='available' WHERE id=? AND status='pending'", (row["id"],)).rowcount != 1:
                continue
            _ledger_batch(conn, user_id=int(row["referrer_user_id"]), legs=[("pending", -remaining), ("available", remaining)], entry_type="bonus_mature", group_key=f"bonus:{row['public_id']}:mature", reference_type="bonus", reference_id=row["public_id"], batch_key=f"bonus:{row['public_id']}:mature", now=now)
            released += 1
        conn.execute("UPDATE referral_bonus_contributors SET status='available' WHERE status='pending' AND datetime(available_at)<=datetime(?)", (_iso(now),))
        return released

    @staticmethod
    def record_reversal(conn: Any, *, source_order_no: str, now: datetime) -> None:
        from core.referral_affiliate_common import _audit, _iso, _ledger_batch
        from core.referral_wallet import ReferralWalletService

        contributor = conn.execute("SELECT * FROM referral_bonus_contributors WHERE source_order_no=? AND status<>'reversed'", (source_order_no,)).fetchone()
        if not contributor:
            return
        conn.execute("UPDATE referral_bonus_contributors SET status='reversed',reversed_at=? WHERE id=?", (_iso(now), contributor["id"]))
        remaining = int(conn.execute("SELECT COUNT(*) FROM referral_bonus_contributors WHERE referrer_user_id=? AND period_key=? AND status<>'reversed'", (contributor["referrer_user_id"], contributor["period_key"])).fetchone()[0])
        period_row = conn.execute("SELECT * FROM referral_bonus_periods WHERE id=?", (contributor["period_id"],)).fetchone()
        target = 0
        if period_row:
            try:
                snapshot = json.loads(str(period_row["policy_snapshot_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("奖金期间政策无效。") from exc
            canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if not hmac.compare_digest(hashlib.sha256(canonical.encode()).hexdigest(), str(period_row["policy_sha256"])):
                raise ValueError("奖金期间政策审计摘要不一致。")
            try:
                targets = [int(t["cumulative_amount_minor"]) for t in snapshot.get("tiers", []) if remaining >= int(t["qualified_count"])]
                target = max(targets, default=0)
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError("奖金期间档位无效。") from exc
        awarded = int(conn.execute("SELECT COALESCE(SUM(award_delta_minor-reversed_amount_minor),0) FROM referral_bonus_award_events WHERE referrer_user_id=? AND period_key=?", (contributor["referrer_user_id"], contributor["period_key"])).fetchone()[0])
        claw = max(0, awarded - target)
        awards = conn.execute("SELECT * FROM referral_bonus_award_events WHERE referrer_user_id=? AND period_key=? AND status IN ('pending','available') ORDER BY id DESC", (contributor["referrer_user_id"], contributor["period_key"])).fetchall()
        withdrawal_cancelled = False
        for award in awards:
            if claw <= 0:
                break
            amount = min(claw, int(award["award_delta_minor"]) - int(award["reversed_amount_minor"]))
            if amount <= 0:
                continue
            new_reversed = int(award["reversed_amount_minor"]) + amount
            status = "reversed" if new_reversed == int(award["award_delta_minor"]) else award["status"]
            conn.execute("UPDATE referral_bonus_award_events SET status=?,reversed_amount_minor=? WHERE id=?", (status, new_reversed, award["id"]))
            bucket = "pending" if award["status"] == "pending" else "available"
            if bucket == "available" and not withdrawal_cancelled:
                ReferralWalletService.cancel_open_withdrawal_in_transaction(conn, int(award["referrer_user_id"]), reason="奖金对应订单发生退款或拒付，系统已释放待付款金额。", now=now)
                withdrawal_cancelled = True
            _ledger_batch(conn, user_id=int(award["referrer_user_id"]), legs=[(bucket, -amount)], entry_type="bonus_clawback", group_key=f"bonus:{award['public_id']}:reversal", reference_type="bonus", reference_id=award["public_id"], batch_key=f"bonus:{award['public_id']}:reversal:{new_reversed}", now=now)
            claw -= amount
        conn.execute("UPDATE referral_bonus_periods SET current_target_minor=? WHERE id=?", (target, contributor["period_id"]))
        clawback_minor = max(0, awarded - target)
        if clawback_minor:
            _audit(
                conn, actor_user_id=None, actor_kind="system", action="BONUS_CLAWBACK",
                entity_type="bonus_contributor", entity_public_id=str(contributor["public_id"]),
                details={"source_order_no": str(source_order_no), "clawback_minor": clawback_minor}, now=now,
            )

    @staticmethod
    def portal_progress(conn: Any, user_id: int, now: datetime) -> tuple[list[dict[str, int]], dict[str, Any]]:
        """Return only persisted bonus facts for the referrer's current HKT period."""
        from core.referral_affiliate_common import HONG_KONG

        period_key = now.astimezone(HONG_KONG).strftime("%Y-%m")
        period = conn.execute(
            "SELECT * FROM referral_bonus_periods WHERE referrer_user_id=? AND period_key=?",
            (int(user_id), period_key),
        ).fetchone()
        if not period:
            return [], {
                "period_key": period_key, "qualified_count": 0, "current_target_minor": 0,
                "earned_amount_minor": 0, "clawed_back_minor": 0, "net_amount_minor": 0,
                "status": "not_qualified",
            }
        try:
            snapshot = json.loads(str(period["policy_snapshot_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("奖金期间政策无效。") from exc
        canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if not hmac.compare_digest(hashlib.sha256(canonical.encode()).hexdigest(), str(period["policy_sha256"])):
            raise ValueError("奖金期间政策审计摘要不一致。")
        try:
            tiers = [
                {"qualified_count": int(tier["qualified_count"]),
                 "cumulative_amount_minor": int(tier["cumulative_amount_minor"])}
                for tier in snapshot.get("tiers", [])
            ]
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("奖金期间档位无效。") from exc
        totals = conn.execute(
            """SELECT COALESCE(SUM(award_delta_minor),0) earned,
                      COALESCE(SUM(reversed_amount_minor),0) clawed,
                      SUM(status='pending') pending
                 FROM referral_bonus_award_events WHERE period_id=?""",
            (int(period["id"]),),
        ).fetchone()
        earned, clawed = int(totals["earned"]), int(totals["clawed"])
        net = earned - clawed
        if not net and clawed:
            status = "clawed_back"
        elif clawed:
            status = "partially_clawed_back"
        elif int(totals["pending"] or 0):
            status = "pending"
        elif earned:
            status = "earned"
        else:
            status = "not_qualified"
        qualified_count = int(conn.execute(
            "SELECT COUNT(*) FROM referral_bonus_contributors WHERE period_id=? AND status<>'reversed'",
            (int(period["id"]),),
        ).fetchone()[0])
        return tiers, {
            "period_key": period_key, "qualified_count": qualified_count,
            "current_target_minor": int(period["current_target_minor"]),
            "earned_amount_minor": earned, "clawed_back_minor": clawed,
            "net_amount_minor": net, "status": status,
        }
