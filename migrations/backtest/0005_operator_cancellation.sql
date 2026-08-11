-- Auditable cancellation source and reason on the canonical queue row.
ALTER TABLE backtest_jobs ADD COLUMN cancel_source TEXT
    CHECK(cancel_source IS NULL OR length(cancel_source) BETWEEN 1 AND 80);
ALTER TABLE backtest_jobs ADD COLUMN cancel_reason TEXT
    CHECK(cancel_reason IS NULL OR length(cancel_reason) BETWEEN 1 AND 500);
