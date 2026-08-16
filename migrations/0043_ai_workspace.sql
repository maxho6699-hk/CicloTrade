-- Owner-scoped AI workspace journal.  Provider output is untrusted and is
-- never allowed to create an assistant message without server citations.

CREATE TABLE ai_workspace_context_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    snapshot_version INTEGER NOT NULL CHECK(snapshot_version >= 1),
    context_json TEXT NOT NULL,
    context_sha256 TEXT NOT NULL CHECK(length(context_sha256)=64),
    created_at TEXT NOT NULL,
    retention_until TEXT,
    CHECK(length(public_id) BETWEEN 16 AND 160)
);

CREATE TABLE ai_workspace_citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    snapshot_public_id TEXT NOT NULL REFERENCES ai_workspace_context_snapshots(public_id),
    citation_kind TEXT NOT NULL,
    source_public_id TEXT NOT NULL,
    source_version INTEGER NOT NULL CHECK(source_version >= 1),
    title TEXT NOT NULL,
    observed_at TEXT,
    available_at TEXT,
    quote_at TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, snapshot_public_id, public_id)
);

CREATE TABLE ai_workspace_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    context_snapshot_public_id TEXT REFERENCES ai_workspace_context_snapshots(public_id),
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE ai_workspace_session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    session_public_id TEXT NOT NULL REFERENCES ai_workspace_sessions(public_id),
    seq INTEGER NOT NULL CHECK(seq >= 1),
    event_type TEXT NOT NULL CHECK(event_type IN ('created','archived')),
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, session_public_id, seq),
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE ai_workspace_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    session_public_id TEXT NOT NULL REFERENCES ai_workspace_sessions(public_id),
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, public_id)
);

CREATE TABLE ai_workspace_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    session_public_id TEXT NOT NULL REFERENCES ai_workspace_sessions(public_id),
    user_message_public_id TEXT REFERENCES ai_workspace_messages(public_id),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','cancelled','blocked','timed_out')),
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
    idempotency_key TEXT NOT NULL,
    blocked_reason TEXT,
    error_code TEXT,
    provider_version TEXT,
    contract_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancel_requested_at TEXT,
    cancel_idempotency_key TEXT,
    cancel_request_sha256 TEXT,
    UNIQUE(owner_id, idempotency_key),
    CHECK(length(idempotency_key) BETWEEN 8 AND 128)
);

CREATE TABLE ai_workspace_task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    task_public_id TEXT NOT NULL REFERENCES ai_workspace_tasks(public_id),
    seq INTEGER NOT NULL CHECK(seq >= 1),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','cancelled','blocked','timed_out')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, task_public_id, seq)
);

CREATE TABLE ai_workspace_paper_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    session_public_id TEXT NOT NULL REFERENCES ai_workspace_sessions(public_id),
    task_public_id TEXT NOT NULL REFERENCES ai_workspace_tasks(public_id),
    draft_json TEXT NOT NULL,
    draft_sha256 TEXT NOT NULL CHECK(length(draft_sha256)=64),
    created_at TEXT NOT NULL,
    CHECK(length(public_id) BETWEEN 16 AND 160)
);

CREATE TABLE ai_workspace_retention_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    resource_kind TEXT NOT NULL,
    resource_public_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_ai_workspace_sessions_owner ON ai_workspace_sessions(owner_id, created_at DESC);
CREATE INDEX idx_ai_workspace_session_events_owner ON ai_workspace_session_events(owner_id, session_public_id, seq);
CREATE INDEX idx_ai_workspace_messages_owner ON ai_workspace_messages(owner_id, session_public_id, created_at);
CREATE INDEX idx_ai_workspace_tasks_owner ON ai_workspace_tasks(owner_id, created_at DESC);
CREATE INDEX idx_ai_workspace_task_events_owner ON ai_workspace_task_events(owner_id, task_public_id, seq);
CREATE INDEX idx_ai_workspace_citations_owner ON ai_workspace_citations(owner_id, snapshot_public_id);

CREATE TRIGGER trg_ai_citation_snapshot_owner
BEFORE INSERT ON ai_workspace_citations
WHEN (SELECT owner_id FROM ai_workspace_context_snapshots WHERE public_id=NEW.snapshot_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'AI citation owner mismatch'); END;

CREATE TRIGGER trg_ai_session_snapshot_owner
BEFORE INSERT ON ai_workspace_sessions
WHEN NEW.context_snapshot_public_id IS NOT NULL
 AND (SELECT owner_id FROM ai_workspace_context_snapshots WHERE public_id=NEW.context_snapshot_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'AI session snapshot owner mismatch'); END;

CREATE TRIGGER trg_ai_session_event_owner
BEFORE INSERT ON ai_workspace_session_events
WHEN (SELECT owner_id FROM ai_workspace_sessions WHERE public_id=NEW.session_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'AI session event owner mismatch'); END;

CREATE TRIGGER trg_ai_message_owner
BEFORE INSERT ON ai_workspace_messages
WHEN (SELECT owner_id FROM ai_workspace_sessions WHERE public_id=NEW.session_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'AI message owner mismatch'); END;

CREATE TRIGGER trg_ai_task_owner
BEFORE INSERT ON ai_workspace_tasks
WHEN (SELECT owner_id FROM ai_workspace_sessions WHERE public_id=NEW.session_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'AI task owner mismatch'); END;

CREATE TRIGGER trg_ai_task_message_consistency
BEFORE INSERT ON ai_workspace_tasks
WHEN NEW.user_message_public_id IS NOT NULL
 AND ((SELECT owner_id FROM ai_workspace_messages WHERE public_id=NEW.user_message_public_id) IS NOT NEW.owner_id
      OR (SELECT session_public_id FROM ai_workspace_messages WHERE public_id=NEW.user_message_public_id) IS NOT NEW.session_public_id)
BEGIN SELECT RAISE(ABORT, 'AI task message mismatch'); END;

CREATE TRIGGER trg_ai_task_event_owner
BEFORE INSERT ON ai_workspace_task_events
WHEN (SELECT owner_id FROM ai_workspace_tasks WHERE public_id=NEW.task_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'AI task event owner mismatch'); END;

CREATE TRIGGER trg_ai_draft_owner
BEFORE INSERT ON ai_workspace_paper_drafts
WHEN (SELECT owner_id FROM ai_workspace_tasks WHERE public_id=NEW.task_public_id) IS NOT NEW.owner_id
BEGIN SELECT RAISE(ABORT, 'AI draft owner mismatch'); END;

CREATE TRIGGER trg_ai_draft_session_consistency
BEFORE INSERT ON ai_workspace_paper_drafts
WHEN (SELECT session_public_id FROM ai_workspace_tasks WHERE public_id=NEW.task_public_id) IS NOT NEW.session_public_id
BEGIN SELECT RAISE(ABORT, 'AI draft session mismatch'); END;

CREATE TRIGGER trg_ai_context_no_update BEFORE UPDATE ON ai_workspace_context_snapshots BEGIN SELECT RAISE(ABORT, 'AI context snapshots are append-only'); END;
CREATE TRIGGER trg_ai_context_no_delete BEFORE DELETE ON ai_workspace_context_snapshots BEGIN SELECT RAISE(ABORT, 'AI context snapshots are append-only'); END;
CREATE TRIGGER trg_ai_citations_no_update BEFORE UPDATE ON ai_workspace_citations BEGIN SELECT RAISE(ABORT, 'AI citations are append-only'); END;
CREATE TRIGGER trg_ai_citations_no_delete BEFORE DELETE ON ai_workspace_citations BEGIN SELECT RAISE(ABORT, 'AI citations are append-only'); END;
CREATE TRIGGER trg_ai_sessions_no_update BEFORE UPDATE ON ai_workspace_sessions BEGIN SELECT RAISE(ABORT, 'AI sessions are append-only'); END;
CREATE TRIGGER trg_ai_sessions_no_delete BEFORE DELETE ON ai_workspace_sessions BEGIN SELECT RAISE(ABORT, 'AI sessions are append-only'); END;
CREATE TRIGGER trg_ai_session_events_no_update BEFORE UPDATE ON ai_workspace_session_events BEGIN SELECT RAISE(ABORT, 'AI session events are append-only'); END;
CREATE TRIGGER trg_ai_session_events_no_delete BEFORE DELETE ON ai_workspace_session_events BEGIN SELECT RAISE(ABORT, 'AI session events are append-only'); END;
CREATE TRIGGER trg_ai_messages_no_update BEFORE UPDATE ON ai_workspace_messages BEGIN SELECT RAISE(ABORT, 'AI messages are append-only'); END;
CREATE TRIGGER trg_ai_messages_no_delete BEFORE DELETE ON ai_workspace_messages BEGIN SELECT RAISE(ABORT, 'AI messages are append-only'); END;
CREATE TRIGGER trg_ai_tasks_no_delete BEFORE DELETE ON ai_workspace_tasks BEGIN SELECT RAISE(ABORT, 'AI tasks are append-only'); END;
CREATE TRIGGER trg_ai_task_events_no_update BEFORE UPDATE ON ai_workspace_task_events BEGIN SELECT RAISE(ABORT, 'AI task events are append-only'); END;
CREATE TRIGGER trg_ai_task_events_no_delete BEFORE DELETE ON ai_workspace_task_events BEGIN SELECT RAISE(ABORT, 'AI task events are append-only'); END;
CREATE TRIGGER trg_ai_drafts_no_update BEFORE UPDATE ON ai_workspace_paper_drafts BEGIN SELECT RAISE(ABORT, 'AI paper drafts are append-only'); END;
CREATE TRIGGER trg_ai_drafts_no_delete BEFORE DELETE ON ai_workspace_paper_drafts BEGIN SELECT RAISE(ABORT, 'AI paper drafts are append-only'); END;
