CREATE TABLE IF NOT EXISTS user_chart_drawings (
    user_id INTEGER NOT NULL,
    market TEXT NOT NULL CHECK(market IN ('US','CN')),
    symbol TEXT NOT NULL,
    origin_timeframe TEXT NOT NULL,
    cross_timeframe INTEGER NOT NULL DEFAULT 0 CHECK(cross_timeframe IN (0,1)),
    drawing_id TEXT NOT NULL,
    drawing_json TEXT NOT NULL CHECK(json_valid(drawing_json)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, market, symbol, origin_timeframe, cross_timeframe, drawing_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    CHECK(length(symbol) BETWEEN 1 AND 12),
    CHECK(length(origin_timeframe) BETWEEN 1 AND 16),
    CHECK(length(drawing_id) BETWEEN 36 AND 36)
);

CREATE INDEX IF NOT EXISTS idx_user_chart_drawings_scope
    ON user_chart_drawings(user_id, market, symbol, cross_timeframe, origin_timeframe, deleted_at);

CREATE INDEX IF NOT EXISTS idx_user_chart_drawings_tombstones
    ON user_chart_drawings(user_id, deleted_at, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_chart_drawings_symbol_tombstones
    ON user_chart_drawings(user_id, market, symbol, deleted_at, updated_at DESC);
