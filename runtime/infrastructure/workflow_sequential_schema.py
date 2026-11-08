"""Canonical org-owned sequential storage. Constants only; no installer.

U2a is source-inert. In particular, importing this module executes no SQL and
neither Database nor OrgState imports it. U2b owns explicit installation; U3
owns admitted writes. These records do not extend document or Coding grammar.
"""
from __future__ import annotations


SEQUENTIAL_TABLES = (
    "workflow_seq_schema_versions", "workflow_seq_definitions",
    "workflow_seq_settings", "workflow_seq_settings_operations",
    "workflow_seq_runs", "workflow_seq_visits", "workflow_seq_outputs",
    "workflow_seq_evidence", "workflow_seq_events", "workflow_seq_outbox",
)

CANONICAL_WORKFLOW_SEQUENTIAL_DDL = """\
CREATE TABLE workflow_seq_schema_versions (
  version INTEGER NOT NULL PRIMARY KEY CHECK(typeof(version)='integer' AND version=1)
);
CREATE TABLE workflow_seq_definitions (
  id TEXT NOT NULL PRIMARY KEY,
  identity TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>0),
  definition_bytes BLOB NOT NULL CHECK(typeof(definition_bytes)='blob'),
  definition_digest TEXT NOT NULL UNIQUE,
  source_digest TEXT NOT NULL,
  compiler_pin TEXT NOT NULL,
  validator_pin TEXT NOT NULL,
  interpreter_pin TEXT NOT NULL,
  capability_pin TEXT NOT NULL,
  UNIQUE(identity,version)
);
CREATE TABLE workflow_seq_settings (
  id TEXT NOT NULL PRIMARY KEY,
  org_slug TEXT NOT NULL,
  definition_id TEXT NOT NULL REFERENCES workflow_seq_definitions(id),
  definition_identity TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
  snapshot_bytes BLOB NOT NULL CHECK(typeof(snapshot_bytes)='blob'),
  snapshot_digest TEXT NOT NULL,
  UNIQUE(org_slug,definition_identity,generation)
);
CREATE TABLE workflow_seq_settings_operations (
  id TEXT NOT NULL PRIMARY KEY,
  org_slug TEXT NOT NULL,
  principal TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  request_bytes BLOB NOT NULL CHECK(typeof(request_bytes)='blob'),
  request_digest TEXT NOT NULL,
  settings_id TEXT NOT NULL UNIQUE REFERENCES workflow_seq_settings(id),
  receipt_bytes BLOB NOT NULL CHECK(typeof(receipt_bytes)='blob'),
  receipt_digest TEXT NOT NULL,
  UNIQUE(org_slug,principal,operation_key)
);
CREATE TABLE workflow_seq_runs (
  id TEXT NOT NULL PRIMARY KEY,
  org_slug TEXT NOT NULL,
  definition_id TEXT NOT NULL REFERENCES workflow_seq_definitions(id),
  settings_id TEXT NOT NULL REFERENCES workflow_seq_settings(id),
  source_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  source_principal TEXT NOT NULL,
  source_session_id TEXT NOT NULL,
  source_result_id INTEGER NOT NULL UNIQUE REFERENCES task_results(id)
    CHECK(typeof(source_result_id)='integer' AND source_result_id>0),
  carrier_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  start_operation_key TEXT NOT NULL,
  start_bytes BLOB NOT NULL CHECK(typeof(start_bytes)='blob'),
  start_digest TEXT NOT NULL,
  original_limits_bytes BLOB NOT NULL CHECK(typeof(original_limits_bytes)='blob'),
  active_visit_id TEXT REFERENCES workflow_seq_visits(id),
  state TEXT NOT NULL CHECK(state IN ('active','held','completed','failed','cancelled')),
  epoch INTEGER NOT NULL CHECK(typeof(epoch)='integer' AND epoch BETWEEN 1 AND 3),
  content_returns INTEGER NOT NULL CHECK(typeof(content_returns)='integer' AND content_returns BETWEEN 0 AND 2),
  visit_count INTEGER NOT NULL CHECK(typeof(visit_count)='integer' AND visit_count BETWEEN 1 AND 64),
  event_count INTEGER NOT NULL CHECK(typeof(event_count)='integer' AND event_count BETWEEN 1 AND 4096),
  cas INTEGER NOT NULL CHECK(typeof(cas)='integer' AND cas>=0),
  last_event_id TEXT NOT NULL REFERENCES workflow_seq_events(id) DEFERRABLE INITIALLY DEFERRED,
  CHECK(source_task_id!=carrier_task_id),
  CHECK(epoch=content_returns+1),
  UNIQUE(source_task_id,source_result_id,start_operation_key),
  UNIQUE(org_slug,source_principal,start_operation_key)
);
CREATE TABLE workflow_seq_visits (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  node_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal BETWEEN 1 AND 64),
  epoch INTEGER NOT NULL CHECK(typeof(epoch)='integer' AND epoch BETWEEN 1 AND 3),
  predecessor_event_id TEXT REFERENCES workflow_seq_events(id),
  task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  principal TEXT NOT NULL,
  session_id TEXT,
  result_id INTEGER UNIQUE REFERENCES task_results(id)
    CHECK(result_id IS NULL OR (typeof(result_id)='integer' AND result_id>0)),
  result_bytes BLOB,
  result_digest TEXT,
  verified_checks_bytes BLOB,
  input_selection_bytes BLOB NOT NULL CHECK(typeof(input_selection_bytes)='blob'),
  input_selection_digest TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('queued','running','waiting','held','completed','failed','cancelled')),
  selected INTEGER NOT NULL CHECK(typeof(selected)='integer' AND selected IN (0,1)),
  CHECK(result_id IS NULL OR session_id IS NOT NULL),
  CHECK((result_id IS NULL AND result_bytes IS NULL AND result_digest IS NULL AND verified_checks_bytes IS NULL) OR
        (result_id IS NOT NULL AND typeof(result_bytes)='blob' AND result_digest IS NOT NULL AND typeof(verified_checks_bytes)='blob')),
  CHECK(selected=0 OR state IN ('queued','running','waiting','held')),
  UNIQUE(run_id,ordinal),
  UNIQUE(run_id,predecessor_event_id),
  UNIQUE(id,run_id)
);
CREATE UNIQUE INDEX workflow_seq_one_selected_visit ON workflow_seq_visits(run_id) WHERE selected=1;
CREATE TABLE workflow_seq_outputs (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  visit_id TEXT NOT NULL,
  slot TEXT NOT NULL,
  epoch INTEGER NOT NULL CHECK(typeof(epoch)='integer' AND epoch BETWEEN 1 AND 3),
  contract TEXT NOT NULL,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  session_id TEXT NOT NULL,
  result_id INTEGER NOT NULL REFERENCES task_results(id) CHECK(typeof(result_id)='integer' AND result_id>0),
  content_bytes BLOB NOT NULL CHECK(typeof(content_bytes)='blob'),
  content_digest TEXT NOT NULL,
  recipients_bytes BLOB NOT NULL CHECK(typeof(recipients_bytes)='blob'),
  FOREIGN KEY(visit_id,run_id) REFERENCES workflow_seq_visits(id,run_id),
  UNIQUE(visit_id,slot)
);
CREATE TABLE workflow_seq_evidence (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  visit_id TEXT,
  kind TEXT NOT NULL CHECK(kind IN ('preflight','review','qa','intent','action','observation','terminal','outer')),
  evidence_bytes BLOB NOT NULL CHECK(typeof(evidence_bytes)='blob'),
  evidence_digest TEXT NOT NULL,
  task_id TEXT REFERENCES tasks(id),
  principal TEXT,
  session_id TEXT,
  result_id INTEGER REFERENCES task_results(id)
    CHECK(result_id IS NULL OR (typeof(result_id)='integer' AND result_id>0)),
  FOREIGN KEY(visit_id,run_id) REFERENCES workflow_seq_visits(id,run_id),
  CHECK((task_id IS NULL AND principal IS NULL AND session_id IS NULL AND result_id IS NULL) OR
        (task_id IS NOT NULL AND principal IS NOT NULL AND session_id IS NOT NULL AND result_id IS NOT NULL))
);
CREATE TABLE workflow_seq_events (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  sequence INTEGER NOT NULL CHECK(typeof(sequence)='integer' AND sequence BETWEEN 1 AND 4096),
  operation_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('admitted','transition','cancelled','returned')),
  prior_digest TEXT NOT NULL,
  event_bytes BLOB NOT NULL CHECK(typeof(event_bytes)='blob'),
  event_digest TEXT NOT NULL,
  source_visit_id TEXT,
  source_result_id INTEGER REFERENCES task_results(id)
    CHECK(source_result_id IS NULL OR (typeof(source_result_id)='integer' AND source_result_id>0)),
  FOREIGN KEY(source_visit_id,run_id) REFERENCES workflow_seq_visits(id,run_id),
  CHECK((kind='transition' AND source_visit_id IS NOT NULL AND source_result_id IS NOT NULL) OR
        (kind!='transition' AND source_visit_id IS NULL AND source_result_id IS NULL)),
  UNIQUE(run_id,sequence),
  UNIQUE(run_id,operation_key),
  UNIQUE(source_visit_id),
  UNIQUE(source_result_id),
  UNIQUE(id,run_id)
);
CREATE TABLE workflow_seq_outbox (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  event_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('visit_queued','run_return')),
  visit_id TEXT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  notification_bytes BLOB NOT NULL CHECK(typeof(notification_bytes)='blob'),
  notification_digest TEXT NOT NULL,
  FOREIGN KEY(event_id,run_id) REFERENCES workflow_seq_events(id,run_id),
  FOREIGN KEY(visit_id,run_id) REFERENCES workflow_seq_visits(id,run_id),
  CHECK((kind='visit_queued' AND visit_id IS NOT NULL) OR (kind='run_return' AND visit_id IS NULL)),
  UNIQUE(event_id,kind)
);
CREATE TRIGGER workflow_seq_definitions_no_update BEFORE UPDATE ON workflow_seq_definitions BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_definitions_no_delete BEFORE DELETE ON workflow_seq_definitions BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_settings_no_update BEFORE UPDATE ON workflow_seq_settings BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_settings_no_delete BEFORE DELETE ON workflow_seq_settings BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_settings_operations_no_update BEFORE UPDATE ON workflow_seq_settings_operations BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_settings_operations_no_delete BEFORE DELETE ON workflow_seq_settings_operations BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_outputs_no_update BEFORE UPDATE ON workflow_seq_outputs BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_outputs_no_delete BEFORE DELETE ON workflow_seq_outputs BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_evidence_no_update BEFORE UPDATE ON workflow_seq_evidence BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_evidence_no_delete BEFORE DELETE ON workflow_seq_evidence BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_events_no_update BEFORE UPDATE ON workflow_seq_events BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_events_no_delete BEFORE DELETE ON workflow_seq_events BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_outbox_no_update BEFORE UPDATE ON workflow_seq_outbox BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_outbox_no_delete BEFORE DELETE ON workflow_seq_outbox BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_runs_pins_immutable BEFORE UPDATE OF id,org_slug,definition_id,settings_id,source_task_id,source_principal,source_session_id,source_result_id,carrier_task_id,start_operation_key,start_bytes,start_digest,original_limits_bytes ON workflow_seq_runs BEGIN SELECT RAISE(ABORT,'sequential admission is immutable'); END;
CREATE TRIGGER workflow_seq_runs_no_delete BEFORE DELETE ON workflow_seq_runs BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_visits_pins_immutable BEFORE UPDATE OF id,run_id,node_id,ordinal,epoch,predecessor_event_id,task_id,principal,input_selection_bytes,input_selection_digest ON workflow_seq_visits BEGIN SELECT RAISE(ABORT,'sequential visit pins are immutable'); END;
CREATE TRIGGER workflow_seq_visits_no_delete BEFORE DELETE ON workflow_seq_visits BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_visits_result_immutable BEFORE UPDATE ON workflow_seq_visits WHEN OLD.result_id IS NOT NULL AND (NEW.result_id IS NOT OLD.result_id OR NEW.session_id IS NOT OLD.session_id OR NEW.result_bytes IS NOT OLD.result_bytes OR NEW.result_digest IS NOT OLD.result_digest OR NEW.verified_checks_bytes IS NOT OLD.verified_checks_bytes) BEGIN SELECT RAISE(ABORT,'sequential result is immutable'); END;
"""
