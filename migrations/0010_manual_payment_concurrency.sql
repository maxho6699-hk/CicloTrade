ALTER TABLE manual_payment_claims ADD COLUMN evidence_sha256 TEXT;

UPDATE manual_payment_claims
SET evidence_sha256=lower(evidence_file_unique_id)
WHERE evidence_source='web'
  AND length(evidence_file_unique_id)=64
  AND lower(evidence_file_unique_id) NOT GLOB '*[^0-9a-f]*';

CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_payment_claims_active_sha256
    ON manual_payment_claims(evidence_sha256)
    WHERE evidence_sha256 IS NOT NULL AND status IN ('submitted','approved');

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_orders_pending_purchase
    ON subscription_orders(user_id,request_fingerprint)
    WHERE status='pending'
      AND request_fingerprint IS NOT NULL
      AND pay_method IN ('fps','alipay','wechat');
