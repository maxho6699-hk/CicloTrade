CREATE TABLE IF NOT EXISTS referral_profiles (
    user_id INTEGER PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    invite_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    disabled_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id),
    CHECK(length(public_id)=27),
    CHECK(length(invite_code) BETWEEN 20 AND 64)
);

CREATE TABLE IF NOT EXISTS referral_visit_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_user_id INTEGER NOT NULL,
    day_bucket TEXT NOT NULL,
    fingerprint_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (profile_user_id) REFERENCES referral_profiles(user_id),
    UNIQUE(profile_user_id,day_bucket,fingerprint_hash),
    CHECK(length(fingerprint_hash)=64)
);

CREATE INDEX IF NOT EXISTS idx_referral_visit_daily_window
ON referral_visit_daily(profile_user_id,day_bucket);

CREATE TABLE IF NOT EXISTS referral_visit_rate_limits (
    rate_key_hash TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL CHECK(attempts >= 1),
    window_started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    CHECK(length(rate_key_hash)=64)
);

CREATE INDEX IF NOT EXISTS idx_referral_visit_rate_expiry
ON referral_visit_rate_limits(expires_at);

CREATE TABLE IF NOT EXISTS referral_attributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    referrer_user_id INTEGER NOT NULL,
    referred_user_id INTEGER NOT NULL UNIQUE,
    invite_code_snapshot TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('web','telegram','legacy')),
    attributed_at TEXT NOT NULL,
    FOREIGN KEY (referrer_user_id) REFERENCES users(id),
    FOREIGN KEY (referred_user_id) REFERENCES users(id),
    CHECK(referrer_user_id <> referred_user_id)
);

CREATE TRIGGER IF NOT EXISTS trg_referral_attribution_no_cycle
BEFORE INSERT ON referral_attributions
WHEN EXISTS (
    WITH RECURSIVE ancestors(user_id) AS (
        SELECT referrer_user_id FROM referral_attributions
        WHERE referred_user_id=NEW.referrer_user_id
        UNION
        SELECT a.referrer_user_id FROM referral_attributions a
        JOIN ancestors x ON a.referred_user_id=x.user_id
    )
    SELECT 1 FROM ancestors WHERE user_id=NEW.referred_user_id
)
BEGIN SELECT RAISE(ABORT,'referral attribution cycle'); END;

INSERT OR IGNORE INTO referral_attributions
    (public_id,referrer_user_id,referred_user_id,invite_code_snapshot,source,attributed_at)
SELECT 'RFR' || printf('%024X',id),referrer_id,referee_id,
       'TAI' || printf('%08d',referrer_id),'legacy',created_at
FROM referrals
WHERE referrer_id <> referee_id;

CREATE INDEX IF NOT EXISTS idx_referral_attributions_referrer
ON referral_attributions(referrer_user_id,attributed_at DESC,id DESC);

CREATE TABLE IF NOT EXISTS referral_commissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    recharge_public_id TEXT NOT NULL UNIQUE,
    attribution_id INTEGER NOT NULL,
    referrer_user_id INTEGER NOT NULL,
    referred_user_id INTEGER NOT NULL,
    source_order_no TEXT NOT NULL UNIQUE,
    settlement_sequence INTEGER NOT NULL CHECK(settlement_sequence >= 1),
    order_kind TEXT NOT NULL CHECK(order_kind IN ('initial_purchase','renewal','upgrade')),
    gross_amount_minor INTEGER NOT NULL CHECK(gross_amount_minor > 0),
    reversed_amount_minor INTEGER NOT NULL DEFAULT 0 CHECK(reversed_amount_minor >= 0),
    rate_bps INTEGER NOT NULL CHECK(rate_bps BETWEEN 0 AND 10000),
    commission_amount_minor INTEGER NOT NULL CHECK(commission_amount_minor >= 0),
    clawed_back_minor INTEGER NOT NULL DEFAULT 0 CHECK(clawed_back_minor >= 0),
    currency TEXT NOT NULL CHECK(currency='HKD'),
    policy_version TEXT NOT NULL,
    settled_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (attribution_id) REFERENCES referral_attributions(id),
    FOREIGN KEY (referrer_user_id) REFERENCES users(id),
    FOREIGN KEY (referred_user_id) REFERENCES users(id),
    FOREIGN KEY (source_order_no) REFERENCES subscription_orders(order_no),
    CHECK(reversed_amount_minor <= gross_amount_minor),
    CHECK(clawed_back_minor <= commission_amount_minor)
);

CREATE INDEX IF NOT EXISTS idx_referral_commissions_referrer
ON referral_commissions(referrer_user_id,settled_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_referral_commissions_due
ON referral_commissions(available_at,id);

CREATE TABLE IF NOT EXISTS referral_ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    bucket TEXT NOT NULL CHECK(bucket IN ('pending','available','reserved','paid')),
    amount_minor INTEGER NOT NULL CHECK(amount_minor <> 0),
    currency TEXT NOT NULL CHECK(currency='HKD'),
    entry_type TEXT NOT NULL,
    group_key TEXT NOT NULL,
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_referral_ledger_user
ON referral_ledger_entries(user_id,currency,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_referral_ledger_reference
ON referral_ledger_entries(reference_type,reference_id,bucket,id);

CREATE TABLE IF NOT EXISTS referral_withdrawal_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    currency TEXT NOT NULL CHECK(currency='HKD'),
    status TEXT NOT NULL CHECK(status IN ('submitted','approved','rejected','paid','system_cancelled')),
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    rejection_reason TEXT,
    approved_by INTEGER,
    approved_at TEXT,
    cancelled_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (reviewed_by) REFERENCES users(id),
    FOREIGN KEY (approved_by) REFERENCES users(id),
    UNIQUE(user_id,idempotency_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_withdrawal_open_user
ON referral_withdrawal_requests(user_id)
WHERE status IN ('submitted','approved');
CREATE INDEX IF NOT EXISTS idx_referral_withdrawal_queue
ON referral_withdrawal_requests(status,submitted_at,id);

CREATE TABLE IF NOT EXISTS referral_payout_confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    withdrawal_id INTEGER NOT NULL UNIQUE,
    payout_method TEXT NOT NULL CHECK(payout_method IN ('fps','bank','other')),
    payout_reference TEXT NOT NULL UNIQUE,
    confirmed_by INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL,
    FOREIGN KEY (withdrawal_id) REFERENCES referral_withdrawal_requests(id),
    FOREIGN KEY (confirmed_by) REFERENCES users(id),
    CHECK(length(payout_reference) BETWEEN 6 AND 64)
);

CREATE TABLE IF NOT EXISTS referral_reversal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    event_key TEXT NOT NULL UNIQUE,
    source_order_no TEXT NOT NULL,
    reversal_kind TEXT NOT NULL CHECK(reversal_kind IN ('refund','chargeback','dispute','other')),
    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (source_order_no) REFERENCES subscription_orders(order_no)
);

CREATE TABLE IF NOT EXISTS referral_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    actor_user_id INTEGER,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user','admin','system')),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_public_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (actor_user_id) REFERENCES users(id)
);

CREATE TRIGGER IF NOT EXISTS trg_referral_profile_identity_immutable
BEFORE UPDATE OF user_id,public_id,invite_code,created_at ON referral_profiles
BEGIN SELECT RAISE(ABORT,'referral profile identity is immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_referral_profile_no_delete
BEFORE DELETE ON referral_profiles
BEGIN SELECT RAISE(ABORT,'referral profile is immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_referral_attribution_immutable
BEFORE UPDATE ON referral_attributions
BEGIN SELECT RAISE(ABORT,'referral attribution is immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_referral_attribution_no_delete
BEFORE DELETE ON referral_attributions
BEGIN SELECT RAISE(ABORT,'referral attribution is immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_referral_ledger_immutable_update
BEFORE UPDATE ON referral_ledger_entries
BEGIN SELECT RAISE(ABORT,'referral ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_referral_ledger_immutable_delete
BEFORE DELETE ON referral_ledger_entries
BEGIN SELECT RAISE(ABORT,'referral ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_referral_commission_business_immutable
BEFORE UPDATE OF public_id,recharge_public_id,attribution_id,referrer_user_id,referred_user_id,
                 source_order_no,settlement_sequence,order_kind,gross_amount_minor,rate_bps,
                 commission_amount_minor,currency,policy_version,settled_at,available_at,created_at
ON referral_commissions
BEGIN SELECT RAISE(ABORT,'referral commission terms are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_referral_withdrawal_transition
BEFORE UPDATE OF status ON referral_withdrawal_requests
WHEN NOT (
    (OLD.status='submitted' AND NEW.status IN ('approved','rejected','system_cancelled')) OR
    (OLD.status='approved' AND NEW.status IN ('paid','rejected','system_cancelled'))
)
BEGIN SELECT RAISE(ABORT,'invalid referral withdrawal transition'); END;

INSERT OR IGNORE INTO platform_controls(control_key,control_value,updated_at)
VALUES ('referral_cash_enabled','1',CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO platform_controls(control_key,control_value,updated_at)
VALUES ('referral_cash_cutover_at',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO platform_controls(control_key,control_value,updated_at)
VALUES ('referral_first_rate_bps','2000',CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO platform_controls(control_key,control_value,updated_at)
VALUES ('referral_repeat_rate_bps','1000',CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO platform_controls(control_key,control_value,updated_at)
VALUES ('referral_upgrade_rate_bps','1000',CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO platform_controls(control_key,control_value,updated_at)
VALUES ('referral_hold_days','14',CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO platform_controls(control_key,control_value,updated_at)
VALUES ('referral_min_withdraw_minor','10000',CURRENT_TIMESTAMP);
