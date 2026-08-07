from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from core.database import DatabaseManager
from core.plans import (
    can, csv_import_limit, strategy_condition_limit, strategy_generation_limit,
)
from core.sandbox import SandboxClient, validate_user_code
from core.signal_imports import DISCLAIMER, SignalImportService, parse_csv
from core.strategy_evaluation import _option_metrics, evaluate_rule_strategy
from core.strategy_generator import generate_backtrader, validate_generated_code
from core.strategy_parser import parse_strategy
from core.user_profiles import UserProfileService, profile_tags


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "extensions.db"))


def _user(db, plan="专业版") -> int:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db.execute(
        """INSERT INTO users(email,password_hash,plan_type,subscription_expire,created_at,email_verified_at)
           VALUES (?,?,?,?,?,?)""",
        (f"{plan}@example.com", "hash", plan, "2099-01-01T00:00:00+00:00", now, now),
    )
    return int(db.fetch_one("SELECT id FROM users WHERE email=?", (f"{plan}@example.com",))["id"])


def _signal(index: int) -> dict:
    return {
        "signal_id": f"SIG-20260807-{index:03d}", "symbol": "AAPL", "action": "buy",
        "quantity": 10, "price": 150 + index, "timestamp": f"2026-08-07T10:{index:02d}:00Z",
        "strategy": "雙均線", "confidence": 0.78, "disclaimer": DISCLAIMER,
    }


def test_migration_runner_is_recorded_and_idempotent(db):
    versions = {row["version"] for row in db.fetch_all("SELECT version FROM schema_migrations")}
    assert {"0001_strategy_platform.sql", "0002_roadmap_locale.sql", "0003_price_alert_outbox.sql"} <= versions
    assert db.fetch_one("SELECT name FROM sqlite_master WHERE name='strategy_definitions'")
    same = DatabaseManager(db._db_path)
    assert same.fetch_one("SELECT COUNT(*) count FROM schema_migrations")["count"] == len(versions)


def test_extension_plan_limits_are_progressive():
    assert not can("标准版", "csv_import")
    assert can("高级版", "csv_import") and not can("高级版", "code_import")
    assert can("专业版", "code_import") and can("专业版", "api_signal_import")
    assert can("定制版", "strategy_template_save")
    assert strategy_generation_limit("标准版") == 3
    assert strategy_generation_limit("高级版") == 10
    assert strategy_generation_limit("专业版") is None
    assert strategy_condition_limit("标准版") == 1
    assert csv_import_limit("高级版") == 3 and csv_import_limit("专业版") is None


def test_csv_and_api_imports_validate_quota_and_idempotency(db):
    user_id = _user(db, "高级版")
    service = SignalImportService(db)
    csv_data = "標的,日期,操作,數量,價格\nAAPL,2026-08-07T10:00:00Z,買入,10,150.25\n".encode()
    parsed = parse_csv(csv_data)
    assert parsed[0]["action"] == "buy"

    first = service.import_signals(user_id, "高级版", [_signal(1)], import_type="csv")
    retry = service.import_signals(user_id, "高级版", [_signal(1)], import_type="csv")
    assert first["created"] is True and retry["created"] is False
    service.import_signals(user_id, "高级版", [_signal(2)], import_type="csv")
    service.import_signals(user_id, "高级版", [_signal(3)], import_type="csv")
    assert service.import_signals(user_id, "高级版", [_signal(1)], import_type="csv")["created"] is False
    with pytest.raises(PermissionError, match="3"):
        service.import_signals(user_id, "高级版", [_signal(4)], import_type="csv")
    with pytest.raises(PermissionError):
        service.import_signals(user_id, "高级版", [_signal(5)], import_type="api")


def test_parser_generator_and_lookahead_guards():
    parsed = parse_strategy(
        "當 AAPL 股價突破 200 日均線且 RSI 低於 30 時買入，當股價跌破 50 日均線時賣出",
        max_conditions=5,
        use_remote=False,
    )
    assert parsed["symbol"] == "AAPL"
    assert len(parsed["entry"]) == 2 and parsed["execution_timing"] == "next_bar_open"
    source = generate_backtrader(parsed)
    compile(source, "<test>", "exec")
    validate_generated_code(source)
    with pytest.raises(ValueError, match="未來資料"):
        parse_strategy(
            "當 AAPL 明日收盤價上漲時買入，當股價跌破 50 日均線時賣出",
            use_remote=False,
        )


def test_rule_backtest_executes_after_signal_bar():
    index = pd.date_range("2025-01-01", periods=100, freq="D")
    close = pd.Series([100.0] * 99 + [200.0], index=index)
    definition = {
        "parameters": {"period": 20},
        "rules": {
            "entry": [{"indicator": "price", "operator": "cross_above_ma", "period_param": "period"}],
            "exit": [{"indicator": "price", "operator": "cross_below_ma", "period_param": "period"}],
        },
    }
    result = evaluate_rule_strategy(close, definition)
    assert result["total_return"] <= 0
    assert max(result["equity_curve"]) <= 100_000


def test_mean_reversion_negates_threshold_without_negating_period():
    index = pd.date_range("2025-01-01", periods=100, freq="D")
    close = pd.Series([100.0] * 80 + [90.0] * 20, index=index)
    definition = {
        "parameters": {"period": 20, "entry_deviations": 2.0},
        "rules": {
            "entry": [{
                "indicator": "zscore", "operator": "lt", "period_param": "period",
                "value_from_param": "entry_deviations", "negate": True,
            }],
            "exit": [{"indicator": "zscore", "operator": "gt", "period_param": "period", "value": 0.0}],
        },
    }

    result = evaluate_rule_strategy(close, definition)

    assert len(result["equity_curve"]) == len(close)


def test_option_catalog_metrics_accept_internal_trade_column_locale():
    close = pd.Series(
        [100.0 + index * 0.2 for index in range(100)],
        index=pd.date_range("2025-01-01", periods=100, freq="D"),
    )
    metrics = _option_metrics(close, {
        "name": "买入 Call",
        "parameters": {"dte": 30, "strike_shift": 0, "quantity": 1},
        "rules": {"option_strategy_name": "买入 Call"},
    })

    assert set(metrics) >= {"total_return", "max_drawdown", "sharpe_ratio", "profit_loss_ratio"}


def test_sandbox_rejects_file_network_and_system_access_and_never_executes_locally(db, monkeypatch):
    for source in ("import os\n", "open('secret.txt').read()", "import socket\n", "import builtins\nbuiltins.open('x')"):
        with pytest.raises(ValueError):
            validate_user_code(source)
    user_id = _user(db)
    monkeypatch.delenv("TRADEAI_SANDBOX_URL", raising=False)
    result = SandboxClient(db).submit(
        user_id, "专业版", "import backtrader as bt\nclass S(bt.Strategy):\n    pass\n"
    )
    assert result["status"] == "quarantined"
    assert result["sandbox"] == "not_configured"


def test_user_profile_tags_and_aggregation_are_internal_and_stable(db):
    assert profile_tags(
        backtest_frequency=2, preferred_strategy="買入 Call",
        average_holding_days=14, preferred_win_rate=.55,
    ) == ["期權探索型", "短線激進型"]
    user_id = _user(db)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for index in range(4):
        db.execute(
            """INSERT INTO backtest_records
               (user_id,strategy_name,symbol,start_date,end_date,return_rate,max_drawdown,
                win_rate,total_trades,params,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, "買入 Call", "AAPL", "2025-01-01", "2026-01-01", .1, -.1, .6, 5, '{"dte": 14}', now),
        )
    profile = UserProfileService(db).aggregate(user_id)
    assert profile["preferred_strategy"] == "買入 Call"
    assert "期權探索型" in profile["tags"] and "短線激進型" in profile["tags"]
