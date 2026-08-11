-- Bounded, stable browser read projections over the isolated shadow ledger.
CREATE INDEX IF NOT EXISTS idx_system_cycle_research_receipts_read_order
    ON system_cycle_research_receipts(
        json_extract(payload_json, '$.evaluated_at') DESC,
        received_at DESC,
        receipt_key DESC
    );

CREATE INDEX IF NOT EXISTS idx_system_cycle_research_heartbeats_read_order
    ON system_cycle_research_heartbeats(heartbeat_at DESC, received_at DESC, heartbeat_key DESC);
