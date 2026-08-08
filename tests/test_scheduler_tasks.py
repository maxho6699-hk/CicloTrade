"""Production scheduler checkpoints use New York market time."""

from scheduler.tasks import STRATEGY_CHECKPOINTS, build_scheduler


def test_strategy_checkpoints_cover_us_market_sessions():
    scheduler = build_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert STRATEGY_CHECKPOINTS == (
        ("strategy_premarket_score", "premarket", "mon-fri", 8, 45),
        ("strategy_intraday_score", "intraday", "mon-fri", 12, 30),
        ("strategy_after_close_score", "after_close", "mon-fri", 16, 1),
        ("strategy_overnight_score", "overnight", "sun,mon-thu", 20, 5),
    )
    for job_id, cycle_slot, days, hour, minute in STRATEGY_CHECKPOINTS:
        job = jobs[job_id]
        fields = {field.name: str(field) for field in job.trigger.fields}
        assert job.args == (cycle_slot,)
        assert str(job.trigger.timezone) == "America/New_York"
        assert fields["day_of_week"] == days
        assert fields["hour"] == str(hour)
        assert fields["minute"] == str(minute)
