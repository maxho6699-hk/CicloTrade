ALTER TABLE manual_payment_claims ADD COLUMN evidence_source TEXT NOT NULL DEFAULT 'telegram';
ALTER TABLE manual_payment_claims ADD COLUMN evidence_storage_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_payment_claims_storage_key
    ON manual_payment_claims(evidence_storage_key)
    WHERE evidence_storage_key IS NOT NULL;
