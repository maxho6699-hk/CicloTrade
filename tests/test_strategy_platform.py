from __future__ import annotations

from datetime import date, timedelta
import pytest

from core.database import DatabaseManager
from core.strategy_registry import StrategyRegistry
from core.strategy_scoring import StrategyScorer
from core.strategy_tracking import StrategyPerformanceTracker


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "strategy-platform.db"))


@pytest.fixture
def registry(db):
    result = StrategyRegistry(db)
    result.sync_catalog()
    return result


def _metrics(strategies):
    values = (
        (0.30, 0.05, 2.0, 3.0, 0),
        (0.15, 0.10, 1.2, 2.0, 2),
        (0.04, 0.20, 0.5, 1.2, 4),
        (-0.10, 0.40, -1.0, 0.5, 8),
    )
    return [
        {
            "strategy_key": strategy["key"],
            "total_return": total_return,
            "max_drawdown": drawdown,
            "sharpe_ratio": sharpe,
            "profit_loss_ratio": ratio,
            "consecutive_losses": losses,
        }
        for strategy, (total_return, drawdown, sharpe, ratio, losses) in zip(
            strategies, values, strict=True
        )
    ]


def _create_user(db, email):
    db.execute(
        "INSERT INTO users (email,password_hash,created_at,email_verified_at) VALUES (?,?,?,?)",
        (email, "not-a-real-hash", "2026-08-07T00:00:00+00:00", "2026-08-07T00:00:00+00:00"),
    )
    return int(db.fetch_one("SELECT id FROM users WHERE email=?", (email,))["id"])


def test_catalog_sync_and_database_registration_are_dynamic_and_preserve_admin_state(db):
    registry = StrategyRegistry(db)
    synced = registry.sync_catalog()

    assert len(synced) == 13
    assert len(registry.list(family="option")) == 8
    registry.set_active("option_long_call", False)
    registry.sync_catalog()
    assert registry.get("option_long_call")["active"] is False

    added = registry.register(
        {
            "key": "equity_breakout_v2",
            "name": "突破策略 V2",
            "family": "equity",
            "engine": "rules",
            "scenario": "区间突破后的趋势跟随",
            "description": "由管理后台动态注册，不需要修改核心代码。",
            "risk": "medium",
            "parameters": {"lookback": 55},
            "rules": {"entry": [{"indicator": "high", "operator": "breakout"}]},
        },
        created_by=None,
    )

    assert added["parameters"] == {"lookback": 55}
    assert StrategyRegistry(db).get("equity_breakout_v2")["engine"] == "rules"
    assert all(item["key"] != "option_long_call" for item in registry.list())


def test_plan_strategy_catalog_is_progressive_and_honors_admin_state(registry):
    free = registry.list_for_plan("免费版", family="option")
    standard = registry.list_for_plan("标准版", family="option")

    assert {item["key"] for item in free} == {"option_long_call"}
    assert {item["key"] for item in free} <= {item["key"] for item in standard}

    registry.set_active("option_long_call", False)
    assert registry.list_for_plan("免费版", family="option") == []
    assert all(item["key"] != "option_long_call" for item in registry.list_for_plan("标准版", family="option"))


def test_five_dimension_scoring_selects_top_three_and_is_idempotent(registry, db):
    strategies = registry.list()[:4]
    scorer = StrategyScorer(db)
    metrics = _metrics(strategies)

    ranked = scorer.evaluate(metrics, eval_date="2026-08-07")
    rerun = scorer.evaluate(metrics, eval_date="2026-08-07")

    assert ranked[0]["weighted_score"] == 100
    assert ranked[-1]["weighted_score"] == 0
    assert [row["lifecycle_status"] for row in ranked] == ["top3", "top3", "top3", "active"]
    assert [row["strategy_key"] for row in scorer.top_three()] == [
        row["strategy_key"] for row in ranked[:3]
    ]
    assert rerun == ranked
    assert db.fetch_one("SELECT COUNT(*) count FROM strategy_scores")["count"] == 4


def test_last_place_lifecycle_moves_to_watch_then_retire_pending(registry, db):
    strategies = registry.list()[:4]
    scorer = StrategyScorer(db)
    metrics = _metrics(strategies)
    start = date(2026, 1, 1)
    statuses = []

    for offset in range(60):
        ranked = scorer.evaluate(metrics, eval_date=start + timedelta(days=offset))
        statuses.append(ranked[-1]["lifecycle_status"])

    assert statuses[28] == "active"
    assert statuses[29] == "watch"
    assert statuses[58] == "watch"
    assert statuses[59] == "retire_pending"
    assert db.fetch_one(
        "SELECT lifecycle_status FROM strategy_scores ORDER BY eval_date DESC,rank_position DESC LIMIT 1"
    )["lifecycle_status"] == "retire_pending"


def test_saved_strategy_performance_is_idempotent_and_user_scoped(registry, db):
    owner = _create_user(db, "owner@example.com")
    stranger = _create_user(db, "stranger@example.com")
    tracker = StrategyPerformanceTracker(db)
    strategy_key = registry.list()[0]["key"]
    saved = tracker.save_strategy(
        owner,
        name="我的趋势策略",
        source_type="template",
        strategy_key=strategy_key,
        config={"fast_period": 20, "slow_period": 50},
    )

    with pytest.raises(KeyError, match="not found"):
        tracker.get(stranger, saved["id"])

    first = tracker.record_performance(
        owner,
        saved["id"],
        {
            "return_30d": 0.03,
            "max_drawdown": -0.08,
            "annual_return": 0.16,
            "sharpe_ratio": 1.1,
            "win_rate": 0.58,
            "equity_curve": [100_000, 103_000],
        },
        eval_date="2026-08-07",
    )
    updated = tracker.record_performance(
        owner,
        saved["id"],
        {
            "return_30d": 0.04,
            "max_drawdown": 0.07,
            "annual_return": 0.18,
            "sharpe_ratio": 1.2,
            "win_rate": 0.60,
            "equity_curve": [100_000, 104_000],
        },
        eval_date="2026-08-07",
    )

    assert first["max_drawdown"] == pytest.approx(0.08)
    assert updated["return_30d"] == pytest.approx(0.04)
    assert updated["equity_curve"] == [100_000, 104_000]
    assert len(tracker.history(owner, saved["id"])) == 1
    tracker.update_strategy(owner, saved["id"], active=False)
    assert tracker.list(owner) == []
