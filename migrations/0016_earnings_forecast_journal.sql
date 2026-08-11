CREATE TABLE IF NOT EXISTS earnings_event_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_key TEXT NOT NULL,
    revision_no INTEGER NOT NULL CHECK (revision_no > 0),
    market TEXT NOT NULL CHECK (market IN ('US','CN')),
    symbol TEXT NOT NULL,
    fiscal_period TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    exchange_timezone TEXT NOT NULL,
    timing TEXT NOT NULL CHECK (timing IN ('BMO','AMC','DURING','UNKNOWN')),
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED','RESCHEDULED','CANCELLED')),
    source TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    journal_ingested_at TEXT NOT NULL,
    journal_receipt_sha256 TEXT NOT NULL CHECK (length(journal_receipt_sha256) = 64),
    supersedes_revision_id INTEGER,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    UNIQUE (event_key, revision_no),
    UNIQUE (source, source_event_id, revision_no),
    FOREIGN KEY (supersedes_revision_id) REFERENCES earnings_event_revisions(id)
);

CREATE INDEX IF NOT EXISTS idx_earnings_event_current
ON earnings_event_revisions(event_key, revision_no DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_earnings_event_window
ON earnings_event_revisions(status, scheduled_at, market, symbol);

CREATE TABLE IF NOT EXISTS earnings_forecast_snapshots (
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
    UNIQUE (event_revision_id, countdown_day, model_version),
    CHECK (abs((p_up + p_down + p_flat) - 1.0) < 0.000000001),
    CHECK (price_p10 <= price_p50 AND price_p50 <= price_p90),
    FOREIGN KEY (event_revision_id) REFERENCES earnings_event_revisions(id)
);

CREATE INDEX IF NOT EXISTS idx_earnings_forecast_event_day
ON earnings_forecast_snapshots(event_revision_id, countdown_day DESC, id DESC);

CREATE TABLE IF NOT EXISTS earnings_option_research_snapshots (
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
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    UNIQUE (forecast_snapshot_id, structure_type),
    FOREIGN KEY (forecast_snapshot_id) REFERENCES earnings_forecast_snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_earnings_options_forecast
ON earnings_option_research_snapshots(forecast_snapshot_id, id DESC);

CREATE TABLE IF NOT EXISTS earnings_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_revision_id INTEGER NOT NULL,
    checkpoint TEXT NOT NULL CHECK (checkpoint IN (
        'AFTER_HOURS','NEXT_CLOSE','D3_CLOSE','D5_CLOSE'
    )),
    baseline_price REAL NOT NULL CHECK (baseline_price > 0),
    observed_price REAL NOT NULL CHECK (observed_price > 0),
    return_pct REAL NOT NULL,
    mfe_pct REAL NOT NULL CHECK (mfe_pct >= 0),
    mae_pct REAL NOT NULL CHECK (mae_pct <= 0),
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL,
    session_close_at TEXT,
    calendar_artifact_sha256 TEXT CHECK (
        calendar_artifact_sha256 IS NULL OR length(calendar_artifact_sha256) = 64
    ),
    session_validation_receipt_sha256 TEXT CHECK (
        session_validation_receipt_sha256 IS NULL
        OR length(session_validation_receipt_sha256) = 64
    ),
    supersedes_outcome_id INTEGER,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    CHECK (
        checkpoint NOT IN ('D3_CLOSE','D5_CLOSE')
        OR (
            session_close_at IS NOT NULL
            AND calendar_artifact_sha256 IS NOT NULL
            AND session_validation_receipt_sha256 IS NOT NULL
        )
    ),
    FOREIGN KEY (event_revision_id) REFERENCES earnings_event_revisions(id),
    FOREIGN KEY (supersedes_outcome_id) REFERENCES earnings_outcomes(id)
);

CREATE INDEX IF NOT EXISTS idx_earnings_outcome_current
ON earnings_outcomes(event_revision_id, checkpoint, id DESC);

CREATE TABLE IF NOT EXISTS earnings_postmortems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_revision_id INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('PRELIMINARY','FINAL','CORRECTION')),
    completed_at TEXT NOT NULL,
    forecast_snapshot_set_sha256 TEXT NOT NULL CHECK (length(forecast_snapshot_set_sha256) = 64),
    outcome_set_sha256 TEXT NOT NULL CHECK (length(outcome_set_sha256) = 64),
    direction_correct INTEGER NOT NULL CHECK (direction_correct IN (0,1)),
    interval_covered INTEGER NOT NULL CHECK (interval_covered IN (0,1)),
    paper_pnl_net REAL NOT NULL,
    paper_max_drawdown REAL NOT NULL CHECK (paper_max_drawdown >= 0),
    analysis_json TEXT NOT NULL CHECK (json_valid(analysis_json)),
    candidate_ref TEXT,
    supersedes_postmortem_id INTEGER,
    publication_state TEXT NOT NULL DEFAULT 'research' CHECK (publication_state = 'research'),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    FOREIGN KEY (event_revision_id) REFERENCES earnings_event_revisions(id),
    FOREIGN KEY (supersedes_postmortem_id) REFERENCES earnings_postmortems(id)
);

CREATE INDEX IF NOT EXISTS idx_earnings_postmortem_event
ON earnings_postmortems(event_revision_id, stage, id DESC);

CREATE TRIGGER IF NOT EXISTS trg_earnings_event_identity
BEFORE INSERT ON earnings_event_revisions BEGIN
    SELECT CASE WHEN NEW.event_key != NEW.market || ':' || NEW.symbol || ':' || NEW.fiscal_period
        THEN RAISE(ABORT, 'earnings event_key identity mismatch') END;
    SELECT CASE WHEN NEW.supersedes_revision_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM earnings_event_revisions previous
        WHERE previous.id=NEW.supersedes_revision_id AND (
            previous.event_key != NEW.event_key
            OR previous.market != NEW.market
            OR previous.symbol != NEW.symbol
            OR previous.fiscal_period != NEW.fiscal_period
            OR previous.exchange_timezone != NEW.exchange_timezone
            OR previous.source != NEW.source
            OR previous.source_event_id != NEW.source_event_id
        )
    ) THEN RAISE(ABORT, 'earnings event revision identity changed') END;
    SELECT CASE WHEN NEW.supersedes_revision_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM earnings_event_revisions previous
        WHERE previous.id=NEW.supersedes_revision_id AND (
            NEW.available_at < previous.available_at
            OR NEW.recorded_at < previous.recorded_at
            OR NEW.journal_ingested_at < previous.journal_ingested_at
        )
    ) THEN RAISE(ABORT, 'earnings event revision chronology regressed') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_events_no_update
BEFORE UPDATE ON earnings_event_revisions BEGIN
    SELECT RAISE(ABORT, 'earnings_event_revisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_events_no_delete
BEFORE DELETE ON earnings_event_revisions BEGIN
    SELECT RAISE(ABORT, 'earnings_event_revisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_forecasts_no_update
BEFORE UPDATE ON earnings_forecast_snapshots BEGIN
    SELECT RAISE(ABORT, 'earnings_forecast_snapshots are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_forecasts_no_delete
BEFORE DELETE ON earnings_forecast_snapshots BEGIN
    SELECT RAISE(ABORT, 'earnings_forecast_snapshots are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_options_no_update
BEFORE UPDATE ON earnings_option_research_snapshots BEGIN
    SELECT RAISE(ABORT, 'earnings_option_research_snapshots are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_options_no_delete
BEFORE DELETE ON earnings_option_research_snapshots BEGIN
    SELECT RAISE(ABORT, 'earnings_option_research_snapshots are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_outcomes_no_update
BEFORE UPDATE ON earnings_outcomes BEGIN
    SELECT RAISE(ABORT, 'earnings_outcomes are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_outcomes_no_delete
BEFORE DELETE ON earnings_outcomes BEGIN
    SELECT RAISE(ABORT, 'earnings_outcomes are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_postmortems_no_update
BEFORE UPDATE ON earnings_postmortems BEGIN
    SELECT RAISE(ABORT, 'earnings_postmortems are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_earnings_postmortems_no_delete
BEFORE DELETE ON earnings_postmortems BEGIN
    SELECT RAISE(ABORT, 'earnings_postmortems are append-only');
END;
