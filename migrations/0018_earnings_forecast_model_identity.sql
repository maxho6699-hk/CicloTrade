-- Keep 0016 immutable: SQLite requires a table rebuild to replace its
-- model_version-only uniqueness constraint.  The migration is atomic and
-- recreates the dependent option snapshot table so its foreign key remains
-- bound to the rebuilt forecast table.
DROP TRIGGER IF EXISTS trg_earnings_options_no_update;
DROP TRIGGER IF EXISTS trg_earnings_options_no_delete;
DROP TRIGGER IF EXISTS trg_earnings_forecasts_no_update;
DROP TRIGGER IF EXISTS trg_earnings_forecasts_no_delete;

CREATE TABLE earnings_forecast_snapshots__0018 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    logical_run_key TEXT NOT NULL UNIQUE,
    event_revision_id INTEGER NOT NULL,
    countdown_day INTEGER NOT NULL CHECK (countdown_day BETWEEN 1 AND 7),
    decision_at TEXT NOT NULL,
    available_cutoff_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_artifact_sha256 TEXT NOT NULL CHECK (length(model_artifact_sha256) = 64),
    input_manifest_json TEXT NOT NULL CHECK (json_valid(input_manifest_json)),
    input_manifest_sha256 TEXT NOT NULL CHECK (length(input_manifest_sha256) = 64),
    p_up REAL NOT NULL CHECK (p_up BETWEEN 0 AND 1),
    p_down REAL NOT NULL CHECK (p_down BETWEEN 0 AND 1),
    p_flat REAL NOT NULL CHECK (p_flat BETWEEN 0 AND 1),
    flat_band_pct REAL NOT NULL CHECK (flat_band_pct >= 0),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    calibration_sample_size INTEGER NOT NULL CHECK (calibration_sample_size >= 0),
    reference_price REAL NOT NULL CHECK (reference_price > 0),
    currency TEXT NOT NULL CHECK (currency IN ('USD','CNY')),
    price_p10 REAL NOT NULL CHECK (price_p10 > 0),
    price_p50 REAL NOT NULL CHECK (price_p50 > 0),
    price_p90 REAL NOT NULL CHECK (price_p90 > 0),
    estimated_mfe_pct REAL NOT NULL CHECK (estimated_mfe_pct >= 0),
    estimated_mae_pct REAL NOT NULL CHECK (estimated_mae_pct <= 0),
    simulated_action TEXT NOT NULL CHECK (simulated_action IN (
        'OBSERVE','PAPER_OPEN','PAPER_ADD','PAPER_REDUCE','PAPER_CLOSE',
        'RESEARCH_LONG_CALL','RESEARCH_LONG_PUT',
        'RESEARCH_LONG_STRADDLE','RESEARCH_LONG_STRANGLE'
    )),
    narrative_json TEXT NOT NULL CHECK (json_valid(narrative_json)),
    causal_graph_json TEXT NOT NULL CHECK (json_valid(causal_graph_json)),
    risk_json TEXT NOT NULL CHECK (json_valid(risk_json)),
    publication_state TEXT NOT NULL DEFAULT 'research' CHECK (publication_state = 'research'),
    research_only INTEGER NOT NULL DEFAULT 1 CHECK (research_only = 1),
    execution_eligible INTEGER NOT NULL DEFAULT 0 CHECK (execution_eligible = 0),
    automatic_ordering INTEGER NOT NULL DEFAULT 0 CHECK (automatic_ordering = 0),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    UNIQUE (event_revision_id, countdown_day, model_id, model_version),
    CHECK (abs((p_up + p_down + p_flat) - 1.0) < 0.000000001),
    CHECK (price_p10 <= price_p50 AND price_p50 <= price_p90),
    FOREIGN KEY (event_revision_id) REFERENCES earnings_event_revisions(id)
);

INSERT INTO earnings_forecast_snapshots__0018
SELECT * FROM earnings_forecast_snapshots;

CREATE TABLE earnings_option_research_snapshots__0018 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    forecast_snapshot_id INTEGER NOT NULL,
    structure_type TEXT NOT NULL CHECK (structure_type IN (
        'LONG_CALL','LONG_PUT','LONG_STRADDLE','LONG_STRANGLE'
    )),
    evidence_mode TEXT NOT NULL CHECK (evidence_mode = 'current_snapshot_research_estimate'),
    historical_oos_validated INTEGER NOT NULL DEFAULT 0 CHECK (historical_oos_validated = 0),
    research_only INTEGER NOT NULL DEFAULT 1 CHECK (research_only = 1),
    execution_eligible INTEGER NOT NULL DEFAULT 0 CHECK (execution_eligible = 0),
    automatic_ordering INTEGER NOT NULL DEFAULT 0 CHECK (automatic_ordering = 0),
    contracts_json TEXT NOT NULL CHECK (json_valid(contracts_json)),
    total_premium REAL NOT NULL CHECK (total_premium > 0),
    commission_cost REAL NOT NULL CHECK (commission_cost >= 0),
    spread_cost REAL NOT NULL CHECK (spread_cost >= 0),
    slippage_cost REAL NOT NULL CHECK (slippage_cost >= 0),
    max_loss REAL NOT NULL CHECK (max_loss > 0),
    lower_breakeven REAL,
    upper_breakeven REAL,
    required_move_pct REAL NOT NULL CHECK (required_move_pct >= 0),
    model_expected_move_pct REAL NOT NULL CHECK (model_expected_move_pct >= 0),
    iv_implied_move_pct REAL NOT NULL CHECK (iv_implied_move_pct >= 0),
    probability_outside_breakeven REAL NOT NULL CHECK (probability_outside_breakeven BETWEEN 0 AND 1),
    expected_value_net_costs REAL NOT NULL,
    one_leg_coverage_json TEXT NOT NULL CHECK (json_valid(one_leg_coverage_json)),
    iv_crush_json TEXT NOT NULL CHECK (json_valid(iv_crush_json)),
    decision_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    UNIQUE (forecast_snapshot_id, structure_type),
    FOREIGN KEY (forecast_snapshot_id) REFERENCES earnings_forecast_snapshots__0018(id)
);

-- 0016 did not record when an option projection was ingested.  Mark legacy
-- rows no earlier than the migration time or their sealed forecast so PIT
-- reads cannot reveal them before the dependent research could exist.
INSERT INTO earnings_option_research_snapshots__0018
    (id,idempotency_key,forecast_snapshot_id,structure_type,evidence_mode,
     historical_oos_validated,research_only,execution_eligible,automatic_ordering,
     contracts_json,total_premium,commission_cost,spread_cost,slippage_cost,max_loss,
     lower_breakeven,upper_breakeven,required_move_pct,model_expected_move_pct,
     iv_implied_move_pct,probability_outside_breakeven,expected_value_net_costs,
     one_leg_coverage_json,iv_crush_json,decision_at,recorded_at,payload_sha256)
SELECT option.id,option.idempotency_key,option.forecast_snapshot_id,
       option.structure_type,option.evidence_mode,
       option.historical_oos_validated,option.research_only,
       option.execution_eligible,option.automatic_ordering,
       option.contracts_json,option.total_premium,option.commission_cost,
       option.spread_cost,option.slippage_cost,option.max_loss,
       option.lower_breakeven,option.upper_breakeven,option.required_move_pct,
       option.model_expected_move_pct,option.iv_implied_move_pct,
       option.probability_outside_breakeven,option.expected_value_net_costs,
       option.one_leg_coverage_json,option.iv_crush_json,option.decision_at,
       max(
           strftime('%Y-%m-%dT%H:%M:%fZ','now'),
           forecast.decision_at,
           forecast.recorded_at
       ),option.payload_sha256
FROM earnings_option_research_snapshots option
JOIN earnings_forecast_snapshots forecast ON forecast.id=option.forecast_snapshot_id;

DROP TABLE earnings_option_research_snapshots;
DROP TABLE earnings_forecast_snapshots;
ALTER TABLE earnings_forecast_snapshots__0018 RENAME TO earnings_forecast_snapshots;
ALTER TABLE earnings_option_research_snapshots__0018 RENAME TO earnings_option_research_snapshots;

CREATE INDEX idx_earnings_forecast_event_day
ON earnings_forecast_snapshots(event_revision_id, countdown_day DESC, id DESC);

CREATE INDEX idx_earnings_options_forecast
ON earnings_option_research_snapshots(forecast_snapshot_id, id DESC);

CREATE TRIGGER trg_earnings_forecasts_no_update
BEFORE UPDATE ON earnings_forecast_snapshots BEGIN
    SELECT RAISE(ABORT, 'earnings_forecast_snapshots are append-only');
END;

CREATE TRIGGER trg_earnings_forecasts_no_delete
BEFORE DELETE ON earnings_forecast_snapshots BEGIN
    SELECT RAISE(ABORT, 'earnings_forecast_snapshots are append-only');
END;

CREATE TRIGGER trg_earnings_options_no_update
BEFORE UPDATE ON earnings_option_research_snapshots BEGIN
    SELECT RAISE(ABORT, 'earnings_option_research_snapshots are append-only');
END;

CREATE TRIGGER trg_earnings_options_no_delete
BEFORE DELETE ON earnings_option_research_snapshots BEGIN
    SELECT RAISE(ABORT, 'earnings_option_research_snapshots are append-only');
END;
