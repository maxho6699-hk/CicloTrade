CREATE TABLE personal_paper_seasons (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    season_number INTEGER NOT NULL CHECK(season_number > 0),
    state TEXT NOT NULL CHECK(state IN ('active','closed')),
    currency TEXT NOT NULL CHECK(currency = 'USD'),
    initial_cash_minor INTEGER NOT NULL CHECK(initial_cash_minor > 0),
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
    started_at TEXT NOT NULL,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    UNIQUE(user_id,season_number),
    CHECK((state='active' AND closed_at IS NULL) OR state='closed')
);
CREATE UNIQUE INDEX idx_personal_paper_one_active_season
ON personal_paper_seasons(user_id) WHERE state='active';
CREATE TRIGGER trg_personal_paper_first_season_10k
BEFORE INSERT ON personal_paper_seasons
WHEN NEW.season_number=1 AND NEW.initial_cash_minor != 1000000
BEGIN SELECT RAISE(ABORT,'first personal paper season must be USD 10,000'); END;
CREATE TRIGGER trg_personal_paper_season_identity_immutable
BEFORE UPDATE OF id,user_id,season_number,currency,initial_cash_minor,started_at,created_at
ON personal_paper_seasons
BEGIN SELECT RAISE(ABORT,'personal paper season identity is immutable'); END;
CREATE TRIGGER trg_personal_paper_season_no_delete
BEFORE DELETE ON personal_paper_seasons
BEGIN SELECT RAISE(ABORT,'personal paper season cannot be deleted'); END;

CREATE TABLE personal_paper_quote_proofs (
    public_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='q1'),
    nonce TEXT NOT NULL UNIQUE CHECK(length(nonce) BETWEEN 16 AND 60),
    claims_json TEXT NOT NULL,
    signature_sha256 TEXT NOT NULL CHECK(length(signature_sha256)=64),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id)
);
CREATE INDEX idx_personal_paper_quote_owner_expiry
ON personal_paper_quote_proofs(user_id,season_id,expires_at,public_id);
CREATE TRIGGER trg_personal_paper_quote_proofs_owner
BEFORE INSERT ON personal_paper_quote_proofs
WHEN NOT EXISTS (
    SELECT 1 FROM personal_paper_seasons s
    WHERE s.id=NEW.season_id AND s.user_id=NEW.user_id AND s.state='active'
)
BEGIN SELECT RAISE(ABORT,'personal paper quote proof owner mismatch'); END;
CREATE TRIGGER trg_personal_paper_quote_proofs_no_update
BEFORE UPDATE ON personal_paper_quote_proofs
BEGIN SELECT RAISE(ABORT,'personal paper quote proofs are append-only'); END;
CREATE TRIGGER trg_personal_paper_quote_proofs_no_delete
BEFORE DELETE ON personal_paper_quote_proofs
BEGIN SELECT RAISE(ABORT,'personal paper quote proofs are append-only'); END;

CREATE TABLE personal_paper_quote_consumptions (
    proof_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    consumed_at TEXT NOT NULL,
    FOREIGN KEY(proof_id) REFERENCES personal_paper_quote_proofs(public_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id)
);
CREATE INDEX idx_personal_paper_quote_consumption_owner
ON personal_paper_quote_consumptions(user_id,season_id,consumed_at);
CREATE TRIGGER trg_personal_paper_quote_consumptions_owner
BEFORE INSERT ON personal_paper_quote_consumptions
WHEN NOT EXISTS (
    SELECT 1 FROM personal_paper_quote_proofs p
    JOIN personal_paper_seasons s ON s.id=NEW.season_id
    WHERE p.public_id=NEW.proof_id AND p.user_id=NEW.user_id
      AND p.season_id=NEW.season_id AND s.user_id=NEW.user_id AND s.state='active'
)
BEGIN SELECT RAISE(ABORT,'personal paper quote consumption owner mismatch'); END;
CREATE TRIGGER trg_personal_paper_quote_consumptions_no_update
BEFORE UPDATE ON personal_paper_quote_consumptions
BEGIN SELECT RAISE(ABORT,'personal paper quote consumptions are append-only'); END;
CREATE TRIGGER trg_personal_paper_quote_consumptions_no_delete
BEFORE DELETE ON personal_paper_quote_consumptions
BEGIN SELECT RAISE(ABORT,'personal paper quote consumptions are append-only'); END;

CREATE TABLE personal_paper_orders (
    public_id TEXT PRIMARY KEY,
    season_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    market TEXT NOT NULL CHECK(market='US'),
    instrument_type TEXT NOT NULL CHECK(instrument_type='stock'),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY','SELL','SHORT','COVER')),
    order_type TEXT NOT NULL CHECK(order_type IN ('MARKET','LIMIT','STOP','STOP_LIMIT')),
    quantity_micros INTEGER NOT NULL CHECK(quantity_micros > 0),
    limit_price_minor INTEGER CHECK(limit_price_minor > 0),
    stop_price_minor INTEGER CHECK(stop_price_minor > 0),
    time_in_force TEXT NOT NULL CHECK(time_in_force='DAY'),
    quote_proof_id TEXT NOT NULL,
    quote_as_of TEXT NOT NULL,
    account_version INTEGER NOT NULL CHECK(account_version >= 0),
    source_kind TEXT NOT NULL CHECK(source_kind IN ('manual','recommendation','chart','screener')),
    source_reference_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('PENDING','FILLED','CANCELLED','REJECTED')),
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    UNIQUE(season_id,idempotency_key),
    CHECK((order_type='MARKET' AND limit_price_minor IS NULL AND stop_price_minor IS NULL) OR
          (order_type='LIMIT' AND limit_price_minor IS NOT NULL AND stop_price_minor IS NULL) OR
          (order_type='STOP' AND limit_price_minor IS NULL AND stop_price_minor IS NOT NULL) OR
          (order_type='STOP_LIMIT' AND limit_price_minor IS NOT NULL AND stop_price_minor IS NOT NULL))
);
CREATE INDEX idx_personal_paper_orders_season_time
ON personal_paper_orders(season_id,created_at,public_id);

CREATE TABLE personal_paper_order_events (
    public_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    event_type TEXT NOT NULL CHECK(event_type IN ('ACCEPTED','FILLED','CANCELLED','REJECTED')),
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    FOREIGN KEY(order_id) REFERENCES personal_paper_orders(public_id),
    UNIQUE(order_id,sequence)
);

CREATE TABLE personal_paper_fills (
    public_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE,
    season_id TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market='US'),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY','SELL','SHORT','COVER')),
    quantity_micros INTEGER NOT NULL CHECK(quantity_micros > 0),
    price_minor INTEGER NOT NULL CHECK(price_minor > 0),
    commission_minor INTEGER NOT NULL CHECK(commission_minor >= 0),
    filled_at TEXT NOT NULL,
    quote_proof_id TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES personal_paper_orders(public_id),
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id)
);

CREATE TABLE personal_paper_account_events (
    public_id TEXT PRIMARY KEY,
    season_id TEXT NOT NULL,
    related_order_id TEXT,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    event_type TEXT NOT NULL CHECK(event_type IN ('SEASON_OPENED','ORDER_FILLED','ORDER_RESERVED','ORDER_RELEASED','SEASON_CLOSED')),
    market TEXT CHECK(market IS NULL OR market='US'),
    symbol TEXT,
    side TEXT CHECK(side IS NULL OR side IN ('BUY','SELL','SHORT','COVER')),
    cash_delta_minor INTEGER NOT NULL DEFAULT 0,
    reserved_cash_delta_minor INTEGER NOT NULL DEFAULT 0,
    position_delta_micros INTEGER NOT NULL DEFAULT 0,
    reserved_position_delta_micros INTEGER NOT NULL DEFAULT 0,
    realized_pnl_delta_minor INTEGER NOT NULL DEFAULT 0,
    execution_price_minor INTEGER CHECK(execution_price_minor IS NULL OR execution_price_minor > 0),
    commission_minor INTEGER NOT NULL DEFAULT 0 CHECK(commission_minor >= 0),
    mark_bid_minor INTEGER CHECK(mark_bid_minor IS NULL OR mark_bid_minor > 0),
    mark_ask_minor INTEGER CHECK(mark_ask_minor IS NULL OR mark_ask_minor > 0),
    mark_last_minor INTEGER CHECK(mark_last_minor IS NULL OR mark_last_minor > 0),
    quote_as_of TEXT,
    quote_state TEXT CHECK(quote_state IS NULL OR quote_state IN ('fresh','delayed','stale','missing')),
    occurred_at TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id),
    UNIQUE(season_id,sequence),
    CHECK((market IS NULL AND symbol IS NULL AND side IS NULL AND execution_price_minor IS NULL
           AND mark_bid_minor IS NULL AND mark_ask_minor IS NULL AND mark_last_minor IS NULL
           AND quote_as_of IS NULL AND quote_state IS NULL) OR
          (market='US' AND length(symbol) BETWEEN 1 AND 16 AND side IS NOT NULL
           AND mark_bid_minor IS NOT NULL AND mark_ask_minor IS NOT NULL
           AND mark_last_minor IS NOT NULL AND quote_as_of IS NOT NULL
           AND quote_state IS NOT NULL AND mark_ask_minor >= mark_bid_minor))
);
CREATE INDEX idx_personal_paper_account_symbol
ON personal_paper_account_events(season_id,market,symbol,sequence);

CREATE TABLE personal_paper_risk_events (
    public_id TEXT PRIMARY KEY,
    season_id TEXT NOT NULL,
    order_id TEXT,
    code TEXT NOT NULL,
    allowed INTEGER NOT NULL CHECK(allowed IN (0,1)),
    details_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id),
    FOREIGN KEY(order_id) REFERENCES personal_paper_orders(public_id)
);

CREATE TABLE personal_paper_equity_events (
    public_id TEXT PRIMARY KEY,
    season_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    cash_minor INTEGER NOT NULL,
    reserved_cash_minor INTEGER NOT NULL CHECK(reserved_cash_minor >= 0),
    market_value_minor INTEGER NOT NULL,
    realized_pnl_minor INTEGER NOT NULL,
    unrealized_pnl_minor INTEGER NOT NULL,
    total_equity_minor INTEGER NOT NULL,
    quote_state TEXT NOT NULL CHECK(quote_state IN ('fresh','delayed','stale','missing')),
    as_of TEXT NOT NULL,
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id),
    UNIQUE(season_id,sequence),
    CHECK(total_equity_minor = cash_minor + market_value_minor)
);
CREATE TRIGGER trg_personal_paper_equity_balances
BEFORE INSERT ON personal_paper_equity_events
WHEN NEW.total_equity_minor !=
     (SELECT initial_cash_minor FROM personal_paper_seasons WHERE id=NEW.season_id)
     + NEW.realized_pnl_minor + NEW.unrealized_pnl_minor
BEGIN SELECT RAISE(ABORT,'personal paper equity snapshot is unbalanced'); END;

CREATE TRIGGER trg_personal_paper_orders_no_update BEFORE UPDATE ON personal_paper_orders
BEGIN SELECT RAISE(ABORT,'personal paper orders are append-only'); END;
CREATE TRIGGER trg_personal_paper_orders_no_delete BEFORE DELETE ON personal_paper_orders
BEGIN SELECT RAISE(ABORT,'personal paper orders are append-only'); END;
CREATE TRIGGER trg_personal_paper_order_events_no_update BEFORE UPDATE ON personal_paper_order_events
BEGIN SELECT RAISE(ABORT,'personal paper order events are append-only'); END;
CREATE TRIGGER trg_personal_paper_order_events_no_delete BEFORE DELETE ON personal_paper_order_events
BEGIN SELECT RAISE(ABORT,'personal paper order events are append-only'); END;
CREATE TRIGGER trg_personal_paper_fills_no_update BEFORE UPDATE ON personal_paper_fills
BEGIN SELECT RAISE(ABORT,'personal paper fills are append-only'); END;
CREATE TRIGGER trg_personal_paper_fills_no_delete BEFORE DELETE ON personal_paper_fills
BEGIN SELECT RAISE(ABORT,'personal paper fills are append-only'); END;
CREATE TRIGGER trg_personal_paper_account_events_no_update BEFORE UPDATE ON personal_paper_account_events
BEGIN SELECT RAISE(ABORT,'personal paper account events are append-only'); END;
CREATE TRIGGER trg_personal_paper_account_events_no_delete BEFORE DELETE ON personal_paper_account_events
BEGIN SELECT RAISE(ABORT,'personal paper account events are append-only'); END;
CREATE TRIGGER trg_personal_paper_risk_events_no_update BEFORE UPDATE ON personal_paper_risk_events
BEGIN SELECT RAISE(ABORT,'personal paper risk events are append-only'); END;
CREATE TRIGGER trg_personal_paper_risk_events_no_delete BEFORE DELETE ON personal_paper_risk_events
BEGIN SELECT RAISE(ABORT,'personal paper risk events are append-only'); END;
CREATE TRIGGER trg_personal_paper_equity_events_no_update BEFORE UPDATE ON personal_paper_equity_events
BEGIN SELECT RAISE(ABORT,'personal paper equity events are append-only'); END;
CREATE TRIGGER trg_personal_paper_equity_events_no_delete BEFORE DELETE ON personal_paper_equity_events
BEGIN SELECT RAISE(ABORT,'personal paper equity events are append-only'); END;
