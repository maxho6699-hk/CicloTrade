# -*- coding: utf-8 -*-
"""Persisted admin roles and audited operating controls."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import ipaddress
import json
from typing import Any

from core.auth import AuthService
from core.database import DatabaseManager, get_database
from core.plans import PLAN_ORDER
from payment.order_service import OrderService, grant_subscription_days


ROLE_LABELS = {
    "super_admin": "超级管理员",
    "support": "客服",
    "finance": "财务运营",
    "research": "研究编辑",
    "risk_audit": "风控审计",
}
ROLE_PERMISSIONS = {
    "super_admin": frozenset({"users", "billing", "research", "system", "audit", "roles", "membership_grant"}),
    "support": frozenset({"users", "audit", "membership_grant"}),
    "finance": frozenset({"billing", "audit"}),
    "research": frozenset({"research", "audit"}),
    "risk_audit": frozenset({"system", "audit"}),
}


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat(timespec="seconds")


class AdminService:
    """Small RBAC layer over the existing SQLite manager."""

    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        now = _iso()
        with self.db.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_roles (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('super_admin','support','finance','research','risk_audit')),
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS platform_controls (
                    control_key TEXT PRIMARY KEY,
                    control_value TEXT NOT NULL,
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (updated_by) REFERENCES users(id)
                );
                CREATE TRIGGER IF NOT EXISTS trg_admin_default_user_control
                AFTER INSERT ON users
                BEGIN
                    INSERT OR IGNORE INTO user_controls (user_id,opening_paused,updated_at)
                    VALUES (
                        NEW.id,
                        COALESCE((SELECT CAST(control_value AS INTEGER) FROM platform_controls WHERE control_key='opening_paused'),0),
                        strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    );
                END;
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO admin_roles (user_id,role,updated_at) "
                "SELECT id,'super_admin',? FROM users WHERE is_admin=1",
                (now,),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO platform_controls (control_key,control_value,updated_at) VALUES (?,?,?)",
                (
                    ("recommendations_published", "1", now),
                    ("opening_paused", "0", now),
                    ("annual_bonus_enabled", "1", now),
                    ("user_auto_trading_enabled", "1", now),
                ),
            )

    def _role_for_id(self, user_id: int) -> str:
        row = self.db.fetch_one(
            """SELECT u.is_admin,u.is_active,r.role FROM users u
               LEFT JOIN admin_roles r ON r.user_id=u.id WHERE u.id=?""",
            (user_id,),
        )
        if not row or not row["is_admin"] or not row["is_active"] or row.get("role") not in ROLE_LABELS:
            raise PermissionError("此账户没有可用的后台权限。")
        return str(row["role"])

    def role_for(self, user_id: int) -> str:
        return self._role_for_id(user_id)

    @staticmethod
    def has_permission(role: str, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS.get(role, ())

    def _require(self, actor_id: int, permission: str) -> str:
        role = self._role_for_id(actor_id)
        if not self.has_permission(role, permission):
            raise PermissionError("当前后台角色无权执行此操作。")
        return role

    @staticmethod
    def _audit(conn: Any, actor_id: int, action: str, details: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
            (actor_id, action, json.dumps(details, ensure_ascii=False), _iso()),
        )

    def dashboard_metrics(self, actor_id: int) -> dict[str, Any]:
        role = self._role_for_id(actor_id)
        metrics: dict[str, Any] = {}
        if self.has_permission(role, "users"):
            users = self.db.fetch_one(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) active
                   FROM users"""
            ) or {}
            metrics.update(
                users=int(users.get("total") or 0),
                active_users=int(users.get("active") or 0),
            )
        if self.has_permission(role, "billing"):
            billing = self.db.fetch_one(
                """SELECT
                          (SELECT COUNT(*) FROM users
                           WHERE plan_type!='免费版' AND datetime(subscription_expire)>datetime('now')) subscribed,
                          SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending,
                          COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) paid_amount
                   FROM subscription_orders"""
            ) or {}
            metrics.update(
                subscribers=int(billing.get("subscribed") or 0),
                pending_orders=int(billing.get("pending") or 0),
                paid_amount=float(billing.get("paid_amount") or 0),
            )
        if self.has_permission(role, "system"):
            risk = self.db.fetch_one(
                "SELECT COUNT(*) count FROM risk_log WHERE severity='CRITICAL' AND datetime(created_at)>=datetime('now','-1 day')"
            ) or {}
            metrics["critical_risk"] = int(risk.get("count") or 0)
        return metrics

    def list_users(self, actor_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "users")
        return self.db.fetch_all(
            """SELECT u.id,u.email,u.display_name,u.plan_type,u.subscription_expire,u.last_login,
                      u.failed_attempts,u.locked_until,u.is_active,u.is_admin,r.role admin_role,
                      (SELECT COUNT(*) FROM user_sessions s WHERE s.user_id=u.id AND s.is_active=1) active_sessions,
                      (SELECT COUNT(*) FROM user_ip_whitelist i WHERE i.user_id=u.id AND i.is_active=1) active_ips
               FROM users u LEFT JOIN admin_roles r ON r.user_id=u.id ORDER BY u.created_at DESC"""
        )

    def list_admins(self, actor_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "roles")
        return self.db.fetch_all(
            """SELECT u.id,u.email,u.display_name,u.is_active,r.role,r.updated_at
               FROM admin_roles r JOIN users u ON u.id=r.user_id ORDER BY u.email"""
        )

    def set_role(self, actor_id: int, user_id: int, role: str | None) -> None:
        self._require(actor_id, "roles")
        if role is not None and role not in ROLE_LABELS:
            raise ValueError("未知后台角色。")
        if actor_id == user_id and role != "super_admin":
            raise ValueError("不能降低或移除自己的超级管理员权限。")
        with self.db.transaction() as conn:
            target = conn.execute(
                "SELECT u.email,r.role FROM users u LEFT JOIN admin_roles r ON r.user_id=u.id WHERE u.id=?",
                (user_id,),
            ).fetchone()
            if not target:
                raise ValueError("用户不存在。")
            if target["role"] == "super_admin" and role != "super_admin":
                remaining = conn.execute(
                    """SELECT COUNT(*) FROM admin_roles r JOIN users u ON u.id=r.user_id
                       WHERE r.role='super_admin' AND r.user_id<>? AND u.is_active=1""",
                    (user_id,),
                ).fetchone()[0]
                if not remaining:
                    raise ValueError("系统必须保留至少一名超级管理员。")
            if role is None:
                conn.execute("DELETE FROM admin_roles WHERE user_id=?", (user_id,))
                conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (user_id,))
            else:
                conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (user_id,))
                conn.execute(
                    """INSERT INTO admin_roles (user_id,role,updated_at) VALUES (?,?,?)
                       ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,updated_at=excluded.updated_at""",
                    (user_id, role, _iso()),
                )
            self._audit(conn, actor_id, "ADMIN_ROLE_CHANGE", {"user_id": user_id, "role": role})

    def set_user_active(self, actor_id: int, user_id: int, active: bool) -> None:
        self._require(actor_id, "users")
        if actor_id == user_id and not active:
            raise ValueError("不能停用当前登录的管理员账户。")
        with self.db.transaction() as conn:
            target = conn.execute(
                "SELECT u.email,r.role FROM users u LEFT JOIN admin_roles r ON r.user_id=u.id WHERE u.id=?",
                (user_id,),
            ).fetchone()
            if not target:
                raise ValueError("用户不存在。")
            if not active and target["role"] == "super_admin":
                remaining = conn.execute(
                    """SELECT COUNT(*) FROM admin_roles r JOIN users u ON u.id=r.user_id
                       WHERE r.role='super_admin' AND r.user_id<>? AND u.is_active=1""",
                    (user_id,),
                ).fetchone()[0]
                if not remaining:
                    raise ValueError("系统必须保留至少一名启用中的超级管理员。")
            conn.execute("UPDATE users SET is_active=? WHERE id=?", (int(active), user_id))
            if not active:
                conn.execute("UPDATE user_sessions SET is_active=0 WHERE user_id=?", (user_id,))
            self._audit(conn, actor_id, "ADMIN_USER_STATUS", {"user_id": user_id, "active": active})

    def reset_sessions(self, actor_id: int, user_id: int) -> None:
        self._require(actor_id, "users")
        if actor_id == user_id:
            raise ValueError("请使用安全退出结束自己的会话。")
        with self.db.transaction() as conn:
            conn.execute("UPDATE user_sessions SET is_active=0 WHERE user_id=?", (user_id,))
            self._audit(conn, actor_id, "ADMIN_RESET_SESSIONS", {"user_id": user_id})

    def unlock_user(self, actor_id: int, user_id: int) -> None:
        self._require(actor_id, "users")
        with self.db.transaction() as conn:
            target = conn.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                raise ValueError("用户不存在。")
            conn.execute("UPDATE users SET failed_attempts=0,locked_until=NULL WHERE id=?", (user_id,))
            conn.execute(
                "DELETE FROM auth_rate_limits WHERE rate_key=?",
                (AuthService._rate_key("login-account", target["email"], "*"),),
            )
            self._audit(conn, actor_id, "ADMIN_UNLOCK_USER", {"user_id": user_id})

    def list_ips(self, actor_id: int, user_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "users")
        return self.db.fetch_all(
            "SELECT id,ip_address,first_seen,last_used,is_active FROM user_ip_whitelist WHERE user_id=? ORDER BY last_used DESC",
            (user_id,),
        )

    def list_sessions(self, actor_id: int, user_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "users")
        return self.db.fetch_all(
            """SELECT ip_address,user_agent,login_time,last_active,is_active FROM user_sessions
               WHERE user_id=? ORDER BY login_time DESC LIMIT 20""",
            (user_id,),
        )

    def add_ip(self, actor_id: int, user_id: int, value: str) -> None:
        self._require(actor_id, "users")
        address = str(ipaddress.ip_address(value.strip()))
        now = _iso()
        with self.db.transaction() as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM user_ip_whitelist WHERE user_id=? AND is_active=1", (user_id,)
            ).fetchone()[0]
            current = conn.execute(
                "SELECT is_active FROM user_ip_whitelist WHERE user_id=? AND ip_address=?", (user_id, address)
            ).fetchone()
            if active >= 3 and not (current and current["is_active"]):
                raise ValueError("此账户已有 3 个启用中的 IP，请先停用一个。")
            conn.execute(
                """INSERT INTO user_ip_whitelist (user_id,ip_address,first_seen,last_used,is_active)
                   VALUES (?,?,?,?,1) ON CONFLICT(user_id,ip_address)
                   DO UPDATE SET last_used=excluded.last_used,is_active=1""",
                (user_id, address, now, now),
            )
            self._audit(conn, actor_id, "ADMIN_ADD_IP", {"user_id": user_id, "ip": address})

    def remove_ip(self, actor_id: int, user_id: int, ip_id: int) -> None:
        self._require(actor_id, "users")
        with self.db.transaction() as conn:
            record = conn.execute(
                "SELECT ip_address FROM user_ip_whitelist WHERE id=? AND user_id=?",
                (ip_id, user_id),
            ).fetchone()
            if not record:
                raise ValueError("IP 记录不存在。")
            conn.execute("UPDATE user_ip_whitelist SET is_active=0 WHERE id=?", (ip_id,))
            conn.execute(
                "UPDATE user_sessions SET is_active=0 WHERE user_id=? AND ip_address=?",
                (user_id, record["ip_address"]),
            )
            self._audit(conn, actor_id, "ADMIN_REMOVE_IP", {"user_id": user_id, "ip_id": ip_id})

    def clear_ips(self, actor_id: int, user_id: int) -> None:
        self._require(actor_id, "users")
        with self.db.transaction() as conn:
            conn.execute("UPDATE user_ip_whitelist SET is_active=0 WHERE user_id=?", (user_id,))
            conn.execute("UPDATE user_sessions SET is_active=0 WHERE user_id=?", (user_id,))
            self._audit(conn, actor_id, "ADMIN_CLEAR_IPS", {"user_id": user_id})

    def adjust_subscription(self, actor_id: int, user_id: int, plan: str, days: int, reason: str = "后台调整订阅", note: str | None = None) -> str | None:
        self._require(actor_id, "billing")
        if plan not in PLAN_ORDER:
            raise ValueError("未知订阅方案。")
        reason = str(reason).strip()
        if not reason:
            raise ValueError("调整等级必须填写原因。")
        if plan != "免费版" and not 1 <= int(days) <= 3650:
            raise ValueError("订阅天数必须在 1 到 3650 天之间。")
        expiry: str | None = None
        with self.db.transaction() as conn:
            current = conn.execute("SELECT plan_type,subscription_expire FROM users WHERE id=?", (user_id,)).fetchone()
            if not current:
                raise ValueError("用户不存在。")
            if plan != "免费版":
                base = datetime.now(UTC)
                if current["subscription_expire"]:
                    try:
                        saved = datetime.fromisoformat(current["subscription_expire"])
                        if saved.tzinfo is None:
                            saved = saved.replace(tzinfo=UTC)
                        base = max(base, saved)
                    except ValueError:
                        pass
                expiry = _iso(base + timedelta(days=int(days)))
            conn.execute(
                "UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?", (plan, expiry, user_id)
            )
            conn.execute(
                """INSERT INTO user_membership_logs
                   (user_id,admin_id,operation_type,before_plan,after_plan,expire_days,expire_at,reason,note,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (user_id, actor_id, "adjust", current["plan_type"], plan, int(days) if plan != "免费版" else None, expiry, reason, note, _iso()),
            )
            self._audit(
                conn,
                actor_id,
                "ADMIN_SUBSCRIPTION_ADJUST",
                {"user_id": user_id, "plan": plan, "days": int(days), "expiry": expiry},
            )
        return expiry

    def grant_trial(
        self,
        actor_id: int,
        user_id: int,
        plan: str,
        days: int = 7,
        reason: str = "",
        note: str | None = None,
    ) -> str:
        """Support may grant a bounded trial; only billing roles can adjust plans."""
        self._require(actor_id, "membership_grant")
        if plan not in {"标准版", "高级版", "专业版"} or not 1 <= int(days) <= 90:
            raise ValueError("体验方案必须是标准/高级/专业版，天数为 1 至 90 天。")
        reason = str(reason).strip()
        if not reason or len(reason) > 240:
            raise ValueError("赠送体验必须填写原因。")
        now = datetime.now(UTC)
        expiry: str
        with self.db.transaction() as conn:
            current = conn.execute("SELECT plan_type,subscription_expire,email FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone()
            if not current:
                raise ValueError("用户不存在或已停用。")
            base = now
            if current["subscription_expire"]:
                try:
                    saved = datetime.fromisoformat(current["subscription_expire"])
                    if saved.tzinfo is None:
                        saved = saved.replace(tzinfo=UTC)
                    base = max(base, saved)
                except ValueError:
                    pass
            expiry = _iso(base + timedelta(days=int(days)))
            conn.execute("UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?", (plan, expiry, user_id))
            conn.execute(
                """INSERT INTO user_membership_logs
                   (user_id,admin_id,operation_type,before_plan,after_plan,expire_days,expire_at,reason,note,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (user_id, actor_id, "grant_trial", current["plan_type"], plan, int(days), expiry, reason, note, _iso()),
            )
            self._audit(conn, actor_id, "ADMIN_MEMBERSHIP_GRANT", {"user_id": user_id, "plan": plan, "days": int(days), "reason": reason})
        # Telegram is best-effort and only targets a verified, entitled private destination.
        try:
            from core.user_settings import load_user_settings
            from notification.telegram_bot import entitled_user_target, send_telegram, telegram_configured
            from notification.templates import telegram_membership
            user = self.db.fetch_one("SELECT id,plan_type,subscription_expire FROM users WHERE id=?", (user_id,)) or {}
            target = entitled_user_target(user, load_user_settings(user_id, self.db), "membership_update")
            if target and telegram_configured(target):
                send_telegram(telegram_membership(plan, expiry, reason), chat_id=target)
        except Exception:
            self.db.log_system_event("WARN", "NOTIFICATION", "赠送体验 Telegram 通知未送达", f"user={user_id}")
        return expiry

    def list_membership_logs(self, actor_id: int, limit: int = 500) -> list[dict[str, Any]]:
        self._require(actor_id, "audit")
        return self.db.fetch_all(
            """SELECT l.*,u.email user_email,a.email admin_email
               FROM user_membership_logs l JOIN users u ON u.id=l.user_id JOIN users a ON a.id=l.admin_id
               ORDER BY l.created_at DESC LIMIT ?""",
            (max(1, min(int(limit), 1000)),),
        )

    def payment_summary(self, actor_id: int) -> dict[str, Any]:
        self._require(actor_id, "billing")
        return self.db.fetch_one(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending,
                      SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) paid,
                      SUM(CASE WHEN status='refunded' THEN 1 ELSE 0 END) refunded,
                      COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) paid_amount
               FROM subscription_orders"""
        ) or {}

    def list_subscription_users(self, actor_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "billing")
        return self.db.fetch_all(
            """SELECT id,email,display_name,plan_type,subscription_expire,is_active
               FROM users ORDER BY email"""
        )

    def list_orders(self, actor_id: int, status: str = "全部", method: str = "全部") -> list[dict[str, Any]]:
        self._require(actor_id, "billing")
        valid_status = {"全部", "pending", "paid", "failed", "cancelled", "refunded"}
        valid_method = {"全部", "paddle", "paypal", "fps"}
        if status not in valid_status or method not in valid_method:
            raise ValueError("订单筛选条件无效。")
        clauses, params = [], []
        if status != "全部":
            clauses.append("o.status=?")
            params.append(status)
        if method != "全部":
            clauses.append("o.pay_method=?")
            params.append(method)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.fetch_all(
            f"""SELECT o.order_no,u.email,o.plan_type,o.billing_cycle,o.amount,o.currency,
                       o.pay_method,o.external_id,o.status,o.created_at,o.paid_at,o.refunded_at
                FROM subscription_orders o JOIN users u ON u.id=o.user_id
                {where} ORDER BY o.created_at DESC LIMIT 500""",
            tuple(params),
        )

    def reconciliation_rows(self, actor_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "billing")
        return self.db.fetch_all(
            """SELECT c.event_id,c.order_no,c.processed,c.created_at,
                      CASE WHEN o.order_no IS NULL THEN 0 ELSE 1 END matched,o.status order_status,o.external_id
               FROM payment_callbacks c LEFT JOIN subscription_orders o ON o.order_no=c.order_no
               ORDER BY c.created_at DESC LIMIT 500"""
        )

    def confirm_fps(self, actor_id: int, order_no: str) -> None:
        self._require(actor_id, "billing")
        order = self.db.fetch_one(
            "SELECT pay_method,status FROM subscription_orders WHERE order_no=?", (order_no,)
        )
        if not order or order["pay_method"] != "fps" or order["status"] != "pending":
            raise ValueError("该订单不是等待确认的 FPS 订单。")
        OrderService(self.db).process_callback(
            f"fps-admin-{order_no}",
            order_no,
            "paid",
            {"source": "admin", "admin_id": actor_id},
            audit_user_id=actor_id,
            audit_action="ADMIN_CONFIRM_FPS",
            audit_details={"order_no": order_no},
        )

    def record_external_refund(self, actor_id: int, order_no: str) -> None:
        """Record a refund already completed in the original payment channel."""
        self._require(actor_id, "billing")
        OrderService(self.db).mark_refunded(actor_id, order_no)

    def control_enabled(self, key: str, default: bool = False) -> bool:
        row = self.db.fetch_one("SELECT control_value FROM platform_controls WHERE control_key=?", (key,))
        return default if not row else str(row["control_value"]).lower() in {"1", "true", "yes", "on"}

    def set_recommendations_published(self, actor_id: int, enabled: bool) -> None:
        self._require(actor_id, "research")
        self._set_control(actor_id, "recommendations_published", enabled, "ADMIN_RECOMMENDATIONS_STATUS", "RESEARCH")

    def list_strategy_definitions(self, actor_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "research")
        from core.strategy_registry import StrategyRegistry

        registry = StrategyRegistry(self.db)
        registry.sync_catalog()
        return registry.list(active_only=False)

    def save_strategy_definition(self, actor_id: int, definition: dict[str, Any]) -> dict[str, Any]:
        self._require(actor_id, "research")
        from core.strategy_registry import StrategyRegistry

        return StrategyRegistry(self.db).register(
            definition, created_by=actor_id, audit_actor=actor_id
        )

    def set_strategy_active(self, actor_id: int, strategy_key: str, active: bool) -> dict[str, Any]:
        self._require(actor_id, "research")
        from core.strategy_registry import StrategyRegistry

        return StrategyRegistry(self.db).set_active(
            strategy_key, active, audit_actor=actor_id
        )

    def set_annual_bonus_enabled(self, actor_id: int, enabled: bool) -> None:
        self._require(actor_id, "billing")
        self._set_control(
            actor_id,
            "annual_bonus_enabled",
            enabled,
            "ADMIN_ANNUAL_BONUS_STATUS",
            "BILLING",
        )

    def set_user_auto_trading_enabled(self, actor_id: int, enabled: bool) -> dict[str, int]:
        if self._role_for_id(actor_id) != "super_admin":
            raise PermissionError("仅超级管理员可控制用户实盘自动交易服务。")
        from notification.telegram_bot import send_telegram, telegram_configured, verified_user_target
        from notification.templates import telegram_live_service_paused, telegram_live_service_resumed

        now = _iso()
        targets: list[str] = []
        affected = 0
        with self.db.transaction() as conn:
            current_row = conn.execute(
                "SELECT control_value FROM platform_controls WHERE control_key='user_auto_trading_enabled'"
            ).fetchone()
            current = bool(current_row and str(current_row[0]).lower() in {"1", "true", "yes", "on"})
            if current == bool(enabled):
                return {"affected": 0, "notified": 0}
            rows = conn.execute(
                "SELECT user_id,settings_json FROM user_settings"
            ).fetchall()
            for row in rows:
                try:
                    settings = json.loads(row["settings_json"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(settings, dict):
                    continue
                selected = settings.get("live_auto_platform_suspended") is True if enabled else settings.get("live_auto_enabled") is True
                if not selected:
                    continue
                affected += 1
                if not enabled:
                    settings["live_auto_enabled"] = False
                    settings["live_auto_platform_suspended"] = True
                    conn.execute(
                        "UPDATE user_settings SET settings_json=?,updated_at=? WHERE user_id=?",
                        (json.dumps(settings, ensure_ascii=False), now, int(row["user_id"])),
                    )
                if target := verified_user_target(settings):
                    targets.append(target)
            conn.execute(
                """INSERT INTO platform_controls (control_key,control_value,updated_by,updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(control_key) DO UPDATE SET control_value=excluded.control_value,
                   updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                ("user_auto_trading_enabled", str(int(enabled)), actor_id, now),
            )
            self._audit(conn, actor_id, "ADMIN_USER_AUTO_TRADING_STATUS", {"enabled": enabled, "affected": affected})
            conn.execute(
                "INSERT INTO system_events (event_type,component,message,details,created_at) VALUES (?,?,?,?,?)",
                ("CONTROL", "TRADING", "ADMIN_USER_AUTO_TRADING_STATUS", f"enabled={enabled}; affected={affected}; admin={actor_id}", now),
            )

        message = telegram_live_service_resumed() if enabled else telegram_live_service_paused()
        notified = 0
        for target in targets:
            try:
                if not telegram_configured(target):
                    raise RuntimeError("Telegram 外部通知当前不可用。")
                send_telegram(message, target)
                notified += 1
            except RuntimeError as exc:
                self.db.log_system_event("WARN", "TELEGRAM", "实盘服务状态通知失败", str(exc)[:500])
        return {"affected": affected, "notified": notified}

    def set_global_opening_paused(self, actor_id: int, paused: bool) -> None:
        self._require(actor_id, "system")
        now = _iso()
        with self.db.transaction() as conn:
            user_ids = [row[0] for row in conn.execute("SELECT id FROM users")]
            conn.executemany(
                """INSERT INTO user_controls (user_id,opening_paused,updated_at) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET opening_paused=excluded.opening_paused,updated_at=excluded.updated_at""",
                ((user_id, int(paused), now) for user_id in user_ids),
            )
            conn.execute(
                """INSERT INTO platform_controls (control_key,control_value,updated_by,updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(control_key) DO UPDATE SET control_value=excluded.control_value,
                   updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                ("opening_paused", str(int(paused)), actor_id, now),
            )
            self._audit(conn, actor_id, "ADMIN_GLOBAL_OPENING_PAUSE", {"paused": paused})
            conn.execute(
                "INSERT INTO system_events (event_type,component,message,details,created_at) VALUES (?,?,?,?,?)",
                ("CONTROL", "RISK", "全局暂停新开仓" if paused else "全局恢复新开仓", f"admin={actor_id}", now),
            )

    def record_data_source_verification(
        self, actor_id: int, provider: str, action: str, success: bool
    ) -> None:
        """Audit provider verification without storing captcha contents."""
        self._require(actor_id, "system")
        provider = provider.strip().lower()
        if provider not in {"opend"} or action not in {"request_captcha", "submit_captcha"}:
            raise ValueError("未知的数据源验证操作。")
        now = _iso()
        details = {"provider": provider, "action": action, "success": bool(success)}
        with self.db.transaction() as conn:
            self._audit(conn, actor_id, "ADMIN_DATA_SOURCE_VERIFICATION", details)
            conn.execute(
                "INSERT INTO system_events (event_type,component,message,details,created_at) VALUES (?,?,?,?,?)",
                (
                    "CONTROL" if success else "WARN",
                    "MARKET_DATA",
                    "数据源验证操作完成" if success else "数据源验证操作失败",
                    json.dumps(details, ensure_ascii=False),
                    now,
                ),
            )

    def _set_control(
        self, actor_id: int, key: str, enabled: bool, action: str, component: str
    ) -> None:
        now = _iso()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO platform_controls (control_key,control_value,updated_by,updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(control_key) DO UPDATE SET control_value=excluded.control_value,
                   updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (key, str(int(enabled)), actor_id, now),
            )
            self._audit(conn, actor_id, action, {"enabled": enabled})
            conn.execute(
                "INSERT INTO system_events (event_type,component,message,details,created_at) VALUES (?,?,?,?,?)",
                ("CONTROL", component, action, f"enabled={enabled}; admin={actor_id}", now),
            )

    def recommendation_activity(self, actor_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "research")
        return self.db.fetch_all(
            """SELECT s.user_id,u.email,s.strategy_name,s.action,s.result,s.created_at
               FROM strategy_action_logs s LEFT JOIN users u ON u.id=s.user_id
               WHERE s.action IN ('BACKTEST','SIGNAL_COPY','STRATEGY_DETAIL')
               ORDER BY s.created_at DESC LIMIT 200"""
        )

    def list_roadmap(self, actor_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "research")
        return self.db.fetch_all("SELECT * FROM roadmap_items ORDER BY sort_order,quarter,id")

    def save_roadmap_item(
        self,
        actor_id: int,
        quarter: str,
        name: str,
        status: str,
        description: str = "",
        sort_order: int = 0,
    ) -> int:
        self._require(actor_id, "research")
        quarter, name, description = quarter.strip(), name.strip(), description.strip()
        if not quarter or not name or status not in {"live", "in_progress", "planning", "evaluating"}:
            raise ValueError("路线图季度、名称或状态无效。")
        now = _iso()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO roadmap_items (quarter,name,status,sort_order,description,updated_at,created_at) VALUES (?,?,?,?,?,?,?)",
                (quarter[:30], name[:120], status, int(sort_order), description[:1000], now, now),
            )
            self._audit(conn, actor_id, "ADMIN_ROADMAP_CREATE", {"id": cursor.lastrowid, "name": name, "status": status})
            return int(cursor.lastrowid)

    def delete_roadmap_item(self, actor_id: int, item_id: int) -> None:
        self._require(actor_id, "research")
        with self.db.transaction() as conn:
            if conn.execute("DELETE FROM roadmap_items WHERE id=?", (int(item_id),)).rowcount != 1:
                raise ValueError("路线图项目不存在。")
            self._audit(conn, actor_id, "ADMIN_ROADMAP_DELETE", {"id": int(item_id)})

    def list_social_share_requests(self, actor_id: int) -> list[dict[str, Any]]:
        self._require(actor_id, "research")
        return self.db.fetch_all(
            """SELECT r.id,r.user_id,u.email,u.display_name,r.reference,r.created_at
               FROM rewards r JOIN users u ON u.id=r.user_id
               WHERE r.reward_type='SOCIAL_PENDING'
               ORDER BY r.created_at,r.id"""
        )

    def review_social_share(
        self, actor_id: int, reward_id: int, approved: bool, days: int = 0
    ) -> str | None:
        self._require(actor_id, "research")
        days = int(days)
        if approved and not 1 <= days <= 15:
            raise ValueError("分享奖励必须在 1 到 15 天之间。")
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            # ponytail: SQLite-wide write lock; use row locks if review traffic outgrows SQLite.
            conn.execute("BEGIN IMMEDIATE")
            request = conn.execute(
                """SELECT id,user_id,reference FROM rewards
                   WHERE id=? AND reward_type='SOCIAL_PENDING'""",
                (int(reward_id),),
            ).fetchone()
            if not request:
                raise ValueError("分享申请不存在或已经审核。")
            reward_type = "SOCIAL_APPROVED" if approved else "SOCIAL_REJECTED"
            changed = conn.execute(
                """UPDATE rewards SET reward_type=?,days=?
                   WHERE id=? AND reward_type='SOCIAL_PENDING'""",
                (reward_type, days if approved else 0, int(reward_id)),
            )
            if changed.rowcount != 1:
                raise ValueError("分享申请状态已变更，请刷新后重试。")
            expiry = (
                grant_subscription_days(conn, request["user_id"], days, "标准版", now)
                if approved
                else None
            )
            self._audit(
                conn,
                actor_id,
                "ADMIN_SOCIAL_SHARE_APPROVE" if approved else "ADMIN_SOCIAL_SHARE_REJECT",
                {
                    "reward_id": int(reward_id),
                    "user_id": request["user_id"],
                    "reference": request["reference"],
                    "days": days if approved else 0,
                    "expiry": expiry,
                },
            )
        return expiry

    def list_audit(self, actor_id: int, limit: int = 500) -> list[dict[str, Any]]:
        self._require(actor_id, "audit")
        return self.db.fetch_all(
            """SELECT l.id,l.created_at,COALESCE(u.email,'系统') actor,l.action_type,l.details
               FROM user_action_logs l LEFT JOIN users u ON u.id=l.user_id
               WHERE l.action_type LIKE 'ADMIN_%' ORDER BY l.created_at DESC LIMIT ?""",
            (max(1, min(int(limit), 1000)),),
        )
