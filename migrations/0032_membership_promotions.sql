ALTER TABLE subscription_orders ADD COLUMN list_price_minor INTEGER;
ALTER TABLE subscription_orders ADD COLUMN coupon_discount_minor INTEGER NOT NULL DEFAULT 0 CHECK(coupon_discount_minor >= 0);
ALTER TABLE subscription_orders ADD COLUMN referral_discount_minor INTEGER NOT NULL DEFAULT 0 CHECK(referral_discount_minor >= 0);
ALTER TABLE subscription_orders ADD COLUMN final_amount_minor INTEGER;
ALTER TABLE subscription_orders ADD COLUMN coupon_code_snapshot TEXT;
ALTER TABLE subscription_orders ADD COLUMN coupon_version_snapshot INTEGER;
ALTER TABLE subscription_orders ADD COLUMN referral_policy_version TEXT;
ALTER TABLE subscription_orders ADD COLUMN referral_eligible_snapshot INTEGER NOT NULL DEFAULT 0 CHECK(referral_eligible_snapshot IN (0,1));
ALTER TABLE subscription_orders ADD COLUMN referral_commission_rate_bps_snapshot INTEGER NOT NULL DEFAULT 0 CHECK(referral_commission_rate_bps_snapshot BETWEEN 0 AND 10000);
ALTER TABLE subscription_orders ADD COLUMN referral_commission_cap_minor_snapshot INTEGER NOT NULL DEFAULT 0 CHECK(referral_commission_cap_minor_snapshot >= 0);
ALTER TABLE subscription_orders ADD COLUMN referral_hold_days_snapshot INTEGER NOT NULL DEFAULT 0 CHECK(referral_hold_days_snapshot BETWEEN 0 AND 365);

UPDATE subscription_orders
SET list_price_minor=COALESCE(amount_minor,CAST(ROUND(amount * 100) AS INTEGER)),
    final_amount_minor=COALESCE(amount_minor,CAST(ROUND(amount * 100) AS INTEGER))
WHERE list_price_minor IS NULL OR final_amount_minor IS NULL;

CREATE TABLE referral_link_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attribution_id INTEGER NOT NULL UNIQUE,
    claim_hash TEXT NOT NULL UNIQUE,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    FOREIGN KEY(attribution_id) REFERENCES referral_attributions(id),
    CHECK(length(claim_hash)=64),
    CHECK(datetime(expires_at)>datetime(issued_at))
);
CREATE TABLE referral_link_claim_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_hash TEXT NOT NULL UNIQUE,
    invite_code TEXT NOT NULL,
    fingerprint_hash TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    CHECK(length(claim_hash)=64), CHECK(length(fingerprint_hash)=64)
);
CREATE INDEX idx_referral_link_claims_active ON referral_link_claims(expires_at,consumed_at);

CREATE TABLE referral_discount_eligibilities (
    attribution_id INTEGER PRIMARY KEY,
    link_claim_id INTEGER NOT NULL UNIQUE,
    eligible_at TEXT NOT NULL,
    FOREIGN KEY(attribution_id) REFERENCES referral_attributions(id),
    FOREIGN KEY(link_claim_id) REFERENCES referral_link_claims(id)
);

CREATE TABLE referral_coupon_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_key TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL CHECK(version >= 1),
    value_json TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(updated_by) REFERENCES users(id)
);

INSERT OR IGNORE INTO referral_coupon_policies(policy_key,version,value_json,updated_at)
VALUES ('membership_promotions_v2',1,'{"automatic_payout_review_threshold_minor":1000000,"bonus_enabled":false,"bonus_tiers":[{"cumulative_amount_minor":10000,"qualified_count":5},{"cumulative_amount_minor":40000,"qualified_count":15},{"cumulative_amount_minor":100000,"qualified_count":30}],"commission_cap_minor":50000,"commission_rate_bps":1000,"hold_days":30,"minimum_final_amount_minor":1,"referral_discount_bps":500,"withdrawal_cooldown_days":0,"withdrawal_daily_limit":3,"withdrawal_max_minor":500000,"withdrawal_min_minor":20000,"withdrawal_monthly_limit":2,"withdrawal_open_limit":1,"withdrawal_paused":false}',CURRENT_TIMESTAMP);

CREATE TABLE referral_coupon_policy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    value_json TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(created_by) REFERENCES users(id),
    UNIQUE(policy_key,version),
    CHECK(length(config_sha256)=64)
);
INSERT OR IGNORE INTO referral_coupon_policy_versions(policy_key,version,value_json,config_sha256,effective_at,created_at)
SELECT policy_key,version,value_json,'a43c2eb0d9860c61b29ae7d070418e373817e31dad7f9d8108e53542490634c4',updated_at,updated_at
FROM referral_coupon_policies WHERE policy_key='membership_promotions_v2';
CREATE TRIGGER trg_referral_coupon_policy_versions_no_update BEFORE UPDATE ON referral_coupon_policy_versions BEGIN SELECT RAISE(ABORT,'promotion policy versions are append-only'); END;
CREATE TRIGGER trg_referral_coupon_policy_versions_no_delete BEFORE DELETE ON referral_coupon_policy_versions BEGIN SELECT RAISE(ABORT,'promotion policy versions are append-only'); END;

CREATE TABLE membership_coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    campaign_name TEXT NOT NULL,
    discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed_hkd')),
    discount_value INTEGER NOT NULL CHECK(discount_value > 0),
    max_discount_minor INTEGER CHECK(max_discount_minor IS NULL OR max_discount_minor > 0),
    min_spend_minor INTEGER NOT NULL DEFAULT 0 CHECK(min_spend_minor >= 0),
    total_use_limit INTEGER NOT NULL CHECK(total_use_limit >= 1),
    per_user_limit INTEGER NOT NULL CHECK(per_user_limit >= 1),
    applicable_plans_json TEXT NOT NULL,
    applicable_cycles_json TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(created_by) REFERENCES users(id),
    FOREIGN KEY(updated_by) REFERENCES users(id),
    CHECK(length(code) BETWEEN 3 AND 64),
    CHECK(length(campaign_name) BETWEEN 1 AND 120),
    CHECK(datetime(expires_at)>datetime(starts_at))
);
CREATE INDEX idx_membership_coupons_lookup ON membership_coupons(code,enabled,starts_at,expires_at);

CREATE TABLE membership_coupon_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coupon_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    order_no TEXT NOT NULL UNIQUE,
    coupon_version INTEGER NOT NULL,
    discount_minor INTEGER NOT NULL CHECK(discount_minor >= 0),
    status TEXT NOT NULL DEFAULT 'reserved' CHECK(status IN ('reserved','consumed','released')),
    expires_at TEXT NOT NULL,
    redeemed_at TEXT NOT NULL,
    FOREIGN KEY(coupon_id) REFERENCES membership_coupons(id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(order_no) REFERENCES subscription_orders(order_no),
    UNIQUE(coupon_id,user_id,order_no)
);
CREATE INDEX idx_membership_coupon_redemptions_coupon ON membership_coupon_redemptions(coupon_id,user_id,id);

CREATE TABLE membership_first_paid_orders (
    user_id INTEGER PRIMARY KEY,
    order_no TEXT NOT NULL UNIQUE,
    claimed_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(order_no) REFERENCES subscription_orders(order_no)
);

CREATE TABLE referral_bonus_qualifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id INTEGER NOT NULL,
    period_key TEXT NOT NULL,
    qualified_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'disabled' CHECK(status IN ('disabled','pending','awarded','reversed')),
    policy_version TEXT NOT NULL,
    FOREIGN KEY(referrer_user_id) REFERENCES users(id),
    UNIQUE(referrer_user_id,period_key,policy_version)
);
CREATE TABLE referral_bonus_awards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qualification_id INTEGER NOT NULL UNIQUE,
    amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
    status TEXT NOT NULL CHECK(status IN ('pending','available','reversed')),
    available_at TEXT NOT NULL,
    reversed_at TEXT,
    FOREIGN KEY(qualification_id) REFERENCES referral_bonus_qualifications(id)
);

CREATE TABLE membership_promotion_admin_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    actor_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_public_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(actor_id) REFERENCES users(id),
    UNIQUE(actor_id,idempotency_key),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    CHECK(length(request_sha256)=64)
);
CREATE TRIGGER trg_membership_promotion_events_no_update BEFORE UPDATE ON membership_promotion_admin_events BEGIN SELECT RAISE(ABORT,'membership promotion events are append-only'); END;
CREATE TRIGGER trg_membership_promotion_events_no_delete BEFORE DELETE ON membership_promotion_admin_events BEGIN SELECT RAISE(ABORT,'membership promotion events are append-only'); END;
