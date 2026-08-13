ALTER TABLE subscription_orders ADD COLUMN referral_bonus_policy_snapshot TEXT;
ALTER TABLE subscription_orders ADD COLUMN refunded_minor INTEGER NOT NULL DEFAULT 0 CHECK(refunded_minor >= 0);
ALTER TABLE subscription_orders ADD COLUMN promotion_snapshot_sha256 TEXT;
ALTER TABLE subscription_orders ADD COLUMN referral_attribution_id_snapshot INTEGER;
ALTER TABLE subscription_orders ADD COLUMN referral_referrer_user_id_snapshot INTEGER;
ALTER TABLE subscription_orders ADD COLUMN referral_referred_user_id_snapshot INTEGER;
ALTER TABLE referral_withdrawal_requests ADD COLUMN enhanced_review_required INTEGER NOT NULL DEFAULT 0 CHECK(enhanced_review_required IN (0,1));

CREATE TRIGGER trg_subscription_orders_promotion_snapshot_hash
BEFORE INSERT ON subscription_orders
WHEN NEW.referral_policy_version IS NOT NULL AND (
    NEW.promotion_snapshot_sha256 IS NULL OR length(NEW.promotion_snapshot_sha256)<>64 OR
    (NEW.referral_attribution_id_snapshot IS NULL AND (
        NEW.referral_referrer_user_id_snapshot IS NOT NULL OR NEW.referral_referred_user_id_snapshot IS NOT NULL
    )) OR
    (NEW.referral_attribution_id_snapshot IS NOT NULL AND (
        NEW.referral_referrer_user_id_snapshot IS NULL OR NEW.referral_referred_user_id_snapshot IS NULL
    )) OR
    (NEW.referral_eligible_snapshot=1 AND NEW.referral_attribution_id_snapshot IS NULL)
)
BEGIN SELECT RAISE(ABORT,'promotion snapshot hash required'); END;
CREATE TRIGGER trg_subscription_orders_promotion_snapshot_immutable
BEFORE UPDATE OF user_id,order_no,plan_type,billing_cycle,currency,list_price_minor,coupon_discount_minor,referral_discount_minor,final_amount_minor,coupon_code_snapshot,coupon_version_snapshot,referral_policy_version,referral_eligible_snapshot,referral_commission_rate_bps_snapshot,referral_commission_cap_minor_snapshot,referral_hold_days_snapshot,referral_bonus_policy_snapshot,promotion_snapshot_sha256,referral_attribution_id_snapshot,referral_referrer_user_id_snapshot,referral_referred_user_id_snapshot ON subscription_orders
WHEN OLD.referral_policy_version IS NOT NULL
BEGIN SELECT RAISE(ABORT,'promotion order snapshot is immutable'); END;

CREATE TABLE referral_bonus_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id INTEGER NOT NULL,
    period_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_snapshot_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    hold_days INTEGER NOT NULL CHECK(hold_days BETWEEN 0 AND 365),
    current_target_minor INTEGER NOT NULL DEFAULT 0,
    locked_at TEXT NOT NULL,
    FOREIGN KEY(referrer_user_id) REFERENCES users(id),
    UNIQUE(referrer_user_id,period_key),
    CHECK(length(policy_sha256)=64)
);
CREATE TABLE referral_bonus_contributors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    period_id INTEGER NOT NULL,
    referrer_user_id INTEGER NOT NULL,
    referred_user_id INTEGER NOT NULL UNIQUE,
    source_order_no TEXT NOT NULL UNIQUE,
    period_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    available_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','available','reversed')),
    qualified_at TEXT NOT NULL,
    reversed_at TEXT,
    FOREIGN KEY(referrer_user_id) REFERENCES users(id),
    FOREIGN KEY(period_id) REFERENCES referral_bonus_periods(id),
    FOREIGN KEY(referred_user_id) REFERENCES users(id),
    FOREIGN KEY(source_order_no) REFERENCES subscription_orders(order_no)
);
CREATE TRIGGER trg_referral_bonus_periods_policy_immutable
BEFORE UPDATE OF referrer_user_id,period_key,policy_version,policy_snapshot_json,policy_sha256,hold_days,locked_at ON referral_bonus_periods
BEGIN SELECT RAISE(ABORT,'referral bonus period policy is immutable'); END;
CREATE TRIGGER trg_referral_bonus_periods_no_delete
BEFORE DELETE ON referral_bonus_periods
BEGIN SELECT RAISE(ABORT,'referral bonus period cannot be deleted'); END;
CREATE INDEX idx_referral_bonus_contributors_period ON referral_bonus_contributors(referrer_user_id,period_key,status);
CREATE TABLE referral_bonus_award_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    period_id INTEGER NOT NULL,
    referrer_user_id INTEGER NOT NULL,
    period_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    threshold_count INTEGER NOT NULL,
    cumulative_target_minor INTEGER NOT NULL,
    award_delta_minor INTEGER NOT NULL,
    available_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','available','reversed')),
    reversed_amount_minor INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(referrer_user_id) REFERENCES users(id),
    FOREIGN KEY(period_id) REFERENCES referral_bonus_periods(id),
    CHECK(award_delta_minor > 0), CHECK(reversed_amount_minor BETWEEN 0 AND award_delta_minor)
);
CREATE INDEX idx_referral_bonus_awards_due ON referral_bonus_award_events(available_at,status);
