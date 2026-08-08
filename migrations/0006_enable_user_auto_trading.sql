INSERT INTO platform_controls
    (control_key,control_value,updated_by,updated_at)
VALUES
    ('user_auto_trading_enabled','1',NULL,datetime('now'))
ON CONFLICT(control_key) DO UPDATE SET
    control_value='1',
    updated_at=datetime('now');
