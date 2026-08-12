CREATE TABLE IF NOT EXISTS official_paper_events_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_key TEXT NOT NULL,
    source TEXT NOT NULL,
    external_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('signal','correction','reversal')),
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    corrects_event_id INTEGER,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    leg_count INTEGER NOT NULL CHECK(leg_count >= 0),
    metadata_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    UNIQUE(source, external_event_id),
    CHECK(event_type != 'reversal' OR leg_count = 0),
    CHECK(event_type != 'correction' OR leg_count > 0),
    CHECK((event_type = 'signal' AND corrects_event_id IS NULL) OR
          (event_type != 'signal' AND corrects_event_id IS NOT NULL)),
    FOREIGN KEY (corrects_event_id) REFERENCES official_paper_events_v2(id)
);

CREATE TABLE IF NOT EXISTS official_paper_event_legs_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    leg_no INTEGER NOT NULL CHECK(leg_no >= 0),
    market TEXT NOT NULL CHECK(market IN ('US','HK','CN')),
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','option')),
    instrument_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    currency TEXT NOT NULL CHECK(currency IN ('USD','HKD','CNY')),
    option_expiry TEXT,
    option_right TEXT CHECK(option_right IS NULL OR option_right IN ('CALL','PUT')),
    option_strike REAL,
    target_quantity REAL NOT NULL,
    quantity_delta REAL NOT NULL CHECK(quantity_delta != 0),
    price REAL NOT NULL CHECK(price > 0),
    multiplier REAL NOT NULL CHECK(multiplier > 0),
    commission REAL NOT NULL CHECK(commission >= 0),
    UNIQUE(event_id, leg_no),
    UNIQUE(event_id, instrument_key),
    CHECK((market = 'US' AND currency = 'USD') OR
          (market = 'HK' AND currency = 'HKD') OR
          (market = 'CN' AND currency = 'CNY')),
    CHECK((instrument_type = 'stock' AND option_expiry IS NULL AND option_right IS NULL
           AND option_strike IS NULL AND multiplier = 1) OR
          (instrument_type = 'option' AND market = 'US' AND option_expiry IS NOT NULL
           AND option_right IS NOT NULL AND option_strike > 0)),
    FOREIGN KEY (event_id) REFERENCES official_paper_events_v2(id)
);

CREATE TABLE IF NOT EXISTS official_paper_equity_snapshots_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_key TEXT NOT NULL,
    source TEXT NOT NULL,
    external_snapshot_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market IN ('US','HK','CN')),
    currency TEXT NOT NULL CHECK(currency IN ('USD','HKD','CNY')),
    initial_cash REAL NOT NULL CHECK(ABS(initial_cash - 10000) < 0.001),
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    total_equity REAL NOT NULL,
    total_pnl REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    UNIQUE(source,external_snapshot_id,currency),
    CHECK((market = 'US' AND currency = 'USD') OR
          (market = 'HK' AND currency = 'HKD') OR
          (market = 'CN' AND currency = 'CNY')),
    CHECK(ABS((cash + market_value) - total_equity) < 0.01),
    CHECK(ABS((realized_pnl + unrealized_pnl) - total_pnl) < 0.01),
    CHECK(ABS((initial_cash + total_pnl) - total_equity) < 0.01)
);

CREATE INDEX IF NOT EXISTS idx_official_paper_events_v2_ledger
    ON official_paper_events_v2(ledger_key, id);
CREATE INDEX IF NOT EXISTS idx_official_paper_legs_v2_instrument
    ON official_paper_event_legs_v2(instrument_key, event_id);
CREATE INDEX IF NOT EXISTS idx_official_paper_equity_v2_timeline
    ON official_paper_equity_snapshots_v2(ledger_key, market, captured_at, id);

CREATE TRIGGER IF NOT EXISTS trg_official_paper_events_v2_no_update
BEFORE UPDATE ON official_paper_events_v2 BEGIN
    SELECT RAISE(ABORT, 'official_paper_events_v2 are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_paper_events_v2_no_delete
BEFORE DELETE ON official_paper_events_v2 BEGIN
    SELECT RAISE(ABORT, 'official_paper_events_v2 are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_paper_legs_v2_no_update
BEFORE UPDATE ON official_paper_event_legs_v2 BEGIN
    SELECT RAISE(ABORT, 'official_paper_event_legs_v2 are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_paper_legs_v2_no_delete
BEFORE DELETE ON official_paper_event_legs_v2 BEGIN
    SELECT RAISE(ABORT, 'official_paper_event_legs_v2 are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_paper_legs_v2_bounded_insert
BEFORE INSERT ON official_paper_event_legs_v2
WHEN NEW.leg_no >= COALESCE((SELECT leg_count FROM official_paper_events_v2 WHERE id=NEW.event_id), 0)
BEGIN
    SELECT RAISE(ABORT, 'official paper v2 event leg exceeds declared leg_count');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_paper_equity_v2_no_update
BEFORE UPDATE ON official_paper_equity_snapshots_v2 BEGIN
    SELECT RAISE(ABORT, 'official_paper_equity_snapshots_v2 are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_official_paper_equity_v2_no_delete
BEFORE DELETE ON official_paper_equity_snapshots_v2 BEGIN
    SELECT RAISE(ABORT, 'official_paper_equity_snapshots_v2 are append-only');
END;

INSERT OR IGNORE INTO official_paper_equity_snapshots_v2
    (ledger_key,source,external_snapshot_id,captured_at,market,currency,initial_cash,
     cash,market_value,realized_pnl,unrealized_pnl,total_equity,total_pnl,recorded_at,payload_hash)
VALUES
    ('tradeai-official-paper-v2','ciclotrade-official-paper-v2','genesis',
     '1970-01-01T00:00:00.000000+00:00','US','USD',10000,10000,0,0,0,10000,0,
     '1970-01-01T00:00:00.000000+00:00','78f3165c6f83d5c7886fe2315f3f3c60c1623c91cd61744e8777e9e7ad2b97b6'),
    ('tradeai-official-paper-v2','ciclotrade-official-paper-v2','genesis',
     '1970-01-01T00:00:00.000000+00:00','HK','HKD',10000,10000,0,0,0,10000,0,
     '1970-01-01T00:00:00.000000+00:00','afb0087605c703cd32777246d86b5d79c22f04d7ab83131d576b9272e3813fcf'),
    ('tradeai-official-paper-v2','ciclotrade-official-paper-v2','genesis',
     '1970-01-01T00:00:00.000000+00:00','CN','CNY',10000,10000,0,0,0,10000,0,
     '1970-01-01T00:00:00.000000+00:00','51ac821fe09efd77ab49e3a09750b27d7da23b7bb662723ddc2d8745a511921d');
