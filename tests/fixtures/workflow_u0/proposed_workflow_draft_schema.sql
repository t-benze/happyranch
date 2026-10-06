CREATE TABLE workflow_draft_adapter_versions (
  version INTEGER PRIMARY KEY CHECK(version=1)
);
CREATE TABLE workflow_draft_dispatch_intents (
  id TEXT NOT NULL PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES workflow_instances(id),
  activation_id TEXT NOT NULL REFERENCES workflow_activations(id),
  activation_revision INTEGER NOT NULL CHECK(activation_revision>0),
  attempt_sequence INTEGER NOT NULL CHECK(attempt_sequence>0),
  predecessor_intent_id TEXT,
  admission_kind TEXT NOT NULL
    CHECK(admission_kind IN ('initial','retry','reassignment','reactivation')),
  admission_principal TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  request_bytes BLOB NOT NULL CHECK(length(request_bytes)>0),
  request_digest TEXT NOT NULL,
  task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  context_id TEXT NOT NULL REFERENCES workflow_contexts(id),
  binding_snapshot_id TEXT NOT NULL REFERENCES workflow_binding_snapshots(id),
  assigned_principal TEXT NOT NULL,
  assignment_generation INTEGER NOT NULL CHECK(assignment_generation>0),
  authority_namespace TEXT NOT NULL,
  authority_generation INTEGER NOT NULL CHECK(authority_generation>0),
  authority_digest TEXT NOT NULL,
  task_scope_bytes BLOB NOT NULL CHECK(length(task_scope_bytes)>0),
  task_scope_digest TEXT NOT NULL,
  effect_key TEXT NOT NULL UNIQUE,
  host_execution_key TEXT NOT NULL UNIQUE,
  is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
  state TEXT NOT NULL
    CHECK(state IN ('queued','claimed','running','uncertain','cancelled','failed','completed')),
  cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancellation_requested IN (0,1)),
  claim_token TEXT,
  claim_owner TEXT,
  host_launch_started INTEGER NOT NULL DEFAULT 0 CHECK(host_launch_started IN (0,1)),
  host_execution_id TEXT,
  session_id TEXT,
  final_result_id INTEGER REFERENCES task_results(id),
  recovery_owner TEXT NOT NULL CHECK(recovery_owner='workflow_recovery'),
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(instance_id,attempt_sequence),
  UNIQUE(instance_id,assignment_generation),
  UNIQUE(id,instance_id),
  UNIQUE(instance_id,admission_principal,operation_key),
  FOREIGN KEY(predecessor_intent_id,instance_id)
    REFERENCES workflow_draft_dispatch_intents(id,instance_id),
  CHECK(predecessor_intent_id IS NULL OR predecessor_intent_id!=id),
  CHECK((attempt_sequence=1 AND predecessor_intent_id IS NULL
         AND admission_kind='initial' AND assignment_generation=1 AND activation_revision=1)
     OR (attempt_sequence>1 AND predecessor_intent_id IS NOT NULL AND admission_kind!='initial')),
  CHECK(is_current=1 OR state IN ('cancelled','failed','completed')),
  CHECK(state NOT IN ('claimed','running','uncertain')
        OR (claim_token IS NOT NULL AND claim_owner IS NOT NULL)),
  CHECK(state!='queued' OR (host_launch_started=0 AND host_execution_id IS NULL AND session_id IS NULL)),
  CHECK(state NOT IN ('running','completed')
        OR (host_launch_started=1 AND host_execution_id IS NOT NULL AND session_id IS NOT NULL)),
  CHECK(state!='completed' OR final_result_id IS NOT NULL),
  CHECK(final_result_id IS NULL OR session_id IS NOT NULL)
);
CREATE UNIQUE INDEX workflow_draft_current_idx
  ON workflow_draft_dispatch_intents(instance_id) WHERE is_current=1;
CREATE UNIQUE INDEX workflow_draft_predecessor_idx
  ON workflow_draft_dispatch_intents(predecessor_intent_id)
  WHERE predecessor_intent_id IS NOT NULL;
CREATE UNIQUE INDEX workflow_draft_session_idx
  ON workflow_draft_dispatch_intents(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX workflow_draft_dispatch_state_idx
  ON workflow_draft_dispatch_intents(state,recovery_owner,is_current);

CREATE TABLE workflow_draft_dispatch_events (
  id TEXT NOT NULL PRIMARY KEY,
  intent_id TEXT NOT NULL REFERENCES workflow_draft_dispatch_intents(id),
  event_seq INTEGER NOT NULL CHECK(event_seq>0),
  event_kind TEXT NOT NULL CHECK(event_kind IN
    ('admitted','claimed','requeued','launch_reserved','running','uncertain',
     'host_reconciled','cancel_requested','cancelled','failed','completed',
     'retired','callback_recorded','callback_rejected')),
  state_before TEXT CHECK(state_before IN
    ('queued','claimed','running','uncertain','cancelled','failed','completed')),
  state_after TEXT NOT NULL CHECK(state_after IN
    ('queued','claimed','running','uncertain','cancelled','failed','completed')),
  event_bytes BLOB NOT NULL CHECK(length(event_bytes)>0),
  event_digest TEXT NOT NULL,
  session_id TEXT,
  result_id INTEGER REFERENCES task_results(id),
  result_digest TEXT,
  callback_accepted INTEGER CHECK(callback_accepted IN (0,1)),
  disposition TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(intent_id,event_seq),
  UNIQUE(intent_id,event_digest),
  CHECK((result_id IS NULL AND result_digest IS NULL AND callback_accepted IS NULL)
     OR (result_id IS NOT NULL AND session_id IS NOT NULL AND result_digest IS NOT NULL
         AND callback_accepted IS NOT NULL AND disposition IS NOT NULL)),
  CHECK(event_kind!='callback_recorded' OR (result_id IS NOT NULL AND callback_accepted=1)),
  CHECK(event_seq!=1 OR (event_kind='admitted' AND state_before IS NULL AND state_after='queued')),
  CHECK(event_seq=1 OR state_before IS NOT NULL)
);
CREATE UNIQUE INDEX workflow_draft_result_idx
  ON workflow_draft_dispatch_events(result_id) WHERE result_id IS NOT NULL;
CREATE INDEX workflow_draft_activation_idx
  ON workflow_draft_dispatch_intents(activation_id,attempt_sequence);
