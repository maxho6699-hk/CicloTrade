ALTER TABLE signal_import_jobs ADD COLUMN public_id TEXT;
ALTER TABLE signal_import_jobs ADD COLUMN idempotency_key TEXT;
ALTER TABLE signal_import_jobs ADD COLUMN request_sha256 TEXT;
ALTER TABLE signal_import_jobs ADD COLUMN provenance_sha256 TEXT;

UPDATE signal_import_jobs
SET public_id = 'legacy_' || printf('%024x', id)
WHERE public_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_signal_import_jobs_public_id
    ON signal_import_jobs(public_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_signal_import_jobs_idempotency
    ON signal_import_jobs(user_id, import_type, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_signal_import_jobs_request_sha
    ON signal_import_jobs(user_id, import_type, request_sha256)
    WHERE request_sha256 IS NOT NULL;
