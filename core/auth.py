# -*- coding: utf-8 -*-
"""持久化邮箱认证、JWT、单会话和 IP 白名单。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import os
import re
import secrets
from typing import Any

import bcrypt
import jwt

from core.database import DatabaseManager, get_database
from core.plans import parse_referral_code


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DUMMY_HASH = b"$2b$12$Fe4u7h6eNY6lnHmKFop.gObDpx6xRdtrnt9KTZNDoHvLRGzRZTZ4K"


class AuthError(ValueError):
    """可安全显示给用户的认证错误。"""


@dataclass(frozen=True)
class AuthResult:
    user: dict[str, Any]
    access_token: str
    refresh_token: str
    new_ip: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def email_verification_required() -> bool:
    configured = os.getenv("REQUIRE_EMAIL_VERIFICATION")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("APP_ENV", "development").strip().lower() == "production"


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY")
    if not secret or len(secret) < 32:
        raise AuthError("JWT_SECRET_KEY 尚未配置为至少 32 个字符，认证服务已停止。")
    return secret


def _token(
    user_id: int,
    session_id: str,
    kind: str,
    expires: timedelta,
    *,
    token_id: str | None = None,
) -> str:
    now = _now()
    claims = {"sub": str(user_id), "sid": session_id, "type": kind, "iat": now, "exp": now + expires}
    if token_id:
        claims["jti"] = token_id
    return jwt.encode(
        claims,
        _jwt_secret(),
        algorithm="HS256",
    )


def _decode_token(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise AuthError("登录凭证无效。")
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            options={
                "require": ["sub", "sid", "type", "iat", "exp"],
                "verify_exp": verify_exp,
            },
        )
    except jwt.PyJWTError as exc:
        raise AuthError("登录凭证已失效，请重新登录。") from exc
    if (
        not isinstance(payload.get("sub"), str)
        or not payload["sub"].isdigit()
        or not isinstance(payload.get("sid"), str)
        or not payload["sid"]
        or payload.get("type") not in {"access", "refresh"}
    ):
        raise AuthError("登录凭证内容无效。")
    return payload


def validate_password(password: str) -> None:
    if (
        not isinstance(password, str)
        or len(password) < 12
        or len(password.encode("utf-8")) > 72
        or not re.search(r"[A-Za-z]", password)
        or not re.search(r"\d", password)
    ):
        raise AuthError("密码需为 12 至 72 个字节，并同时包含英文字母和数字。")


def validate_display_name(display_name: str) -> str:
    cleaned = display_name.strip()
    if not cleaned or len(cleaned) > 80 or any(ord(char) < 32 for char in cleaned):
        raise AuthError("显示名称必须为 1 至 80 个可见字符。")
    return cleaned


class AuthService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def _rate_key(action: str, email: str, ip_address: str) -> str:
        value = f"{action}:{email.strip().lower()}:{(ip_address or 'unknown')[:64]}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _check_rate_limit(self, key: str, now: datetime) -> None:
        row = self.db.fetch_one(
            "SELECT blocked_until FROM auth_rate_limits WHERE rate_key=?", (key,)
        )
        if row and row["blocked_until"]:
            try:
                blocked_until = datetime.fromisoformat(row["blocked_until"])
            except (TypeError, ValueError):
                return
            if blocked_until > now:
                raise AuthError("尝试次数过多，请稍后再试。")

    def _record_attempt(
        self,
        key: str,
        now: datetime,
        *,
        limit: int,
        window: timedelta,
        block: timedelta,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempts,window_started,blocked_until FROM auth_rate_limits WHERE rate_key=?", (key,)
            ).fetchone()
            attempts = 1
            started = now
            if row:
                try:
                    blocked_until = datetime.fromisoformat(row["blocked_until"]) if row["blocked_until"] else None
                except (TypeError, ValueError):
                    blocked_until = None
                if blocked_until and blocked_until > now:
                    raise AuthError("尝试次数过多，请稍后再试。")
                try:
                    existing_start = datetime.fromisoformat(row["window_started"])
                    if now - existing_start <= window:
                        attempts = int(row["attempts"]) + 1
                        started = existing_start
                except (TypeError, ValueError):
                    pass
            blocked_until = _iso(now + block) if attempts >= limit else None
            conn.execute(
                """INSERT INTO auth_rate_limits (rate_key,attempts,window_started,blocked_until)
                   VALUES (?,?,?,?) ON CONFLICT(rate_key) DO UPDATE SET
                   attempts=excluded.attempts,window_started=excluded.window_started,
                   blocked_until=excluded.blocked_until""",
                (key, attempts, _iso(started), blocked_until),
            )

    def register(
        self,
        email: str,
        password: str,
        display_name: str,
        agreed: bool,
        referral: str = "",
        *,
        ip_address: str | None = None,
    ) -> dict[str, Any] | None:
        email = email.strip().lower()
        if len(email) > 254 or not EMAIL_RE.fullmatch(email):
            raise AuthError("请输入有效的邮箱地址。")
        if not agreed:
            raise AuthError("注册前必须同意用户协议、隐私政策和风险披露。")
        validate_password(password)
        display_name = validate_display_name(display_name)
        if ip_address:
            ip_address = ip_address[:64]
            now = _now()
            rate_key = self._rate_key("register-ip", "*", ip_address)
            self._check_rate_limit(rate_key, now)
            self._record_attempt(
                rate_key,
                now,
                limit=5,
                window=timedelta(hours=1),
                block=timedelta(hours=1),
            )
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        try:
            with self.db.transaction() as conn:
                now = _iso()
                cursor = conn.execute(
                    """INSERT INTO users
                       (email,password_hash,display_name,created_at,email_verified_at)
                       VALUES (?,?,?,?,?)""",
                    (email, password_hash, display_name, now, None if email_verification_required() else now),
                )
                user_id = int(cursor.lastrowid)
                referrer_id = parse_referral_code(referral)
                if referrer_id and referrer_id != user_id and conn.execute(
                    "SELECT 1 FROM users WHERE id=? AND is_active=1", (referrer_id,)
                ).fetchone():
                    conn.execute(
                        "INSERT INTO referrals (referrer_id,referee_id,status,created_at) VALUES (?,?,?,?)",
                        (referrer_id, user_id, "registered", _iso()),
                    )
                conn.execute(
                    "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                    (user_id, "REGISTER", "用户同意协议并完成邮箱注册", _iso()),
                )
        except Exception as exc:
            if "UNIQUE constraint failed: users.email" in str(exc):
                return None
            raise
        return self.get_user(user_id)

    def login(self, email: str, password: str, ip_address: str, user_agent: str) -> AuthResult:
        if not isinstance(email, str) or not isinstance(password, str):
            raise AuthError("邮箱或密码不正确。")
        email = email.strip().lower()
        if len(email) > 254 or len(password.encode("utf-8")) > 72:
            raise AuthError("邮箱或密码不正确。")
        ip_address = (ip_address or "unknown")[:64]
        now = _now()
        rate_key = self._rate_key("login", email, ip_address)
        ip_rate_key = self._rate_key("login-ip", "*", ip_address)
        account_rate_key = self._rate_key("login-account", email, "*")
        for key in (rate_key, ip_rate_key, account_rate_key):
            self._check_rate_limit(key, now)
        user = self.db.fetch_one("SELECT * FROM users WHERE email=?", (email,))
        if not user:
            bcrypt.checkpw(password.encode("utf-8"), DUMMY_HASH)
            valid = False
        else:
            locked_until = user.get("locked_until")
            if locked_until and datetime.fromisoformat(locked_until) > now:
                raise AuthError("登录失败次数过多，请在锁定期结束后再试。")
            password_valid = bcrypt.checkpw(
                password.encode("utf-8"), user["password_hash"].encode("ascii")
            )
            valid = bool(user.get("is_active")) and password_valid
        if not valid:
            for key, limit in ((rate_key, 5), (ip_rate_key, 20), (account_rate_key, 12)):
                self._record_attempt(
                    key,
                    now,
                    limit=limit,
                    window=timedelta(minutes=15),
                    block=timedelta(minutes=15),
                )
            raise AuthError("邮箱或密码不正确。")
        if email_verification_required() and not user.get("email_verified_at"):
            raise AuthError("请先完成注册邮箱验证，再登录 CicloTrade。")

        user_agent = (user_agent or "unknown")[:500]
        new_ip = False
        session_id = secrets.token_urlsafe(32)
        refresh_id = secrets.token_urlsafe(32)
        with self.db.transaction() as conn:
            known = conn.execute(
                "SELECT id,is_active FROM user_ip_whitelist WHERE user_id=? AND ip_address=?",
                (user["id"], ip_address),
            ).fetchone()
            if known and not known["is_active"]:
                raise AuthError("此 IP 已被停用，请联系管理员重新启用。")
            if not known:
                count = conn.execute(
                    "SELECT COUNT(*) FROM user_ip_whitelist WHERE user_id=? AND is_active=1", (user["id"],)
                ).fetchone()[0]
                if count >= 3:
                    raise AuthError("此账户已绑定 3 个 IP。请联系 Telegram @Maxooo8 或 support@ciclotrade.com。")
                conn.execute(
                    """INSERT INTO user_ip_whitelist (user_id,ip_address,first_seen,last_used,is_active)
                       VALUES (?,?,?,?,1)
                       ON CONFLICT(user_id,ip_address) DO UPDATE SET
                       last_used=excluded.last_used,is_active=1""",
                    (user["id"], ip_address, _iso(now), _iso(now)),
                )
                new_ip = True
            else:
                conn.execute(
                    "UPDATE user_ip_whitelist SET last_used=? WHERE id=?", (_iso(now), known[0])
                )
            conn.execute("UPDATE user_sessions SET is_active=0 WHERE user_id=?", (user["id"],))
            conn.execute(
                """INSERT INTO user_sessions
                   (user_id,session_token,refresh_token_hash,ip_address,user_agent,login_time,last_active,is_active)
                   VALUES (?,?,?,?,?,?,?,1)""",
                (
                    user["id"],
                    session_id,
                    _token_digest(refresh_id),
                    ip_address,
                    user_agent,
                    _iso(now),
                    _iso(now),
                ),
            )
            conn.execute(
                "UPDATE users SET failed_attempts=0,locked_until=NULL,last_login=? WHERE id=?",
                (_iso(now), user["id"]),
            )
            conn.execute(
                "DELETE FROM auth_rate_limits WHERE rate_key IN (?,?)",
                (rate_key, account_rate_key),
            )
            conn.execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (user["id"], "LOGIN", f"IP={ip_address}; new_ip={new_ip}", _iso(now)),
            )
        access_minutes = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "120"))
        refresh_days = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
        return AuthResult(
            self.get_user(int(user["id"])),
            _token(int(user["id"]), session_id, "access", timedelta(minutes=access_minutes)),
            _token(
                int(user["id"]),
                session_id,
                "refresh",
                timedelta(days=refresh_days),
                token_id=refresh_id,
            ),
            new_ip,
        )

    def verify(self, token: str) -> dict[str, Any]:
        payload = _decode_token(token)
        if payload.get("type") != "access":
            raise AuthError("登录凭证类型无效。")
        session = self.db.fetch_one(
            "SELECT * FROM user_sessions WHERE user_id=? AND session_token=? AND is_active=1",
            (int(payload["sub"]), payload["sid"]),
        )
        if not session:
            raise AuthError("账户已在其他设备登录，请重新登录。")
        self.db.execute("UPDATE user_sessions SET last_active=? WHERE id=?", (_iso(), session["id"]))
        return self.get_user(int(payload["sub"]))

    def refresh(self, refresh_token: str) -> tuple[str, str]:
        payload = _decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise AuthError("刷新凭证类型无效。")
        token_id = payload.get("jti")
        supplied_hash = _token_digest(token_id) if isinstance(token_id, str) and token_id else ""
        next_token_id = secrets.token_urlsafe(32)
        rejected = False
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = conn.execute(
                """SELECT id,user_id,refresh_token_hash FROM user_sessions
                   WHERE user_id=? AND session_token=? AND is_active=1""",
                (int(payload["sub"]), payload["sid"]),
            ).fetchone()
            if not session:
                rejected = True
            elif not secrets.compare_digest(session["refresh_token_hash"] or "", supplied_hash):
                conn.execute("UPDATE user_sessions SET is_active=0 WHERE id=?", (session["id"],))
                conn.execute(
                    "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                    (session["user_id"], "REFRESH_TOKEN_REUSE", "刷新凭证重用，已撤销会话", _iso()),
                )
                rejected = True
            else:
                conn.execute(
                    "UPDATE user_sessions SET refresh_token_hash=?,last_active=? WHERE id=?",
                    (_token_digest(next_token_id), _iso(), session["id"]),
                )
        if rejected:
            raise AuthError("刷新凭证已失效，请重新登录。")
        minutes = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "120"))
        days = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
        return (
            _token(int(payload["sub"]), payload["sid"], "access", timedelta(minutes=minutes)),
            _token(
                int(payload["sub"]),
                payload["sid"],
                "refresh",
                timedelta(days=days),
                token_id=next_token_id,
            ),
        )

    def logout(self, token: str) -> None:
        try:
            payload = _decode_token(token, verify_exp=False)
        except AuthError:
            return
        self.db.execute("UPDATE user_sessions SET is_active=0 WHERE session_token=?", (payload.get("sid"),))

    def request_password_reset(self, email: str, ip_address: str = "unknown") -> str | None:
        if not isinstance(email, str) or len(email) > 254:
            return None
        email = email.strip().lower()
        ip_address = (ip_address or "unknown")[:64]
        now = _now()
        rate_key = self._rate_key("reset", email, ip_address)
        ip_rate_key = self._rate_key("reset-ip", "*", ip_address)
        for key in (rate_key, ip_rate_key):
            self._check_rate_limit(key, now)
        for key, limit in ((rate_key, 3), (ip_rate_key, 10)):
            self._record_attempt(
                key,
                now,
                limit=limit,
                window=timedelta(minutes=30),
                block=timedelta(minutes=30),
            )
        user = self.db.fetch_one("SELECT id FROM users WHERE email=? AND is_active=1", (email,))
        if not user:
            return None
        recent = self.db.fetch_one(
            "SELECT created_at FROM password_resets WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (user["id"],),
        )
        if recent:
            try:
                if now - datetime.fromisoformat(recent["created_at"]) < timedelta(minutes=5):
                    return None
            except ValueError:
                pass
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (_iso(now), user["id"]),
            )
            conn.execute(
                "DELETE FROM password_resets WHERE created_at<?",
                (_iso(now - timedelta(days=7)),),
            )
            conn.execute(
                "INSERT INTO password_resets (user_id,token_hash,expires_at,created_at) VALUES (?,?,?,?)",
                (user["id"], digest, _iso(now + timedelta(minutes=30)), _iso(now)),
            )
        return token

    def request_email_verification(self, email: str, ip_address: str = "unknown") -> str | None:
        if not email_verification_required() or not isinstance(email, str) or len(email) > 254:
            return None
        email = email.strip().lower()
        ip_address = (ip_address or "unknown")[:64]
        now = _now()
        rate_key = self._rate_key("verify-email", email, ip_address)
        ip_rate_key = self._rate_key("verify-email-ip", "*", ip_address)
        for key in (rate_key, ip_rate_key):
            self._check_rate_limit(key, now)
        for key, limit in ((rate_key, 3), (ip_rate_key, 10)):
            self._record_attempt(
                key,
                now,
                limit=limit,
                window=timedelta(minutes=30),
                block=timedelta(minutes=30),
            )
        user = self.db.fetch_one(
            "SELECT id FROM users WHERE email=? AND is_active=1 AND email_verified_at IS NULL",
            (email,),
        )
        if not user:
            return None
        recent = self.db.fetch_one(
            "SELECT created_at FROM email_verifications WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (user["id"],),
        )
        if recent:
            try:
                if now - datetime.fromisoformat(recent["created_at"]) < timedelta(minutes=5):
                    return None
            except ValueError:
                pass
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE email_verifications SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (_iso(now), user["id"]),
            )
            conn.execute(
                "DELETE FROM email_verifications WHERE created_at<?",
                (_iso(now - timedelta(days=7)),),
            )
            conn.execute(
                """INSERT INTO email_verifications
                   (user_id,token_hash,expires_at,created_at) VALUES (?,?,?,?)""",
                (user["id"], digest, _iso(now + timedelta(minutes=30)), _iso(now)),
            )
        return token

    def verify_email(self, token: str) -> None:
        if not isinstance(token, str) or not 32 <= len(token.strip()) <= 128:
            raise AuthError("邮箱验证码无效或已过期。")
        digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = conn.execute(
                """SELECT v.id,v.user_id,v.expires_at,u.is_active
                   FROM email_verifications v JOIN users u ON u.id=v.user_id
                   WHERE v.token_hash=? AND v.used_at IS NULL""",
                (digest,),
            ).fetchone()
            try:
                valid = bool(record and record["is_active"]) and datetime.fromisoformat(
                    record["expires_at"]
                ) > _now()
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise AuthError("邮箱验证码无效或已过期。")
            claimed = conn.execute(
                "UPDATE email_verifications SET used_at=? WHERE id=? AND used_at IS NULL",
                (_iso(), record["id"]),
            )
            if claimed.rowcount != 1:
                raise AuthError("邮箱验证码无效或已过期。")
            conn.execute(
                "UPDATE users SET email_verified_at=COALESCE(email_verified_at,?) WHERE id=?",
                (_iso(), record["user_id"]),
            )
            conn.execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (record["user_id"], "EMAIL_VERIFIED", "注册邮箱验证完成", _iso()),
            )

    def reset_password(self, token: str, password: str) -> None:
        validate_password(password)
        if not isinstance(token, str) or not 32 <= len(token.strip()) <= 128:
            raise AuthError("重设密码链接无效或已过期。")
        digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
        record = self.db.fetch_one(
            "SELECT * FROM password_resets WHERE token_hash=? AND used_at IS NULL", (digest,)
        )
        try:
            valid = bool(record) and datetime.fromisoformat(record["expires_at"]) > _now()
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise AuthError("重设密码链接无效或已过期。")
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = conn.execute(
                "SELECT * FROM password_resets WHERE token_hash=? AND used_at IS NULL", (digest,)
            ).fetchone()
            if not record or datetime.fromisoformat(record["expires_at"]) <= _now():
                raise AuthError("重设密码链接无效或已过期。")
            claimed = conn.execute(
                "UPDATE password_resets SET used_at=? WHERE id=? AND used_at IS NULL",
                (_iso(), record["id"]),
            )
            if claimed.rowcount != 1:
                raise AuthError("重设密码链接无效或已过期。")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, record["user_id"]))
            conn.execute("UPDATE user_sessions SET is_active=0 WHERE user_id=?", (record["user_id"],))

    def get_user(self, user_id: int) -> dict[str, Any]:
        user = self.db.fetch_one(
            """SELECT id,email,display_name,plan_type,subscription_expire,created_at,last_login,
                      email_verified_at,is_active,is_admin FROM users WHERE id=?""",
            (user_id,),
        )
        if not user:
            raise AuthError("用户不存在或已停用。")
        return user

    def update_profile(self, user_id: int, display_name: str) -> None:
        self.db.execute("UPDATE users SET display_name=? WHERE id=?", (validate_display_name(display_name), user_id))

    def list_ips(self, user_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT id,ip_address,first_seen,last_used,is_active FROM user_ip_whitelist WHERE user_id=? ORDER BY last_used DESC",
            (user_id,),
        )

    def bootstrap_admin(self) -> None:
        email = os.getenv("TRADEAI_ADMIN_EMAIL", "").strip().lower()
        password = os.getenv("TRADEAI_ADMIN_PASSWORD", "")
        if not email:
            return
        existing = self.db.fetch_one("SELECT id,is_admin FROM users WHERE email=?", (email,))
        if existing:
            if not existing["is_admin"]:
                raise AuthError("管理员邮箱已被普通账户占用；为防止提权，自动引导已停止。")
            self.db.execute(
                "UPDATE users SET email_verified_at=COALESCE(email_verified_at,?) WHERE id=?",
                (_iso(), existing["id"]),
            )
            return
        if not password:
            raise AuthError("已配置管理员邮箱但缺少 TRADEAI_ADMIN_PASSWORD；自动引导已停止。")
        self.register(email, password, "系统管理员", True)
        self.db.execute(
            """UPDATE users SET is_admin=1,plan_type='专业版',
               email_verified_at=COALESCE(email_verified_at,?) WHERE email=?""",
            (_iso(), email),
        )
