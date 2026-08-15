-- Additive read indexes for bounded expanded-research website projections.
-- Safe for databases where 0013_expanded_research_invalidations.sql is already applied.
CREATE INDEX IF NOT EXISTS idx_expanded_research_latest_symbol
ON expanded_research_receipts(symbol, received_at DESC, receipt_key DESC);
CREATE INDEX IF NOT EXISTS idx_expanded_research_dataset_cycle
ON expanded_research_receipts(json_extract(payload_json, '$.dataset_end') DESC, received_at DESC, receipt_key DESC);
