-- Canonical dedicated-queue replacement for the retired product-database
-- draft 0014. Apply only after backtest/0001; the website migration ledger
-- must not record this Worker migration.
ALTER TABLE backtest_jobs ADD COLUMN attempt_deadline_at TEXT;
ALTER TABLE backtest_job_attempts ADD COLUMN attempt_deadline_at TEXT;
