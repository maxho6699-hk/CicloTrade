CREATE TABLE personal_paper_risk_proofs (
    public_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    account_version INTEGER NOT NULL CHECK(account_version >= 0),
    draft_sha256 TEXT NOT NULL CHECK(length(draft_sha256)=64),
    schema_version TEXT NOT NULL CHECK(schema_version='r1'),
    computed_at TEXT NOT NULL,
    marks_as_of TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('allow','review','reject')),
    risk_level TEXT NOT NULL CHECK(risk_level IN ('low','moderate','high','blocked')),
    data_state TEXT NOT NULL CHECK(data_state IN ('fresh','partial','stale','missing')),
    checks_json TEXT NOT NULL CHECK(json_valid(checks_json) AND json_type(checks_json)='array'),
    blocking_reasons_json TEXT NOT NULL CHECK(json_valid(blocking_reasons_json) AND json_type(blocking_reasons_json)='array'),
    warnings_json TEXT NOT NULL CHECK(json_valid(warnings_json) AND json_type(warnings_json)='array'),
    proof_payload_json TEXT NOT NULL CHECK(json_valid(proof_payload_json) AND json_type(proof_payload_json)='object'),
    proof_sha256 TEXT NOT NULL CHECK(length(proof_sha256)=64),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id),
    FOREIGN KEY(quote_id) REFERENCES personal_paper_quote_proofs(public_id),
    CHECK(expires_at > created_at AND expires_at > computed_at),
    CHECK(marks_as_of <= computed_at)
);
CREATE INDEX idx_personal_paper_risk_proofs_owner_expiry
ON personal_paper_risk_proofs(user_id,season_id,expires_at,public_id);
CREATE TRIGGER trg_personal_paper_risk_proofs_owner
BEFORE INSERT ON personal_paper_risk_proofs
WHEN NOT EXISTS (
    SELECT 1 FROM personal_paper_seasons s
    JOIN personal_paper_quote_proofs q ON q.public_id=NEW.quote_id
    WHERE s.id=NEW.season_id AND s.user_id=NEW.user_id AND s.state='active'
      AND q.user_id=NEW.user_id AND q.season_id=NEW.season_id
)
BEGIN SELECT RAISE(ABORT,'personal paper risk proof owner mismatch'); END;
CREATE TRIGGER trg_personal_paper_risk_proofs_no_update
BEFORE UPDATE ON personal_paper_risk_proofs
BEGIN SELECT RAISE(ABORT,'personal paper risk proofs are append-only'); END;
CREATE TRIGGER trg_personal_paper_risk_proofs_no_delete
BEFORE DELETE ON personal_paper_risk_proofs
BEGIN SELECT RAISE(ABORT,'personal paper risk proofs are append-only'); END;

CREATE TABLE personal_paper_risk_proof_consumptions (
    proof_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    draft_sha256 TEXT NOT NULL CHECK(length(draft_sha256)=64),
    idempotency_key TEXT NOT NULL,
    consumed_at TEXT NOT NULL,
    FOREIGN KEY(proof_id) REFERENCES personal_paper_risk_proofs(public_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id)
);
CREATE TRIGGER trg_personal_paper_risk_consumptions_owner
BEFORE INSERT ON personal_paper_risk_proof_consumptions
WHEN NOT EXISTS (
    SELECT 1 FROM personal_paper_risk_proofs p
    JOIN personal_paper_seasons s ON s.id=NEW.season_id
    WHERE p.public_id=NEW.proof_id AND p.user_id=NEW.user_id
      AND p.season_id=NEW.season_id AND p.draft_sha256=NEW.draft_sha256
      AND s.user_id=NEW.user_id AND s.state='active'
)
BEGIN SELECT RAISE(ABORT,'personal paper risk consumption owner mismatch'); END;
CREATE TRIGGER trg_personal_paper_risk_consumptions_no_update
BEFORE UPDATE ON personal_paper_risk_proof_consumptions
BEGIN SELECT RAISE(ABORT,'personal paper risk proof consumptions are append-only'); END;
CREATE TRIGGER trg_personal_paper_risk_consumptions_no_delete
BEFORE DELETE ON personal_paper_risk_proof_consumptions
BEGIN SELECT RAISE(ABORT,'personal paper risk proof consumptions are append-only'); END;

CREATE TABLE personal_paper_risk_proof_events (
    public_id TEXT PRIMARY KEY,
    proof_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('ISSUED','CONSUMED','REJECTED')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
    occurred_at TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    FOREIGN KEY(proof_id) REFERENCES personal_paper_risk_proofs(public_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(season_id) REFERENCES personal_paper_seasons(id)
);
CREATE INDEX idx_personal_paper_risk_events_owner_time
ON personal_paper_risk_proof_events(user_id,season_id,occurred_at,public_id);
CREATE TRIGGER trg_personal_paper_risk_proof_events_owner
BEFORE INSERT ON personal_paper_risk_proof_events
WHEN NOT EXISTS (
    SELECT 1 FROM personal_paper_risk_proofs p
    WHERE p.public_id=NEW.proof_id AND p.user_id=NEW.user_id AND p.season_id=NEW.season_id
)
BEGIN SELECT RAISE(ABORT,'personal paper risk proof event owner mismatch'); END;
CREATE TRIGGER trg_personal_paper_risk_proof_events_no_update
BEFORE UPDATE ON personal_paper_risk_proof_events
BEGIN SELECT RAISE(ABORT,'personal paper risk proof events are append-only'); END;
CREATE TRIGGER trg_personal_paper_risk_proof_events_no_delete
BEFORE DELETE ON personal_paper_risk_proof_events
BEGIN SELECT RAISE(ABORT,'personal paper risk proof events are append-only'); END;
