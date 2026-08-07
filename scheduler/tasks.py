# -*- coding: utf-8 -*-
"""APScheduler 进程级任务注册。"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from scheduler.jobs import (
    aggregate_user_profiles,
    downgrade_expired_subscriptions,
    evaluate_strategy_catalog,
    notify_expiring_subscriptions,
    notify_inactive_users,
    process_quant_signal_notifications,
    refresh_saved_strategy_performance,
    scan_price_alerts,
)


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
    scheduler.add_job(scan_price_alerts, "interval", minutes=1, id="price_alert_scan", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(process_quant_signal_notifications, "interval", minutes=1, id="quant_signal_delivery", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(downgrade_expired_subscriptions, "interval", minutes=15, id="subscription_expiry", replace_existing=True)
    scheduler.add_job(notify_expiring_subscriptions, "cron", hour=9, minute=30, id="renewal_reminders", replace_existing=True)
    scheduler.add_job(notify_inactive_users, "cron", hour=10, minute=0, id="inactive_users", replace_existing=True)
    scheduler.add_job(
        evaluate_strategy_catalog, "cron", day_of_week="mon-fri", hour=17, minute=30,
        timezone="America/New_York", id="strategy_daily_score", replace_existing=True,
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        refresh_saved_strategy_performance, "cron", day_of_week="mon-fri", hour=18, minute=15,
        timezone="America/New_York", id="strategy_performance", replace_existing=True,
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        aggregate_user_profiles, "cron", day_of_week="sun", hour=3, minute=0,
        id="user_profile_weekly", replace_existing=True, max_instances=1, coalesce=True,
    )
    return scheduler
