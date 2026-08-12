ALTER TABLE price_alerts
ADD COLUMN market TEXT NOT NULL DEFAULT 'US' CHECK(market IN ('US','CN'));

UPDATE price_alerts
SET market = CASE
    WHEN length(trim(symbol)) = 6
         AND trim(symbol) GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    THEN 'CN'
    ELSE 'US'
END;

CREATE INDEX IF NOT EXISTS idx_alerts_user_market_symbol
ON price_alerts(user_id, market, symbol, is_active);
