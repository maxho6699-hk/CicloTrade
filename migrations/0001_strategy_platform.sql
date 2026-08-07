CREATE TABLE IF NOT EXISTS strategy_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    family TEXT NOT NULL CHECK(family IN ('option','equity')),
    engine_key TEXT NOT NULL,
    scenario TEXT NOT NULL,
    description TEXT NOT NULL,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','high')),
    parameters_json TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    example_metrics_json TEXT NOT NULL DEFAULT '{}',
    is_core INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS strategy_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    eval_date TEXT NOT NULL,
    total_return REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    sharpe_ratio REAL NOT NULL,
    profit_loss_ratio REAL NOT NULL,
    consecutive_losses INTEGER NOT NULL,
    weighted_score REAL NOT NULL,
    rank_position INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK(lifecycle_status IN ('active','top3','watch','retire_pending')),
    created_at TEXT NOT NULL,
    UNIQUE(strategy_id, eval_date),
    FOREIGN KEY (strategy_id) REFERENCES strategy_definitions(id)
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY,
    backtest_frequency REAL NOT NULL DEFAULT 0,
    preferred_strategy TEXT NOT NULL DEFAULT '',
    average_holding_days REAL NOT NULL DEFAULT 0,
    preferred_win_rate REAL NOT NULL DEFAULT 0,
    tags_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS signal_import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    import_type TEXT NOT NULL CHECK(import_type IN ('csv','code','api')),
    filename TEXT,
    status TEXT NOT NULL CHECK(status IN ('validated','quarantined','failed')),
    row_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    report_json TEXT NOT NULL DEFAULT '{}',
    source_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(user_id, import_type, source_hash),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS imported_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('buy','sell','hold')),
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    timestamp TEXT NOT NULL,
    strategy TEXT NOT NULL,
    confidence REAL,
    disclaimer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, signal_id),
    FOREIGN KEY (job_id) REFERENCES signal_import_jobs(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS strategy_code_submissions (
    job_id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    syntax_valid INTEGER NOT NULL DEFAULT 0,
    sandbox_status TEXT NOT NULL DEFAULT 'not_configured',
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES signal_import_jobs(id)
);

CREATE TABLE IF NOT EXISTS strategy_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    description TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    generated_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('generated','backtested','failed')),
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS saved_strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    strategy_key TEXT,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('template','generated','imported')),
    config_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (strategy_key) REFERENCES strategy_definitions(strategy_key)
);

CREATE TABLE IF NOT EXISTS saved_strategy_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    saved_strategy_id INTEGER NOT NULL,
    eval_date TEXT NOT NULL,
    return_30d REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    annual_return REAL NOT NULL,
    sharpe_ratio REAL NOT NULL,
    win_rate REAL NOT NULL,
    equity_curve_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(saved_strategy_id, eval_date),
    FOREIGN KEY (saved_strategy_id) REFERENCES saved_strategies(id)
);

CREATE TABLE IF NOT EXISTS strategy_shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    saved_strategy_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','disabled')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (saved_strategy_id) REFERENCES saved_strategies(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_strategy_definitions_active
    ON strategy_definitions(is_active, family, name);
CREATE INDEX IF NOT EXISTS idx_strategy_scores_latest
    ON strategy_scores(eval_date, rank_position);
CREATE INDEX IF NOT EXISTS idx_import_jobs_user_time
    ON signal_import_jobs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_imported_signals_user_time
    ON imported_signals(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_strategy_generations_user_time
    ON strategy_generations(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_saved_strategies_user
    ON saved_strategies(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_saved_strategy_performance_timeline
    ON saved_strategy_performance(saved_strategy_id, eval_date);

INSERT INTO roadmap_items (quarter,name,status,sort_order,description,updated_at,created_at)
SELECT '待定','策略分享功能（規劃中）','planning',900,
       '用戶可分享、收藏策略；待社群規模與審核機制成熟後開放。',datetime('now'),datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM roadmap_items WHERE name IN ('策略分享功能（規劃中）','策略分享功能（规划中）'));
