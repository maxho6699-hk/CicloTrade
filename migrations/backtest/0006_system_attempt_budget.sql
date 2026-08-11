-- Persist the Compute Gate's actual-attempt budget so claim can enforce it atomically.
ALTER TABLE backtest_jobs ADD COLUMN system_daily_attempt_limit INTEGER
    CHECK(system_daily_attempt_limit IS NULL OR system_daily_attempt_limit BETWEEN 1 AND 10000);
ALTER TABLE backtest_jobs ADD COLUMN system_budget_timezone TEXT
    CHECK(system_budget_timezone IS NULL OR length(system_budget_timezone) BETWEEN 1 AND 64);
