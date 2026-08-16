ALTER TABLE earnings_postmortems
ADD COLUMN paper_performance_state TEXT NOT NULL DEFAULT 'unavailable'
CHECK (paper_performance_state IN ('unavailable'));

ALTER TABLE earnings_postmortems
ADD COLUMN paper_pnl_net_v2 REAL;

ALTER TABLE earnings_postmortems
ADD COLUMN paper_max_drawdown_v2 REAL
CHECK (paper_max_drawdown_v2 IS NULL OR paper_max_drawdown_v2 >= 0);

ALTER TABLE earnings_postmortems
ADD COLUMN paper_ledger_snapshot_sha256 TEXT
CHECK (paper_ledger_snapshot_sha256 IS NULL OR length(paper_ledger_snapshot_sha256) = 64);
