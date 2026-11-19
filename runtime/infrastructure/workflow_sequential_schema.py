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


# Schema1 remains the default for A2/A4 until S2b explicitly selects schema2.
SEQUENTIAL_V2_TABLES = SEQUENTIAL_TABLES + ('workflow_seq_assignments', 'workflow_seq_node_counters', 'workflow_seq_controls', 'workflow_seq_source_wait_sets', 'workflow_seq_source_memberships', 'workflow_seq_inbox', 'workflow_seq_inbox_deliveries', 'workflow_seq_consumption_receipts', 'workflow_seq_consumed_notifications')

CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL = """\
CREATE TABLE workflow_seq_schema_versions (
  version INTEGER NOT NULL PRIMARY KEY CHECK(typeof(version)='integer' AND version=2)
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
  source_task_id TEXT NOT NULL REFERENCES tasks(id),
  source_principal TEXT NOT NULL,
  source_session_id TEXT NOT NULL,
  source_result_id INTEGER NOT NULL REFERENCES task_results(id)
    CHECK(typeof(source_result_id)='integer' AND source_result_id>0),
  carrier_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  start_operation_key TEXT NOT NULL,
  start_bytes BLOB NOT NULL CHECK(typeof(start_bytes)='blob'),
  start_digest TEXT NOT NULL,
  original_limits_bytes BLOB NOT NULL CHECK(typeof(original_limits_bytes)='blob'),
  engine_version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(engine_version)='integer' AND engine_version IN (1,2)),
  assignment_snapshot_bytes BLOB,
  assignment_snapshot_digest TEXT,
  assignment_generation INTEGER,
  held_node_id TEXT,
  held_event_id TEXT,
  terminal_outcome TEXT CHECK(terminal_outcome IN ('completed','failed','cancelled')),
  progress TEXT CHECK(progress IN ('queued','executing','result_pending_settlement','cancelling')),
  recovery_count INTEGER NOT NULL DEFAULT 0 CHECK(typeof(recovery_count)='integer' AND recovery_count BETWEEN 0 AND 64),
  active_visit_id TEXT,
  active_visit_selected INTEGER NOT NULL DEFAULT 1 CHECK(typeof(active_visit_selected)='integer' AND active_visit_selected=1),
  state TEXT NOT NULL CHECK(state IN ('active','held','completed','failed','cancelled','running','finished')),
  epoch INTEGER CHECK(epoch IS NULL OR (typeof(epoch)='integer' AND epoch BETWEEN 1 AND 3)),
  content_returns INTEGER CHECK(content_returns IS NULL OR (typeof(content_returns)='integer' AND content_returns BETWEEN 0 AND 2)),
  visit_count INTEGER NOT NULL CHECK(typeof(visit_count)='integer' AND visit_count BETWEEN 0 AND 64),
  event_count INTEGER NOT NULL CHECK(typeof(event_count)='integer' AND event_count BETWEEN 1 AND 4096),
  cas INTEGER NOT NULL CHECK(typeof(cas)='integer' AND cas>=0),
  last_event_id TEXT NOT NULL REFERENCES workflow_seq_events(id) DEFERRABLE INITIALLY DEFERRED,
  CHECK(source_task_id!=carrier_task_id),
  FOREIGN KEY(held_event_id,id) REFERENCES workflow_seq_events(id,run_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(active_visit_id,id,active_visit_selected) REFERENCES workflow_seq_visits(id,run_id,selected) DEFERRABLE INITIALLY DEFERRED,
  CHECK((engine_version=1 AND epoch IS NOT NULL AND content_returns IS NOT NULL
    AND epoch=content_returns+1 AND visit_count>=1 AND state IN ('active','held','completed','failed','cancelled')
    AND assignment_snapshot_bytes IS NULL AND assignment_snapshot_digest IS NULL AND assignment_generation IS NULL
    AND held_node_id IS NULL AND held_event_id IS NULL AND terminal_outcome IS NULL AND progress IS NULL AND recovery_count=0) OR
    (engine_version=2 AND epoch IS NULL AND content_returns IS NULL
    AND typeof(assignment_snapshot_bytes)='blob' AND assignment_snapshot_digest IS NOT NULL
    AND typeof(assignment_generation)='integer' AND assignment_generation>0
    AND ((state='running' AND active_visit_id IS NOT NULL AND held_node_id IS NULL AND held_event_id IS NULL
          AND terminal_outcome IS NULL AND progress IS NOT NULL) OR
         (state='held' AND active_visit_id IS NULL AND held_node_id IS NOT NULL AND held_event_id IS NOT NULL
          AND terminal_outcome IS NULL AND progress IS NULL) OR
         (state='finished' AND active_visit_id IS NULL AND held_node_id IS NULL AND held_event_id IS NULL
          AND terminal_outcome IS NOT NULL AND progress IS NULL)))),
  UNIQUE(source_task_id,source_result_id,start_operation_key),
  UNIQUE(org_slug,source_principal,source_task_id,start_operation_key),
  UNIQUE(id,source_task_id,source_result_id,start_operation_key),
  UNIQUE(id,engine_version),
  UNIQUE(id,source_task_id,source_principal)
);
CREATE UNIQUE INDEX workflow_seq_legacy_source_task ON workflow_seq_runs(source_task_id) WHERE engine_version=1;
CREATE UNIQUE INDEX workflow_seq_legacy_source_result ON workflow_seq_runs(source_result_id) WHERE engine_version=1;
CREATE UNIQUE INDEX workflow_seq_legacy_start_operation ON workflow_seq_runs(org_slug,source_principal,start_operation_key) WHERE engine_version=1;
CREATE TABLE workflow_seq_visits (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  engine_version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(engine_version)='integer' AND engine_version IN (1,2)),
  node_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal BETWEEN 1 AND 64),
  epoch INTEGER CHECK(epoch IS NULL OR (typeof(epoch)='integer' AND epoch BETWEEN 1 AND 3)),
  predecessor_event_id TEXT REFERENCES workflow_seq_events(id),
  task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  principal TEXT NOT NULL,
  session_id TEXT,
  result_id INTEGER UNIQUE REFERENCES task_results(id)
    CHECK(result_id IS NULL OR (typeof(result_id)='integer' AND result_id>0)),
  result_bytes BLOB,
  result_digest TEXT,
  verified_checks_bytes BLOB,
  generic_checks_bytes BLOB,
  assignment_id TEXT,
  assignment_generation INTEGER,
  input_selection_bytes BLOB NOT NULL CHECK(typeof(input_selection_bytes)='blob'),
  input_selection_digest TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('queued','running','waiting','held','completed','failed','cancelled')),
  selected INTEGER NOT NULL CHECK(typeof(selected)='integer' AND selected IN (0,1)),
  CHECK(result_id IS NULL OR session_id IS NOT NULL),
  CHECK((result_id IS NULL AND result_bytes IS NULL AND result_digest IS NULL AND verified_checks_bytes IS NULL) OR
        (result_id IS NOT NULL AND typeof(result_bytes)='blob' AND result_digest IS NOT NULL AND ((engine_version=1 AND typeof(verified_checks_bytes)='blob' AND generic_checks_bytes IS NULL) OR
         (engine_version=2 AND verified_checks_bytes IS NULL AND typeof(generic_checks_bytes)='blob')))),
  CHECK(selected=0 OR state IN ('queued','running','waiting') OR (engine_version=1 AND state='held')),
  FOREIGN KEY(run_id,engine_version) REFERENCES workflow_seq_runs(id,engine_version),
  CHECK((engine_version=1 AND epoch IS NOT NULL) OR (engine_version=2 AND epoch IS NULL)),
  CHECK((engine_version=1 AND assignment_id IS NULL AND assignment_generation IS NULL AND generic_checks_bytes IS NULL) OR
        (engine_version=2 AND assignment_id IS NOT NULL AND typeof(assignment_generation)='integer' AND assignment_generation>0)),
  FOREIGN KEY(assignment_id,run_id) REFERENCES workflow_seq_assignments(id,run_id),
  UNIQUE(run_id,ordinal),
  UNIQUE(run_id,predecessor_event_id),
  UNIQUE(id,run_id),
  UNIQUE(id,run_id,selected)
);
CREATE UNIQUE INDEX workflow_seq_one_selected_visit ON workflow_seq_visits(run_id) WHERE selected=1;
CREATE TABLE workflow_seq_outputs (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  engine_version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(engine_version)='integer' AND engine_version IN (1,2)),
  visit_id TEXT NOT NULL,
  slot TEXT NOT NULL,
  epoch INTEGER CHECK(epoch IS NULL OR (typeof(epoch)='integer' AND epoch BETWEEN 1 AND 3)),
  contract TEXT NOT NULL,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  session_id TEXT NOT NULL,
  result_id INTEGER NOT NULL REFERENCES task_results(id) CHECK(typeof(result_id)='integer' AND result_id>0),
  content_bytes BLOB NOT NULL CHECK(typeof(content_bytes)='blob'),
  content_digest TEXT NOT NULL,
  recipients_bytes BLOB NOT NULL CHECK(typeof(recipients_bytes)='blob'),
  FOREIGN KEY(visit_id,run_id) REFERENCES workflow_seq_visits(id,run_id),
  FOREIGN KEY(run_id,engine_version) REFERENCES workflow_seq_runs(id,engine_version),
  CHECK((engine_version=1 AND epoch IS NOT NULL) OR (engine_version=2 AND epoch IS NULL)),
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
  engine_version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(engine_version)='integer' AND engine_version IN (1,2)),
  sequence INTEGER NOT NULL CHECK(typeof(sequence)='integer' AND sequence BETWEEN 1 AND 4096),
  operation_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('admitted','transition','cancelled','returned','held','control','failed','cancellation_finished','failure_observed')),
  prior_digest TEXT NOT NULL,
  event_bytes BLOB NOT NULL CHECK(typeof(event_bytes)='blob'),
  event_digest TEXT NOT NULL,
  source_visit_id TEXT,
  source_result_id INTEGER REFERENCES task_results(id)
    CHECK(source_result_id IS NULL OR (typeof(source_result_id)='integer' AND source_result_id>0)),
  FOREIGN KEY(source_visit_id,run_id) REFERENCES workflow_seq_visits(id,run_id),
  CHECK((kind='transition' AND source_visit_id IS NOT NULL AND source_result_id IS NOT NULL) OR
        (kind!='transition' AND ((engine_version=1 AND source_visit_id IS NULL AND source_result_id IS NULL) OR
         (engine_version=2 AND (source_result_id IS NULL OR source_visit_id IS NOT NULL))))),
  FOREIGN KEY(run_id,engine_version) REFERENCES workflow_seq_runs(id,engine_version),
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
  kind TEXT NOT NULL CHECK(kind IN ('visit_queued','run_return','initiator_wake')),
  visit_id TEXT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  notification_bytes BLOB NOT NULL CHECK(typeof(notification_bytes)='blob'),
  notification_digest TEXT NOT NULL,
  FOREIGN KEY(event_id,run_id) REFERENCES workflow_seq_events(id,run_id),
  FOREIGN KEY(visit_id,run_id) REFERENCES workflow_seq_visits(id,run_id),
  CHECK((kind='visit_queued' AND visit_id IS NOT NULL) OR (kind IN ('run_return','initiator_wake') AND visit_id IS NULL)),
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
CREATE TRIGGER workflow_seq_runs_pins_immutable BEFORE UPDATE OF id,org_slug,definition_id,settings_id,source_task_id,source_principal,source_session_id,source_result_id,carrier_task_id,start_operation_key,start_bytes,start_digest,original_limits_bytes,engine_version,assignment_snapshot_bytes,assignment_snapshot_digest ON workflow_seq_runs BEGIN SELECT RAISE(ABORT,'sequential admission is immutable'); END;
CREATE TRIGGER workflow_seq_runs_no_delete BEFORE DELETE ON workflow_seq_runs BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_visits_pins_immutable BEFORE UPDATE OF id,run_id,node_id,ordinal,epoch,predecessor_event_id,task_id,principal,input_selection_bytes,input_selection_digest,engine_version,assignment_id,assignment_generation ON workflow_seq_visits BEGIN SELECT RAISE(ABORT,'sequential visit pins are immutable'); END;
CREATE TRIGGER workflow_seq_visits_no_delete BEFORE DELETE ON workflow_seq_visits BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_visits_selected_insert BEFORE INSERT ON workflow_seq_visits WHEN NEW.engine_version=2 AND NEW.selected=1 AND NOT EXISTS (SELECT 1 FROM workflow_seq_runs WHERE id=NEW.run_id AND engine_version=2 AND state='running' AND active_visit_id=NEW.id) BEGIN SELECT RAISE(ABORT,'sequential outstanding visit is not current'); END;
CREATE TRIGGER workflow_seq_visits_selected_update BEFORE UPDATE ON workflow_seq_visits WHEN NEW.engine_version=2 AND NEW.selected=1 AND NOT EXISTS (SELECT 1 FROM workflow_seq_runs WHERE id=NEW.run_id AND engine_version=2 AND state='running' AND active_visit_id=NEW.id) BEGIN SELECT RAISE(ABORT,'sequential outstanding visit is not current'); END;
CREATE TRIGGER workflow_seq_runs_stopped_unselected BEFORE UPDATE ON workflow_seq_runs WHEN NEW.engine_version=2 AND NEW.state!='running' AND EXISTS (SELECT 1 FROM workflow_seq_visits WHERE run_id=NEW.id AND selected=1) BEGIN SELECT RAISE(ABORT,'sequential stopped run has outstanding visit'); END;
CREATE TRIGGER workflow_seq_visits_result_immutable BEFORE UPDATE ON workflow_seq_visits WHEN OLD.result_id IS NOT NULL AND (NEW.result_id IS NOT OLD.result_id OR NEW.session_id IS NOT OLD.session_id OR NEW.result_bytes IS NOT OLD.result_bytes OR NEW.result_digest IS NOT OLD.result_digest OR NEW.verified_checks_bytes IS NOT OLD.verified_checks_bytes OR NEW.generic_checks_bytes IS NOT OLD.generic_checks_bytes) BEGIN SELECT RAISE(ABORT,'sequential result is immutable'); END;
CREATE TABLE workflow_seq_assignments (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  node_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation BETWEEN 1 AND 64),
  principal TEXT NOT NULL,
  snapshot_bytes BLOB NOT NULL CHECK(typeof(snapshot_bytes)='blob'),
  snapshot_digest TEXT NOT NULL,
  predecessor_id TEXT,
  FOREIGN KEY(predecessor_id,run_id) REFERENCES workflow_seq_assignments(id,run_id),
  UNIQUE(run_id,node_id,generation),
  UNIQUE(id,run_id)
);
CREATE TABLE workflow_seq_node_counters (
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  node_id TEXT NOT NULL,
  visits INTEGER NOT NULL CHECK(typeof(visits)='integer' AND visits BETWEEN 0 AND 64),
  revisions INTEGER NOT NULL CHECK(typeof(revisions)='integer' AND revisions BETWEEN 0 AND 64),
  PRIMARY KEY(run_id,node_id)
);
CREATE TABLE workflow_seq_controls (
  id TEXT NOT NULL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  operation_key TEXT NOT NULL,
  actor_task_id TEXT NOT NULL REFERENCES tasks(id),
  actor_principal TEXT NOT NULL,
  actor_session_id TEXT NOT NULL,
  actor_result_id INTEGER NOT NULL REFERENCES task_results(id) CHECK(typeof(actor_result_id)='integer' AND actor_result_id>0),
  expected_cas INTEGER NOT NULL CHECK(typeof(expected_cas)='integer' AND expected_cas>=0),
  expected_held_event_id TEXT,
  action TEXT NOT NULL CHECK(action IN ('retry','rebrief','reassign','cancel')),
  request_bytes BLOB NOT NULL CHECK(typeof(request_bytes)='blob'),
  request_digest TEXT NOT NULL,
  event_id TEXT NOT NULL,
  new_visit_id TEXT,
  receipt_bytes BLOB NOT NULL CHECK(typeof(receipt_bytes)='blob'),
  receipt_digest TEXT NOT NULL,
  FOREIGN KEY(expected_held_event_id,run_id) REFERENCES workflow_seq_events(id,run_id),
  FOREIGN KEY(event_id,run_id) REFERENCES workflow_seq_events(id,run_id),
  FOREIGN KEY(new_visit_id,run_id) REFERENCES workflow_seq_visits(id,run_id),
  UNIQUE(run_id,operation_key)
);
CREATE TABLE workflow_seq_source_wait_sets (
  id TEXT NOT NULL PRIMARY KEY,
  org_slug TEXT NOT NULL,
  source_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  source_principal TEXT NOT NULL,
  UNIQUE(id,source_task_id)
);
CREATE TABLE workflow_seq_source_memberships (
  wait_set_id TEXT NOT NULL,
  source_task_id TEXT NOT NULL REFERENCES tasks(id),
  run_id TEXT NOT NULL UNIQUE,
  source_result_id INTEGER NOT NULL REFERENCES task_results(id) CHECK(typeof(source_result_id)='integer' AND source_result_id>0),
  start_operation_key TEXT NOT NULL,
  FOREIGN KEY(wait_set_id,source_task_id) REFERENCES workflow_seq_source_wait_sets(id,source_task_id),
  FOREIGN KEY(run_id,source_task_id,source_result_id,start_operation_key)
    REFERENCES workflow_seq_runs(id,source_task_id,source_result_id,start_operation_key),
  PRIMARY KEY(wait_set_id,run_id)
);
CREATE INDEX workflow_seq_memberships_source_result ON workflow_seq_source_memberships(source_task_id,source_result_id);
CREATE INDEX workflow_seq_runs_source_result ON workflow_seq_runs(source_task_id,source_result_id);
CREATE TABLE workflow_seq_inbox (
  initiator_task_id TEXT NOT NULL REFERENCES tasks(id),
  notification_id TEXT NOT NULL,
  initiator_principal TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES workflow_seq_runs(id),
  event_id TEXT NOT NULL,
  notification_bytes BLOB NOT NULL CHECK(typeof(notification_bytes)='blob' AND length(notification_bytes)<=16384),
  notification_digest TEXT NOT NULL,
  FOREIGN KEY(event_id,run_id) REFERENCES workflow_seq_events(id,run_id),
  FOREIGN KEY(run_id,initiator_task_id,initiator_principal) REFERENCES workflow_seq_runs(id,source_task_id,source_principal),
  PRIMARY KEY(initiator_task_id,notification_id),
  UNIQUE(event_id)
);
CREATE TABLE workflow_seq_inbox_deliveries (
  initiator_task_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  notification_id TEXT NOT NULL,
  FOREIGN KEY(initiator_task_id,notification_id) REFERENCES workflow_seq_inbox(initiator_task_id,notification_id),
  PRIMARY KEY(initiator_task_id,session_id,notification_id)
);
CREATE TABLE workflow_seq_consumption_receipts (
  id TEXT NOT NULL PRIMARY KEY,
  initiator_task_id TEXT NOT NULL REFERENCES tasks(id),
  principal TEXT NOT NULL,
  session_id TEXT NOT NULL,
  result_id INTEGER NOT NULL UNIQUE REFERENCES task_results(id) CHECK(typeof(result_id)='integer' AND result_id>0),
  decision_bytes BLOB NOT NULL CHECK(typeof(decision_bytes)='blob'),
  decision_digest TEXT NOT NULL,
  receipt_bytes BLOB NOT NULL CHECK(typeof(receipt_bytes)='blob'),
  receipt_digest TEXT NOT NULL,
  UNIQUE(id,initiator_task_id,session_id)
);
CREATE TABLE workflow_seq_consumed_notifications (
  initiator_task_id TEXT NOT NULL,
  notification_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  FOREIGN KEY(initiator_task_id,session_id,notification_id)
    REFERENCES workflow_seq_inbox_deliveries(initiator_task_id,session_id,notification_id),
  FOREIGN KEY(receipt_id,initiator_task_id,session_id)
    REFERENCES workflow_seq_consumption_receipts(id,initiator_task_id,session_id),
  PRIMARY KEY(initiator_task_id,notification_id)
);
CREATE TRIGGER workflow_seq_assignments_no_update BEFORE UPDATE ON workflow_seq_assignments BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_assignments_no_delete BEFORE DELETE ON workflow_seq_assignments BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_node_counters_no_delete BEFORE DELETE ON workflow_seq_node_counters BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_controls_no_update BEFORE UPDATE ON workflow_seq_controls BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_controls_no_delete BEFORE DELETE ON workflow_seq_controls BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_source_wait_sets_no_update BEFORE UPDATE ON workflow_seq_source_wait_sets BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_source_wait_sets_no_delete BEFORE DELETE ON workflow_seq_source_wait_sets BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_source_memberships_no_update BEFORE UPDATE ON workflow_seq_source_memberships BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_source_memberships_no_delete BEFORE DELETE ON workflow_seq_source_memberships BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_inbox_no_update BEFORE UPDATE ON workflow_seq_inbox BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_inbox_no_delete BEFORE DELETE ON workflow_seq_inbox BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_inbox_deliveries_no_update BEFORE UPDATE ON workflow_seq_inbox_deliveries BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_inbox_deliveries_no_delete BEFORE DELETE ON workflow_seq_inbox_deliveries BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_consumption_receipts_no_update BEFORE UPDATE ON workflow_seq_consumption_receipts BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_consumption_receipts_no_delete BEFORE DELETE ON workflow_seq_consumption_receipts BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_consumed_notifications_no_update BEFORE UPDATE ON workflow_seq_consumed_notifications BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_consumed_notifications_no_delete BEFORE DELETE ON workflow_seq_consumed_notifications BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;
CREATE TRIGGER workflow_seq_node_counters_pins_immutable BEFORE UPDATE OF run_id,node_id ON workflow_seq_node_counters BEGIN SELECT RAISE(ABORT,'sequential counter pins are immutable'); END;
CREATE TRIGGER workflow_seq_node_counters_monotonic BEFORE UPDATE ON workflow_seq_node_counters WHEN NEW.visits<OLD.visits OR NEW.revisions<OLD.revisions BEGIN SELECT RAISE(ABORT,'sequential counters cannot reset'); END;
"""


def sequential_ddl(version: int) -> str:
    """Finite reviewed reference selection, never candidate-derived DDL."""
    if type(version) is not int or version not in (1, 2):
        raise ValueError('workflow_seq_schema_version_unsupported')
    return CANONICAL_WORKFLOW_SEQUENTIAL_DDL if version == 1 else CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL


def sequential_tables(version: int) -> tuple[str, ...]:
    sequential_ddl(version)
    return SEQUENTIAL_TABLES if version == 1 else SEQUENTIAL_V2_TABLES


# The zero-history upgrade retains these three tables and all their original
# SQL/rowids/bytes. Only reviewed literal statements are selected here.
SEQUENTIAL_RETAINED_TABLES = ('workflow_seq_definitions', 'workflow_seq_settings', 'workflow_seq_settings_operations')
SEQUENTIAL_V2_REBUILT_TABLES = ('workflow_seq_schema_versions', 'workflow_seq_runs', 'workflow_seq_visits',
                                'workflow_seq_outputs', 'workflow_seq_events', 'workflow_seq_outbox')
SEQUENTIAL_V2_UPGRADE_DDL = CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL.replace(
    CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL[
        CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL.index('CREATE TABLE workflow_seq_definitions'):
        CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL.index('CREATE TABLE workflow_seq_runs')], '')
SEQUENTIAL_V2_UPGRADE_DDL = SEQUENTIAL_V2_UPGRADE_DDL.replace(
    CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL[
        CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL.index('CREATE TABLE workflow_seq_evidence'):
        CANONICAL_WORKFLOW_SEQUENTIAL_V2_DDL.index('CREATE TABLE workflow_seq_events')], '')
for _table in (*SEQUENTIAL_RETAINED_TABLES, 'workflow_seq_evidence'):
    for _action in ('update', 'delete'):
        _statement = f"CREATE TRIGGER {_table}_no_{_action} BEFORE {_action.upper()} ON {_table} BEGIN SELECT RAISE(ABORT,'sequential history is immutable'); END;\n"
        SEQUENTIAL_V2_UPGRADE_DDL = SEQUENTIAL_V2_UPGRADE_DDL.replace(_statement, '')
