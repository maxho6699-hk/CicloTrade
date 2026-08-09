# -*- coding: utf-8 -*-
"""
============================================================================
量化交易系统 V5.1 - 数据库管理模块
============================================================================
使用 SQLite 持久化存储：订单记录、成交记录、风控日志、通知消息、策略绩效等
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from core.exceptions import DatabaseError


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _sql_statements(script: str):
    """Yield complete SQLite statements, including multi-line triggers."""
    buffer: list[str] = []
    for line in script.splitlines():
        buffer.append(line)
        candidate = "\n".join(buffer).strip()
        if candidate and sqlite3.complete_statement(candidate):
            yield candidate
            buffer.clear()
    remainder = "\n".join(buffer).strip()
    if remainder:
        raise DatabaseError("数据库迁移包含不完整的 SQL 语句。")

# 数据库表创建 SQL
CREATE_TABLES_SQL = """
-- 订单记录表
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,           -- 券商订单ID
    symbol TEXT NOT NULL,                    -- 标的代码
    side TEXT NOT NULL,                      -- BUY / SELL
    order_type TEXT NOT NULL,                -- LMT / STP_LMT / MKT
    quantity REAL NOT NULL,                  -- 数量
    price REAL,                              -- 限价
    stop_price REAL,                         -- 止损价
    status TEXT NOT NULL,                    -- PENDING / FILLED / CANCELLED / REJECTED
    filled_qty REAL DEFAULT 0,              -- 已成交数量
    filled_avg_price REAL,                   -- 成交均价
    strategy_name TEXT,                      -- 发起策略名称
    reason TEXT,                             -- 订单原因/备注
    created_at TEXT NOT NULL,                -- 创建时间 (HKT)
    updated_at TEXT,                         -- 最后更新时间 (HKT)
    account_mode TEXT DEFAULT 'paper'        -- paper / live
);

-- 成交记录表
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,           -- 成交ID
    order_id TEXT NOT NULL,                  -- 关联订单ID
    symbol TEXT NOT NULL,                    -- 标的代码
    side TEXT NOT NULL,                      -- BUY / SELL
    quantity REAL NOT NULL,                  -- 成交数量
    price REAL NOT NULL,                     -- 成交价格
    commission REAL DEFAULT 0,              -- 佣金
    trade_time TEXT NOT NULL,                -- 成交时间 (HKT)
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

-- 风控拦截日志表
CREATE TABLE IF NOT EXISTS risk_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,                         -- 关联用户；系统级事件可为空
    event_type TEXT NOT NULL,                -- 拦截类型：POSITION_LIMIT / DAILY_LOSS / COOLDOWN 等
    symbol TEXT,                             -- 相关标的
    details TEXT,                            -- 详细说明
    severity TEXT DEFAULT 'WARN',            -- INFO / WARN / CRITICAL
    created_at TEXT NOT NULL                 -- 时间 (HKT)
);

-- 通知消息持久化表
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_type TEXT NOT NULL,                  -- 消息类型：ORDER / FILL / RISK / SYSTEM 等
    title TEXT,                              -- 标题
    content TEXT NOT NULL,                   -- 内容
    push_status TEXT DEFAULT 'pending',      -- pending / sent / failed
    created_at TEXT NOT NULL                 -- 时间 (HKT)
);

-- 策略绩效表
CREATE TABLE IF NOT EXISTS strategy_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,             -- 策略名称
    eval_date TEXT NOT NULL,                 -- 评估日期
    total_return REAL DEFAULT 0,            -- 总收益率
    max_drawdown REAL DEFAULT 0,            -- 最大回撤
    profit_loss_ratio REAL DEFAULT 0,       -- 盈亏比
    consecutive_losses INTEGER DEFAULT 0,   -- 连续亏损次数
    total_trades INTEGER DEFAULT 0,         -- 总交易次数
    win_rate REAL DEFAULT 0,                -- 胜率
    score REAL DEFAULT 0,                   -- 综合评分
    is_active INTEGER DEFAULT 0,            -- 是否当前生效策略
    created_at TEXT NOT NULL                 -- 时间 (HKT)
);

-- 账户快照表（每小时记录）
CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    total_assets REAL,                       -- 总资产
    available_funds REAL,                    -- 可用资金
    market_value REAL,                       -- 持仓市值
    daily_pnl REAL,                          -- 当日盈亏
    total_pnl REAL,                          -- 累计盈亏
    margin_used REAL,                        -- 已用保证金
    snapshot_time TEXT NOT NULL              -- 快照时间 (HKT)
);

-- 系统事件日志表
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,                -- CONNECTION / DISCONNECT / ERROR / SWITCH 等
    component TEXT NOT NULL,                 -- 组件：MARKET / TRADING / STRATEGY / SYSTEM
    message TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL                 -- 时间 (HKT)
);

-- 用户与访问控制
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    plan_type TEXT NOT NULL DEFAULT '免费版',
    subscription_expire TEXT,
    created_at TEXT NOT NULL,
    last_login TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    email_verified_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_admin INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subscription_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    plan_type TEXT NOT NULL,
    billing_cycle TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'HKD',
    pay_method TEXT NOT NULL,
    external_id TEXT,
    external_price_id TEXT,
    external_capture_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    paid_at TEXT,
    refunded_at TEXT,
    previous_plan_type TEXT,
    previous_subscription_expire TEXT,
    entitlement_days INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS payment_callbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    order_no TEXT,
    raw_data TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    return_rate REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    win_rate REAL NOT NULL,
    total_trades INTEGER NOT NULL,
    params TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS strategy_action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    action TEXT NOT NULL,
    params TEXT,
    result TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action_type TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER NOT NULL,
    referee_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'registered',
    created_at TEXT NOT NULL,
    FOREIGN KEY (referrer_id) REFERENCES users(id),
    FOREIGN KEY (referee_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_token TEXT UNIQUE NOT NULL,
    refresh_token_hash TEXT,
    ip_address TEXT NOT NULL,
    user_agent TEXT,
    login_time TEXT NOT NULL,
    last_active TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_ip_whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ip_address TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_used TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (user_id, ip_address),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS email_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS auth_rate_limits (
    rate_key TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL,
    window_started TEXT NOT NULL,
    blocked_until TEXT
);

CREATE TABLE IF NOT EXISTS price_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    operator TEXT NOT NULL,
    target_price REAL NOT NULL,
    conditions TEXT,
    logic TEXT NOT NULL DEFAULT 'AND',
    is_active INTEGER NOT NULL DEFAULT 1,
    last_triggered TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS price_alert_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    notification_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    FOREIGN KEY (alert_id) REFERENCES price_alerts(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (notification_id) REFERENCES notifications(id)
);

CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    reward_type TEXT NOT NULL,
    days INTEGER NOT NULL DEFAULT 0,
    reference TEXT,
    source_order_no TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, reward_type, reference),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_controls (
    user_id INTEGER PRIMARY KEY,
    opening_paused INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS broker_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    account_alias TEXT NOT NULL,
    external_account_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'paper',
    is_active INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'not_configured',
    last_checked TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, provider, external_account_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_membership_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    operation_type TEXT NOT NULL,
    before_plan TEXT,
    after_plan TEXT,
    expire_days INTEGER,
    expire_at TEXT,
    reason TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (admin_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS roadmap_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quarter TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planning',
    sort_order INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    consent INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS share_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS mystic_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    dimension TEXT NOT NULL,
    prompt TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    outcome_3d REAL,
    checked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 不可变量化事件账本。修正与撤销只能通过引用旧事件的新事件表达。
CREATE TABLE IF NOT EXISTS quant_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_key TEXT NOT NULL,
    source TEXT NOT NULL,
    external_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('signal','correction','reversal')),
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    corrects_event_id INTEGER,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    leg_count INTEGER NOT NULL CHECK(leg_count >= 0),
    metadata_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    UNIQUE(source, external_event_id),
    CHECK(event_type != 'reversal' OR leg_count = 0),
    CHECK(event_type != 'correction' OR leg_count > 0),
    CHECK((event_type = 'signal' AND corrects_event_id IS NULL) OR
          (event_type != 'signal' AND corrects_event_id IS NOT NULL)),
    FOREIGN KEY (corrects_event_id) REFERENCES quant_events(id)
);

CREATE TABLE IF NOT EXISTS quant_event_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    leg_no INTEGER NOT NULL CHECK(leg_no >= 0),
    market TEXT NOT NULL CHECK(market IN ('US','CN')),
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    instrument_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    currency TEXT NOT NULL CHECK(currency IN ('USD','CNY')),
    option_expiry TEXT,
    option_right TEXT CHECK(option_right IS NULL OR option_right IN ('CALL','PUT')),
    option_strike REAL,
    target_quantity REAL NOT NULL,
    quantity_delta REAL NOT NULL CHECK(quantity_delta != 0),
    price REAL NOT NULL CHECK(price > 0),
    multiplier REAL NOT NULL CHECK(multiplier > 0),
    commission REAL NOT NULL CHECK(commission >= 0),
    UNIQUE(event_id, leg_no),
    UNIQUE(event_id, instrument_key),
    CHECK((instrument_type = 'stock' AND option_expiry IS NULL AND option_right IS NULL
           AND option_strike IS NULL AND multiplier = 1) OR
          (instrument_type = 'option' AND market = 'US' AND option_expiry IS NOT NULL
           AND option_right IS NOT NULL AND option_strike > 0)),
    FOREIGN KEY (event_id) REFERENCES quant_events(id)
);

CREATE TABLE IF NOT EXISTS quant_event_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('telegram')),
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE(event_id,user_id,channel,instrument_type,symbol),
    FOREIGN KEY (event_id) REFERENCES quant_events(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS quant_equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_key TEXT NOT NULL,
    source TEXT NOT NULL,
    external_snapshot_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    currency TEXT NOT NULL CHECK(currency IN ('USD','CNY')),
    initial_cash REAL NOT NULL CHECK(initial_cash >= 0),
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    total_equity REAL NOT NULL,
    total_pnl REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    UNIQUE(source,external_snapshot_id,currency),
    CHECK(ABS((cash + market_value) - total_equity) < 0.01),
    CHECK(ABS((realized_pnl + unrealized_pnl) - total_pnl) < 0.01),
    CHECK(ABS((initial_cash + total_pnl) - total_equity) < 0.01)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(trade_time);
CREATE INDEX IF NOT EXISTS idx_risk_log_time ON risk_log(created_at);
CREATE INDEX IF NOT EXISTS idx_strategy_perf ON strategy_performance(strategy_name, eval_date);
CREATE INDEX IF NOT EXISTS idx_notifications_time ON notifications(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_email_verifications_user ON email_verifications(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON price_alerts(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_actions_user ON user_action_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_backtests_user ON backtest_records(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_subscription_external ON subscription_orders(external_id);
CREATE INDEX IF NOT EXISTS idx_quant_events_ledger ON quant_events(ledger_key, id);
CREATE INDEX IF NOT EXISTS idx_quant_events_strategy ON quant_events(strategy_name, strategy_version, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quant_events_corrects_once
    ON quant_events(corrects_event_id) WHERE corrects_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_quant_legs_instrument ON quant_event_legs(instrument_key, event_id);
CREATE INDEX IF NOT EXISTS idx_quant_deliveries_pending
    ON quant_event_deliveries(status, next_attempt_at, id);
CREATE INDEX IF NOT EXISTS idx_quant_deliveries_user
    ON quant_event_deliveries(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quant_equity_timeline
    ON quant_equity_snapshots(ledger_key, currency, captured_at, id);

CREATE TRIGGER IF NOT EXISTS trg_quant_events_no_update
BEFORE UPDATE ON quant_events BEGIN
    SELECT RAISE(ABORT, 'quant_events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_events_no_delete
BEFORE DELETE ON quant_events BEGIN
    SELECT RAISE(ABORT, 'quant_events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_legs_no_update
BEFORE UPDATE ON quant_event_legs BEGIN
    SELECT RAISE(ABORT, 'quant_event_legs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_legs_no_delete
BEFORE DELETE ON quant_event_legs BEGIN
    SELECT RAISE(ABORT, 'quant_event_legs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_legs_bounded_insert
BEFORE INSERT ON quant_event_legs
WHEN NEW.leg_no >= COALESCE((SELECT leg_count FROM quant_events WHERE id=NEW.event_id), 0)
     OR (SELECT COUNT(*) FROM quant_event_legs WHERE event_id=NEW.event_id) >=
        COALESCE((SELECT leg_count FROM quant_events WHERE id=NEW.event_id), 0)
BEGIN
    SELECT RAISE(ABORT, 'quant event leg set is sealed');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_equity_no_update
BEFORE UPDATE ON quant_equity_snapshots BEGIN
    SELECT RAISE(ABORT, 'quant_equity_snapshots are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_equity_no_delete
BEFORE DELETE ON quant_equity_snapshots BEGIN
    SELECT RAISE(ABORT, 'quant_equity_snapshots are append-only');
END;
"""


class DatabaseManager:
    """
    SQLite 数据库管理器
    提供线程安全的数据库操作接口
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()

        # 确保数据库目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # 初始化表结构
        self._init_tables()

    @contextmanager
    def _get_connection(self):
        """获取数据库连接（上下文管理器，线程安全）"""
        conn = sqlite3.connect(self._db_path, timeout=15)
        conn.row_factory = sqlite3.Row  # 支持按列名访问
        conn.execute("PRAGMA journal_mode=WAL")  # WAL模式提高并发性能
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """执行原子事务，异常时回滚。"""
        with self._lock:
            with self._get_connection() as conn:
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

    def _init_tables(self) -> None:
        """初始化数据库表结构"""
        try:
            with self._lock:
                with self._get_connection() as conn:
                    conn.executescript(CREATE_TABLES_SQL)
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(risk_log)")}
                    if "user_id" not in columns:
                        conn.execute("ALTER TABLE risk_log ADD COLUMN user_id INTEGER")
                    order_columns = {row[1] for row in conn.execute("PRAGMA table_info(subscription_orders)")}
                    if "previous_plan_type" not in order_columns:
                        conn.execute("ALTER TABLE subscription_orders ADD COLUMN previous_plan_type TEXT")
                    if "previous_subscription_expire" not in order_columns:
                        conn.execute("ALTER TABLE subscription_orders ADD COLUMN previous_subscription_expire TEXT")
                    if "external_price_id" not in order_columns:
                        conn.execute("ALTER TABLE subscription_orders ADD COLUMN external_price_id TEXT")
                    if "external_capture_id" not in order_columns:
                        conn.execute("ALTER TABLE subscription_orders ADD COLUMN external_capture_id TEXT")
                    if "entitlement_days" not in order_columns:
                        conn.execute("ALTER TABLE subscription_orders ADD COLUMN entitlement_days INTEGER")
                    conn.execute(
                        """UPDATE subscription_orders SET entitlement_days=CASE billing_cycle
                           WHEN 'monthly' THEN 30 WHEN 'quarterly' THEN 90
                           WHEN 'yearly' THEN 455 ELSE 3650 END
                           WHERE entitlement_days IS NULL"""
                    )
                    reward_columns = {row[1] for row in conn.execute("PRAGMA table_info(rewards)")}
                    if "source_order_no" not in reward_columns:
                        conn.execute("ALTER TABLE rewards ADD COLUMN source_order_no TEXT")
                    alert_columns = {row[1] for row in conn.execute("PRAGMA table_info(price_alerts)")}
                    if "conditions" not in alert_columns:
                        conn.execute("ALTER TABLE price_alerts ADD COLUMN conditions TEXT")
                    if "logic" not in alert_columns:
                        conn.execute("ALTER TABLE price_alerts ADD COLUMN logic TEXT NOT NULL DEFAULT 'AND'")
                    conn.execute(
                        """UPDATE price_alerts SET conditions=json_array(json_object('type','price','operator',operator,'value',target_price))
                           WHERE conditions IS NULL OR conditions=''"""
                    )
                    broker_columns = {row[1] for row in conn.execute("PRAGMA table_info(broker_accounts)")}
                    for name, definition in (("status", "TEXT NOT NULL DEFAULT 'not_configured'"), ("last_checked", "TEXT"), ("metadata_json", "TEXT")):
                        if name not in broker_columns:
                            conn.execute(f"ALTER TABLE broker_accounts ADD COLUMN {name} {definition}")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_membership_logs_user ON user_membership_logs(user_id,created_at)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_roadmap_sort ON roadmap_items(sort_order,quarter)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_telegram_verifications_user ON telegram_verifications(user_id,created_at)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_share_events_user_time ON share_events(user_id,created_at)")
                    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
                    if "email_verified_at" not in user_columns:
                        conn.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
                        conn.execute(
                            "UPDATE users SET email_verified_at=COALESCE(created_at,datetime('now'))"
                        )
                    session_columns = {row[1] for row in conn.execute("PRAGMA table_info(user_sessions)")}
                    if "refresh_token_hash" not in session_columns:
                        conn.execute("ALTER TABLE user_sessions ADD COLUMN refresh_token_hash TEXT")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_risk_log_user ON risk_log(user_id, created_at)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_subscription_external ON subscription_orders(external_id)")
                    conn.commit()
                    self._run_migrations(conn)
        except Exception as e:
            raise DatabaseError(f"数据库初始化失败: {e}")

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply each pending migration atomically and record its filename."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version TEXT PRIMARY KEY,
                   applied_at TEXT NOT NULL
               )"""
        )
        conn.commit()
        if not MIGRATIONS_DIR.exists():
            return
        applied = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            try:
                conn.execute("BEGIN IMMEDIATE")
                for statement in _sql_statements(path.read_text(encoding="utf-8")):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",
                    (path.name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def execute(self, sql: str, params: tuple = ()) -> int:
        """
        执行 SQL（INSERT/UPDATE/DELETE），返回影响行数
        """
        try:
            with self.transaction() as conn:
                return conn.execute(sql, params).rowcount
        except Exception as e:
            raise DatabaseError(f"数据库执行失败: {e}\nSQL: {sql}")

    def execute_many(self, sql: str, params_list: List[tuple]) -> int:
        """批量执行 SQL"""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.executemany(sql, params_list)
                    conn.commit()
                    return cursor.rowcount
            except Exception as e:
                raise DatabaseError(f"数据库批量执行失败: {e}")

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        """查询单条记录"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(sql, params)
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            raise DatabaseError(f"数据库查询失败: {e}")

    def fetch_all(self, sql: str, params: tuple = ()) -> List[Dict]:
        """查询多条记录"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(sql, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            raise DatabaseError(f"数据库查询失败: {e}")

    # ==================== 订单相关 ====================

    def insert_order(self, order_data: Dict) -> int:
        """插入订单记录"""
        sql = """
        INSERT OR REPLACE INTO orders
        (order_id, symbol, side, order_type, quantity, price, stop_price,
         status, strategy_name, reason, created_at, account_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            order_data["order_id"],
            order_data["symbol"],
            order_data["side"],
            order_data.get("order_type", "LMT"),
            order_data["quantity"],
            order_data.get("price"),
            order_data.get("stop_price"),
            order_data.get("status", "PENDING"),
            order_data.get("strategy_name"),
            order_data.get("reason"),
            order_data.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            order_data.get("account_mode", "paper"),
        )
        return self.execute(sql, params)

    def update_order_status(self, order_id: str, status: str,
                            filled_qty: float = None,
                            filled_avg_price: float = None) -> int:
        """更新订单状态"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = """UPDATE orders SET status=?, updated_at=?"""
        params = [status, now]

        if filled_qty is not None:
            sql += ", filled_qty=?"
            params.append(filled_qty)
        if filled_avg_price is not None:
            sql += ", filled_avg_price=?"
            params.append(filled_avg_price)

        sql += " WHERE order_id=?"
        params.append(order_id)
        return self.execute(sql, tuple(params))

    def get_orders_by_status(self, status: str) -> List[Dict]:
        """按状态查询订单"""
        return self.fetch_all(
            "SELECT * FROM orders WHERE status=? ORDER BY created_at DESC", (status,)
        )

    def get_today_orders(self) -> List[Dict]:
        """获取今日订单"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.fetch_all(
            "SELECT * FROM orders WHERE created_at LIKE ? ORDER BY created_at DESC",
            (f"{today}%",),
        )

    # ==================== 成交相关 ====================

    def insert_trade(self, trade_data: Dict) -> int:
        """插入成交记录"""
        sql = """
        INSERT OR REPLACE INTO trades
        (trade_id, order_id, symbol, side, quantity, price, commission, trade_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            trade_data["trade_id"],
            trade_data["order_id"],
            trade_data["symbol"],
            trade_data["side"],
            trade_data["quantity"],
            trade_data["price"],
            trade_data.get("commission", 0),
            trade_data.get("trade_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        return self.execute(sql, params)

    def get_today_trades(self) -> List[Dict]:
        """获取今日成交"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.fetch_all(
            "SELECT * FROM trades WHERE trade_time LIKE ? ORDER BY trade_time DESC",
            (f"{today}%",),
        )

    # ==================== 风控日志 ====================

    def log_risk_event(self, event_type: str, symbol: str = None,
                       details: str = None, severity: str = "WARN", user_id: int | None = None) -> int:
        """记录风控事件"""
        sql = """
        INSERT INTO risk_log (user_id, event_type, symbol, details, severity, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (user_id, event_type, symbol, details, severity,
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return self.execute(sql, params)

    def get_risk_logs(self, limit: int = 100, user_id: int | None = None) -> List[Dict]:
        """获取风控日志"""
        if user_id is not None:
            return self.fetch_all(
                "SELECT * FROM risk_log WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
            )
        return self.fetch_all(
            "SELECT * FROM risk_log ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    # ==================== 通知消息 ====================

    def insert_notification(self, msg_type: str, title: str, content: str,
                            push_status: str = "pending") -> int:
        """持久化通知消息"""
        sql = """
        INSERT INTO notifications (msg_type, title, content, push_status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """
        params = (msg_type, title, content, push_status,
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return self.execute(sql, params)

    # ==================== 策略绩效 ====================

    def save_strategy_performance(self, perf_data: Dict) -> int:
        """保存策略绩效"""
        sql = """
        INSERT INTO strategy_performance
        (strategy_name, eval_date, total_return, max_drawdown, profit_loss_ratio,
         consecutive_losses, total_trades, win_rate, score, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            perf_data["strategy_name"],
            perf_data.get("eval_date", datetime.now().strftime("%Y-%m-%d")),
            perf_data.get("total_return", 0),
            perf_data.get("max_drawdown", 0),
            perf_data.get("profit_loss_ratio", 0),
            perf_data.get("consecutive_losses", 0),
            perf_data.get("total_trades", 0),
            perf_data.get("win_rate", 0),
            perf_data.get("score", 0),
            perf_data.get("is_active", 0),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        return self.execute(sql, params)

    def get_latest_performances(self) -> List[Dict]:
        """获取最新策略绩效排名（按评分降序）"""
        return self.fetch_all("""
            SELECT * FROM strategy_performance
            WHERE eval_date = (SELECT MAX(eval_date) FROM strategy_performance)
            ORDER BY score DESC
        """)

    # ==================== 账户快照 ====================

    def save_account_snapshot(self, snapshot: Dict) -> int:
        """保存账户快照"""
        sql = """
        INSERT INTO account_snapshots
        (total_assets, available_funds, market_value, daily_pnl, total_pnl,
         margin_used, snapshot_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            snapshot.get("total_assets"),
            snapshot.get("available_funds"),
            snapshot.get("market_value"),
            snapshot.get("daily_pnl"),
            snapshot.get("total_pnl"),
            snapshot.get("margin_used"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        return self.execute(sql, params)

    def get_recent_snapshots(self, hours: int = 24) -> List[Dict]:
        """获取近期账户快照"""
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        return self.fetch_all(
            "SELECT * FROM account_snapshots WHERE snapshot_time >= ? ORDER BY snapshot_time",
            (since,),
        )

    # ==================== 系统事件 ====================

    def log_system_event(self, event_type: str, component: str,
                         message: str, details: str = None) -> int:
        """记录系统事件"""
        sql = """
        INSERT INTO system_events (event_type, component, message, details, created_at)
        VALUES (?, ?, ?, ?, ?)
        """
        params = (event_type, component, message, details,
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return self.execute(sql, params)

    def get_system_events(self, limit: int = 200) -> List[Dict]:
        """获取系统事件"""
        return self.fetch_all(
            "SELECT * FROM system_events ORDER BY created_at DESC LIMIT ?", (limit,)
        )


def _database_path() -> str:
    """将 sqlite:/// URL 转为跨平台绝对路径。"""
    value = os.getenv("DATABASE_URL", "sqlite:///data/tradeai.db")
    if not value.startswith("sqlite:///"):
        raise DatabaseError("当前版本只支持 sqlite:/// 数据库地址。")
    raw_path = value.removeprefix("sqlite:///")
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return str(path.resolve())


@lru_cache(maxsize=1)
def get_database() -> DatabaseManager:
    """返回进程内共享的 SQLite 管理器。"""
    return DatabaseManager(_database_path())
