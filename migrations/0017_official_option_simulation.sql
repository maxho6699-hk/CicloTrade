-- Official simulation is deliberately separate from personal orders/trades and
-- the legacy quant journal.  These rows are immutable audit evidence.
CREATE TABLE IF NOT EXISTS official_option_sim_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_key TEXT NOT NULL UNIQUE,
    structure_type TEXT NOT NULL CHECK (structure_type IN (
        'LONG_CALL','LONG_PUT','LONG_STRADDLE','LONG_STRANGLE',
        'CALL_DEBIT_SPREAD','PUT_DEBIT_SPREAD','PROTECTIVE_HEDGE'
    )),
    underlying TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    evidence_hashes_json TEXT NOT NULL CHECK (json_valid(evidence_hashes_json)),
    account_equity REAL NOT NULL CHECK (account_equity > 0),
    max_loss REAL NOT NULL CHECK (max_loss > 0),
    max_account_pct REAL NOT NULL CHECK (max_account_pct > 0 AND max_account_pct <= 3),
    portfolio_risk_before_pct REAL NOT NULL CHECK (portfolio_risk_before_pct >= 0 AND portfolio_risk_before_pct <= 8),
    portfolio_risk_limit_pct REAL NOT NULL CHECK (portfolio_risk_limit_pct > 0 AND portfolio_risk_limit_pct <= 8),
    invalidation_condition TEXT NOT NULL,
    created_event_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (created_event_id) REFERENCES official_option_sim_events(id)
);

CREATE TABLE IF NOT EXISTS official_option_sim_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    external_event_id TEXT NOT NULL UNIQUE,
    position_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'PROPOSED','ACCEPTED','OPENED','MARKED','CLOSING','CLOSED','REJECTED','CANCELLED'
    )),
    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN (
        'proposed','accepted','open','closing','closed','rejected','cancelled'
    )),
    action_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    fencing_epoch INTEGER NOT NULL CHECK (fencing_epoch >= 1),
    cash_flow REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    FOREIGN KEY (position_id) REFERENCES official_option_sim_positions(id)
);

CREATE INDEX IF NOT EXISTS idx_official_option_sim_events_position
ON official_option_sim_events(position_id, id DESC);

CREATE TABLE IF NOT EXISTS official_option_sim_event_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    leg_no INTEGER NOT NULL CHECK (leg_no >= 0),
    contract_key TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    expiry TEXT NOT NULL,
    right TEXT NOT NULL CHECK (right IN ('CALL','PUT')),
    strike REAL NOT NULL CHECK (strike > 0),
    multiplier INTEGER NOT NULL CHECK (multiplier > 0),
    bid REAL NOT NULL CHECK (bid >= 0),
    ask REAL NOT NULL CHECK (ask > 0 AND ask >= bid),
    quote_at TEXT NOT NULL,
    execution_price REAL,
    commission REAL NOT NULL DEFAULT 0 CHECK (commission >= 0),
    UNIQUE(event_id, leg_no),
    UNIQUE(event_id, contract_key),
    FOREIGN KEY (event_id) REFERENCES official_option_sim_events(id)
);

CREATE TABLE IF NOT EXISTS official_option_sim_equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    position_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    equity REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    drawdown REAL NOT NULL CHECK (drawdown >= 0),
    FOREIGN KEY (event_id) REFERENCES official_option_sim_events(id),
    FOREIGN KEY (position_id) REFERENCES official_option_sim_positions(id)
);

CREATE TABLE IF NOT EXISTS official_option_sim_worker_fences (
    worker_id TEXT PRIMARY KEY,
    highest_epoch INTEGER NOT NULL CHECK (highest_epoch >= 1),
    updated_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_official_option_sim_positions_no_update
BEFORE UPDATE ON official_option_sim_positions BEGIN
    SELECT RAISE(ABORT, 'official_option_sim_positions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_option_sim_positions_no_delete
BEFORE DELETE ON official_option_sim_positions BEGIN
    SELECT RAISE(ABORT, 'official_option_sim_positions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_option_sim_events_no_update
BEFORE UPDATE ON official_option_sim_events BEGIN
    SELECT RAISE(ABORT, 'official_option_sim_events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_option_sim_events_no_delete
BEFORE DELETE ON official_option_sim_events BEGIN
    SELECT RAISE(ABORT, 'official_option_sim_events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_option_sim_legs_no_update
BEFORE UPDATE ON official_option_sim_event_legs BEGIN
    SELECT RAISE(ABORT, 'official_option_sim_event_legs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_option_sim_legs_no_delete
BEFORE DELETE ON official_option_sim_event_legs BEGIN
    SELECT RAISE(ABORT, 'official_option_sim_event_legs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_option_sim_equity_no_update
BEFORE UPDATE ON official_option_sim_equity_snapshots BEGIN
    SELECT RAISE(ABORT, 'official_option_sim_equity_snapshots are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_option_sim_equity_no_delete
BEFORE DELETE ON official_option_sim_equity_snapshots BEGIN
    SELECT RAISE(ABORT, 'official_option_sim_equity_snapshots are append-only');
END;
