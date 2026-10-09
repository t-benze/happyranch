-- PROPOSAL ONLY; NOT APPROVED, NOT EXECUTED. Desired G definitions, not an upgrade script.
-- These two replace their named F/E definitions only in G; all other F/E objects stay unchanged.
CREATE TABLE workflow_submissions (id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES workflow_instances(id), revision INTEGER NOT NULL CHECK(revision>0), submission_bytes BLOB NOT NULL, submission_digest TEXT NOT NULL, storage_ref TEXT, source_task_id TEXT NOT NULL, source_session_id TEXT NOT NULL, source_result_id TEXT, author_principal TEXT NOT NULL, UNIQUE(instance_id,revision), UNIQUE(id,submission_digest), CHECK(storage_ref IS NOT NULL OR length(submission_bytes)>0));
CREATE TABLE workflow_events (id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES workflow_instances(id), revision INTEGER NOT NULL CHECK(revision>0), event_kind TEXT NOT NULL CHECK(event_kind IN ('submitted','joined')), event_bytes BLOB NOT NULL, event_digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, UNIQUE(instance_id,revision,event_kind));
-- G-only additive objects. Marker row inserted in the same successful migration/creation transaction.
CREATE TABLE workflow_submission_schema_versions (version INTEGER PRIMARY KEY CHECK(version=1));
CREATE TABLE workflow_submission_operations (
    submission_id TEXT PRIMARY KEY REFERENCES workflow_submissions(id),
    instance_id TEXT NOT NULL REFERENCES workflow_instances(id),
    org_slug TEXT NOT NULL,
    principal TEXT NOT NULL,
    source_task_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    provenance_bytes BLOB NOT NULL,
    provenance_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(org_slug,principal,operation_key),
    UNIQUE(submission_id,source_task_id,source_session_id)
);
CREATE TABLE workflow_submission_result_links (
    submission_id TEXT PRIMARY KEY REFERENCES workflow_submission_operations(submission_id),
    source_task_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    task_result_id INTEGER NOT NULL REFERENCES task_results(id),
    result_bytes BLOB NOT NULL,
    result_digest TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    FOREIGN KEY(submission_id,source_task_id,source_session_id)
        REFERENCES workflow_submission_operations(submission_id,source_task_id,source_session_id)
);
CREATE INDEX workflow_submission_operations_source_idx
    ON workflow_submission_operations(instance_id,source_task_id,source_session_id);
CREATE INDEX workflow_submission_result_links_result_idx
    ON workflow_submission_result_links(task_result_id);
