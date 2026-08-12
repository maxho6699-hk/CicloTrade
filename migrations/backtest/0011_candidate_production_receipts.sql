-- Durable reservations for the local-only candidate input producer. These
-- receipts do not approve, publish, display, notify, or execute a strategy.
CREATE TABLE IF NOT EXISTS backtest_candidate_production_receipts (
    request_id TEXT PRIMARY KEY CHECK(length(request_id) BETWEEN 8 AND 96),
    request_sha256 TEXT NOT NULL
        CHECK(length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    candidate_id TEXT NOT NULL CHECK(length(candidate_id) BETWEEN 1 AND 128),
    candidate_version TEXT NOT NULL CHECK(length(candidate_version) BETWEEN 1 AND 128),
    budget_day TEXT NOT NULL,
    symbol TEXT NOT NULL CHECK(length(symbol) BETWEEN 1 AND 16),
    template_key TEXT NOT NULL CHECK(length(template_key) BETWEEN 1 AND 128),
    universe_sha256 TEXT NOT NULL
        CHECK(length(universe_sha256)=64 AND universe_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_file TEXT NOT NULL CHECK(length(source_file) BETWEEN 5 AND 128),
    source_sha256 TEXT NOT NULL
        CHECK(length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_bytes INTEGER NOT NULL CHECK(source_bytes > 0),
    request_json TEXT NOT NULL CHECK(json_valid(request_json))
        CHECK(json_extract(request_json,'$.request_id')=request_id)
        CHECK(json_extract(request_json,'$.candidate_spec.candidate_id')=candidate_id)
        CHECK(json_extract(request_json,'$.candidate_spec.candidate_version')=candidate_version)
        CHECK(json_extract(request_json,'$.symbol')=symbol)
        CHECK(json_extract(request_json,'$.template_key')=template_key)
        CHECK(json_extract(request_json,'$.universe_sha256')=universe_sha256)
        CHECK(json_extract(request_json,'$.source_file')=source_file)
        CHECK(json_extract(request_json,'$.source_sha256')=source_sha256)
        CHECK(json_extract(request_json,'$.source_bytes')=source_bytes),
    state TEXT NOT NULL CHECK(state IN ('reserved','delivered')),
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    CHECK((state='reserved' AND delivered_at IS NULL) OR (state='delivered' AND delivered_at IS NOT NULL)),
    UNIQUE(candidate_id,candidate_version)
);

CREATE INDEX IF NOT EXISTS idx_backtest_candidate_production_budget
    ON backtest_candidate_production_receipts(budget_day,created_at);

CREATE TRIGGER IF NOT EXISTS trg_backtest_candidate_production_no_update
BEFORE UPDATE ON backtest_candidate_production_receipts
WHEN NOT (
    OLD.state='reserved' AND NEW.state='delivered'
    AND OLD.request_id=NEW.request_id
    AND OLD.request_sha256=NEW.request_sha256
    AND OLD.candidate_id=NEW.candidate_id
    AND OLD.candidate_version=NEW.candidate_version
    AND OLD.budget_day=NEW.budget_day
    AND OLD.symbol=NEW.symbol
    AND OLD.template_key=NEW.template_key
    AND OLD.universe_sha256=NEW.universe_sha256
    AND OLD.source_file=NEW.source_file
    AND OLD.source_sha256=NEW.source_sha256
    AND OLD.source_bytes=NEW.source_bytes
    AND OLD.request_json=NEW.request_json
    AND OLD.created_at=NEW.created_at
    AND NEW.delivered_at IS NOT NULL
)
BEGIN SELECT RAISE(ABORT, 'candidate production receipts are immutable except reserved delivery'); END;

CREATE TRIGGER IF NOT EXISTS trg_backtest_candidate_production_no_delete
BEFORE DELETE ON backtest_candidate_production_receipts
BEGIN SELECT RAISE(ABORT, 'candidate production receipts are immutable'); END;
