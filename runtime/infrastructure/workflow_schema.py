"""Installed U1A workflow schema and compatibility boundary.

F is the unchanged inert version-one foundation. Existing OrgState.load installs
only F and validates complete F/E; explicit new-org creation initializes E. The
operator script alone upgrades existing F. Generic Database construction,
including runtime-audit.db, has no workflow side effect.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from runtime.infrastructure.database import Database


CANONICAL_WORKFLOW_DDL = """\
-- TASK-8849 U1A: canonical inert version-1 workflow-owned layout.
PRAGMA foreign_keys=ON;
-- This is the adapter's sole durable version discriminator.  It is created
-- and committed in the same workflow-owned transaction as every table.
CREATE TABLE workflow_adapter_versions (version INTEGER PRIMARY KEY CHECK(version=1));
CREATE TABLE workflow_template_drafts (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, template_name TEXT NOT NULL, definition_bytes BLOB NOT NULL, definition_digest TEXT NOT NULL, compiler_pin TEXT NOT NULL, validator_pin TEXT NOT NULL, source_pin TEXT NOT NULL, author_principal TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE workflow_template_versions (id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES workflow_template_drafts(id), namespace TEXT NOT NULL, template_name TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0), definition_bytes BLOB NOT NULL, definition_digest TEXT NOT NULL, compiler_pin TEXT NOT NULL, validator_pin TEXT NOT NULL, source_pin TEXT NOT NULL, published_by TEXT NOT NULL, published_at TEXT NOT NULL, UNIQUE(namespace,template_name,version));
CREATE TABLE workflow_authorization_revisions (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0), authority_bytes BLOB NOT NULL, authority_digest TEXT NOT NULL UNIQUE, source_pin TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(namespace,revision));
CREATE TABLE workflow_active_authorizations (namespace TEXT PRIMARY KEY, authorization_revision_id TEXT NOT NULL REFERENCES workflow_authorization_revisions(id));
CREATE TABLE workflow_binding_snapshots (id TEXT PRIMARY KEY, template_version_id TEXT NOT NULL REFERENCES workflow_template_versions(id), authorization_revision_id TEXT NOT NULL REFERENCES workflow_authorization_revisions(id), binding_bytes BLOB NOT NULL, binding_digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE workflow_contexts (id TEXT PRIMARY KEY, binding_snapshot_id TEXT NOT NULL REFERENCES workflow_binding_snapshots(id), context_bytes BLOB NOT NULL, context_digest TEXT NOT NULL UNIQUE, source_kind TEXT NOT NULL, source_id TEXT NOT NULL);
CREATE TABLE workflow_instances (id TEXT PRIMARY KEY, binding_snapshot_id TEXT NOT NULL REFERENCES workflow_binding_snapshots(id), context_id TEXT NOT NULL REFERENCES workflow_contexts(id), root_task_id TEXT NOT NULL UNIQUE, owner_principal TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','reviewing','complete','cancelled')));
CREATE TABLE workflow_instance_tasks (instance_id TEXT NOT NULL REFERENCES workflow_instances(id), task_id TEXT NOT NULL, session_id TEXT NOT NULL, role_key TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0), state TEXT NOT NULL, PRIMARY KEY(instance_id,task_id), UNIQUE(instance_id,task_id,session_id), UNIQUE(instance_id,role_key,generation));
CREATE TABLE workflow_events (id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES workflow_instances(id), event_kind TEXT NOT NULL CHECK(event_kind IN ('submitted','joined')), event_bytes BLOB NOT NULL, event_digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, UNIQUE(instance_id,event_kind));
CREATE TABLE workflow_submissions (id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES workflow_instances(id), revision INTEGER NOT NULL CHECK(revision>0), submission_bytes BLOB NOT NULL, submission_digest TEXT NOT NULL UNIQUE, storage_ref TEXT, source_task_id TEXT NOT NULL, source_session_id TEXT NOT NULL, source_result_id TEXT NOT NULL, author_principal TEXT NOT NULL, UNIQUE(instance_id,revision), UNIQUE(id,submission_digest), CHECK(storage_ref IS NOT NULL OR length(submission_bytes)>0));
CREATE TABLE workflow_submission_contributors (submission_id TEXT NOT NULL REFERENCES workflow_submissions(id), principal TEXT NOT NULL, source_task_id TEXT NOT NULL, source_session_id TEXT NOT NULL, source_result_id TEXT NOT NULL, contribution_kind TEXT NOT NULL, PRIMARY KEY(submission_id,principal,source_task_id,source_session_id,source_result_id));
-- The service checks this instance-wide closure; SQL alone cannot establish
-- current request, digest, authority, or lifecycle semantics.
CREATE TABLE workflow_instance_contributors (instance_id TEXT NOT NULL REFERENCES workflow_instances(id), principal TEXT NOT NULL, source_task_id TEXT NOT NULL, source_session_id TEXT NOT NULL, source_result_id TEXT NOT NULL, contribution_kind TEXT NOT NULL, PRIMARY KEY(instance_id,principal,source_task_id,source_session_id,source_result_id));
CREATE TABLE workflow_rounds (id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES workflow_instances(id), submission_id TEXT NOT NULL REFERENCES workflow_submissions(id), current_revision INTEGER NOT NULL CHECK(current_revision>0), state TEXT NOT NULL CHECK(state IN ('reviewing','closed','superseded')), UNIQUE(instance_id,current_revision));
CREATE TABLE workflow_review_requests (id TEXT PRIMARY KEY, round_id TEXT NOT NULL REFERENCES workflow_rounds(id), principal TEXT NOT NULL, assignment_generation INTEGER NOT NULL CHECK(assignment_generation>0), request_scope_bytes BLOB NOT NULL, request_scope_digest TEXT NOT NULL UNIQUE, status TEXT NOT NULL CHECK(status IN ('pending','approved','changes_requested','superseded')), supersedes_request_id TEXT REFERENCES workflow_review_requests(id), UNIQUE(round_id,principal,assignment_generation));
CREATE TABLE workflow_review_receipts (id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES workflow_review_requests(id), submission_id TEXT NOT NULL, submission_digest TEXT NOT NULL, assignment_generation INTEGER NOT NULL CHECK(assignment_generation>0), request_scope_digest TEXT NOT NULL, proof_bytes BLOB NOT NULL, proof_digest TEXT NOT NULL UNIQUE, outcome TEXT NOT NULL CHECK(outcome IN ('approved','changes_requested')), supersedes_receipt_id TEXT REFERENCES workflow_review_receipts(id), created_at TEXT NOT NULL, FOREIGN KEY(submission_id,submission_digest) REFERENCES workflow_submissions(id,submission_digest), UNIQUE(request_id,assignment_generation));
-- These three inert relations reserve the F2 provenance bridge for a later
-- unit. U1A installs no publication/outbox behavior.
CREATE TABLE workflow_task_results (id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, task_id TEXT NOT NULL, session_id TEXT NOT NULL, principal TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0), lifecycle TEXT NOT NULL CHECK(lifecycle='completed'), result_bytes BLOB NOT NULL, result_digest TEXT NOT NULL, FOREIGN KEY(instance_id,task_id,session_id) REFERENCES workflow_instance_tasks(instance_id,task_id,session_id), UNIQUE(instance_id,task_id,session_id,id));
CREATE TABLE workflow_current_assignments (instance_id TEXT NOT NULL REFERENCES workflow_instances(id), role_key TEXT NOT NULL, principal TEXT NOT NULL, task_id TEXT NOT NULL, session_id TEXT NOT NULL, result_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0), lifecycle TEXT NOT NULL CHECK(lifecycle='completed'), PRIMARY KEY(instance_id,role_key), FOREIGN KEY(instance_id,task_id,session_id,result_id) REFERENCES workflow_task_results(instance_id,task_id,session_id,id));
-- Evidence is separate from the receipt. SQL binds durable result/context/
-- binding identities; the owned finalizer checks the current cross-row meaning.
CREATE TABLE workflow_receipt_evidence (receipt_id TEXT PRIMARY KEY REFERENCES workflow_review_receipts(id), signer_principal TEXT NOT NULL, task_id TEXT NOT NULL, session_id TEXT NOT NULL, result_id TEXT NOT NULL REFERENCES workflow_task_results(id), context_id TEXT NOT NULL REFERENCES workflow_contexts(id), binding_snapshot_id TEXT NOT NULL REFERENCES workflow_binding_snapshots(id), round_revision INTEGER NOT NULL CHECK(round_revision>0), proof_bytes BLOB NOT NULL, proof_digest TEXT NOT NULL UNIQUE);
CREATE TABLE workflow_operation_replays (org_slug TEXT NOT NULL, principal TEXT NOT NULL, operation_key TEXT NOT NULL, request_digest TEXT NOT NULL, instance_id TEXT NOT NULL REFERENCES workflow_instances(id), effect_id TEXT NOT NULL REFERENCES workflow_events(id), PRIMARY KEY(org_slug,principal,operation_key));
CREATE INDEX workflow_instances_root_idx ON workflow_instances(root_task_id);
CREATE INDEX workflow_instance_tasks_state_idx ON workflow_instance_tasks(instance_id,state);
CREATE INDEX workflow_events_instance_idx ON workflow_events(instance_id);
CREATE INDEX workflow_requests_round_idx ON workflow_review_requests(round_id,status);
-- Inert F4/F5 authority-publication layout. U1A installs no coordinator.
CREATE TABLE workflow_authority_pointers (namespace TEXT PRIMARY KEY, current_generation INTEGER NOT NULL CHECK(current_generation>=0), journal_id TEXT, snapshot_digest TEXT, state TEXT NOT NULL CHECK(state IN ('ready','fenced')), profile_fence INTEGER NOT NULL DEFAULT 0 CHECK(profile_fence>=0), CHECK((current_generation=0 AND journal_id IS NULL AND snapshot_digest IS NULL) OR (current_generation>0 AND journal_id IS NOT NULL AND snapshot_digest IS NOT NULL)));
CREATE TABLE workflow_publication_journals (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0), expected_generation INTEGER NOT NULL CHECK(expected_generation>=0), snapshot_bytes BLOB NOT NULL, snapshot_digest TEXT NOT NULL, publisher TEXT NOT NULL, publisher_invocation TEXT NOT NULL, profile_fence INTEGER NOT NULL CHECK(profile_fence>=0), state TEXT NOT NULL CHECK(state IN ('prepared','file_phase_reserved','canonical_published','forward_recovery_required','pointer_committed','cache_installed','aborted')), recovery_owner TEXT NOT NULL, file_phase_owner TEXT, CHECK(generation=expected_generation+1), CHECK((state='file_phase_reserved' AND file_phase_owner IS NOT NULL) OR state!='file_phase_reserved'));
CREATE TABLE workflow_publication_leases (namespace TEXT PRIMARY KEY, owner_token TEXT NOT NULL, owner_pid INTEGER NOT NULL CHECK(owner_pid>0));
CREATE TABLE workflow_admission_records (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0), request_digest TEXT NOT NULL, admitted_by TEXT NOT NULL, UNIQUE(namespace,id), FOREIGN KEY(namespace) REFERENCES workflow_authority_pointers(namespace));
CREATE INDEX workflow_publication_journals_namespace_state_idx ON workflow_publication_journals(namespace,state,generation);
CREATE INDEX workflow_admission_records_namespace_generation_idx ON workflow_admission_records(namespace,generation);
-- Inert F4 machine-global profile membership/activation contract. Installing
-- these org-local records is not a coordinator or a claim of distributed
-- atomic commit. The future coordinator is same-host cooperative
-- only: a cross-process lease serializes operations, and any same-UID direct
-- database/file mutation stays outside the guarantee.  The per-organization
-- authority pointer/journal/lease/file/cache relations above remain the only
-- authority store; the coordinator never replaces them.
CREATE TABLE workflow_profile_store (profile_name TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(generation>=0), profile_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('active','removed')));
CREATE TABLE workflow_profile_registry (profile_name TEXT PRIMARY KEY REFERENCES workflow_profile_store(profile_name), published_generation INTEGER NOT NULL CHECK(published_generation>=0));
CREATE TABLE workflow_profile_operations (id TEXT PRIMARY KEY, profile_name TEXT NOT NULL, operation_kind TEXT NOT NULL CHECK(operation_kind IN ('register','rebind','remove')), captured_members TEXT NOT NULL, target_generation INTEGER NOT NULL CHECK(target_generation>0), state TEXT NOT NULL CHECK(state IN ('captured','fenced','store_committed','published','forward_recovery_required','aborted')), profile_digest TEXT NOT NULL, coordinator_invocation TEXT NOT NULL, compensation_generation INTEGER NOT NULL DEFAULT 0 CHECK(compensation_generation>=0), created_at TEXT NOT NULL);
CREATE TABLE workflow_profile_leases (profile_name TEXT PRIMARY KEY, owner_token TEXT NOT NULL, owner_pid INTEGER NOT NULL CHECK(owner_pid>0));
-- Consumer-requirement identity is the tuple
-- (org_namespace, profile_name, consumer_identity): one org may depend on
-- several profiles, several consumers (agents) inside one org may depend on the
-- same profile, and several orgs may depend on one profile.  ``consumer_identity``
-- is the executor/agent identity the requirement belongs to (production resolves
-- it per agent through ``_resolve_executor_name(agent_name)``); two live
-- consumers therefore occupy two rows and one consumer's rebind/removal cannot
-- silently discharge another's requirement.  This is a service constraint, not a
-- DDL one: SQL cannot express cross-row eligibility.
--
-- ``state`` separates requirement presence from binding validity:
--   * ``active``  - the consumer still requires the profile and its binding is
--                   coherent with the store and registry at ``bound_generation``;
--   * ``unbound`` - the consumer still requires the profile, but its binding is
--                   no longer valid (the profile store was removed or moved on).
--                   It is an outstanding requirement that blocks eligibility
--                   until a supported consumer action or a coherent
--                   republication discharges it;
--   * ``removed`` - the consumer explicitly discharged the requirement (rebind
--                   or removal); it is not a requirement.
-- ``bound_generation`` is the generation the consumer's authority was last
-- coherently published against; a removal never rewrites it to hide the loss.
CREATE TABLE workflow_profile_dependencies (org_namespace TEXT NOT NULL, profile_name TEXT NOT NULL, consumer_identity TEXT NOT NULL, bound_generation INTEGER NOT NULL CHECK(bound_generation>=0), state TEXT NOT NULL CHECK(state IN ('active','unbound','removed')), PRIMARY KEY(org_namespace,profile_name,consumer_identity));
CREATE INDEX workflow_profile_operations_profile_state_idx ON workflow_profile_operations(profile_name,state,target_generation);
CREATE INDEX workflow_profile_dependencies_profile_idx ON workflow_profile_dependencies(profile_name,state);
CREATE INDEX workflow_profile_dependencies_org_state_idx ON workflow_profile_dependencies(org_namespace,state);
-- Inert F5 workflow-owned request/task/outbox boundary. U1A does not implement
-- a durable host execution identity or claim exactly-once host launch.
CREATE TABLE workflow_dispatch_operations (id TEXT PRIMARY KEY, org_slug TEXT NOT NULL, principal TEXT NOT NULL, operation_key TEXT NOT NULL, request_digest TEXT NOT NULL, instance_id TEXT NOT NULL REFERENCES workflow_instances(id), round_id TEXT NOT NULL REFERENCES workflow_rounds(id), request_id TEXT NOT NULL UNIQUE REFERENCES workflow_review_requests(id), state TEXT NOT NULL CHECK(state IN ('admitted','cancelled','completed')), created_at TEXT NOT NULL, UNIQUE(org_slug,principal,operation_key));
CREATE TABLE workflow_request_task_bridges (request_id TEXT PRIMARY KEY REFERENCES workflow_review_requests(id), operation_id TEXT NOT NULL UNIQUE REFERENCES workflow_dispatch_operations(id), instance_id TEXT NOT NULL REFERENCES workflow_instances(id), task_id TEXT NOT NULL UNIQUE, assigned_principal TEXT NOT NULL, assignment_generation INTEGER NOT NULL CHECK(assignment_generation>0), session_id TEXT, result_id TEXT, state TEXT NOT NULL CHECK(state IN ('queued','claimed','running','cancelled','uncertain','completed')), created_at TEXT NOT NULL, CHECK((state IN ('running','completed') AND session_id IS NOT NULL) OR state NOT IN ('running','completed')), CHECK((state='completed' AND result_id IS NOT NULL) OR state!='completed'));
CREATE TABLE workflow_dispatch_outbox (id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE REFERENCES workflow_dispatch_operations(id), request_id TEXT NOT NULL UNIQUE REFERENCES workflow_review_requests(id), effect_key TEXT NOT NULL UNIQUE, authority_namespace TEXT NOT NULL, authority_generation INTEGER NOT NULL CHECK(authority_generation>0), authority_digest TEXT NOT NULL, artifact_revision INTEGER NOT NULL CHECK(artifact_revision>0), state TEXT NOT NULL CHECK(state IN ('queued','claimed','running','cancelled','uncertain','completed')), claim_token TEXT, claim_owner TEXT, host_launch_started INTEGER NOT NULL DEFAULT 0 CHECK(host_launch_started IN (0,1)), host_execution_key TEXT NOT NULL UNIQUE, host_execution_id TEXT, recovery_owner TEXT NOT NULL, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, CHECK((state IN ('claimed','running','uncertain') AND claim_token IS NOT NULL) OR state NOT IN ('claimed','running','uncertain')), CHECK((state='running' AND host_execution_id IS NOT NULL) OR state!='running'));
CREATE TABLE workflow_dispatch_events (id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES workflow_dispatch_operations(id), event_seq INTEGER NOT NULL CHECK(event_seq>0), event_kind TEXT NOT NULL, state_before TEXT, state_after TEXT NOT NULL, event_bytes BLOB NOT NULL, event_digest TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(operation_id,event_seq), UNIQUE(operation_id,event_digest));
-- ``result_id`` is globally unique, but identical replay is accepted only after
-- the service joins this row through outbox/request/operation/bridge and matches
-- task, session, artifact revision, assignment generation and reviewer binding.
-- Same-ID reuse across bridges conflicts with zero mutation even when the digest
-- matches; SQL uniqueness alone is not the replay-authorization rule.
CREATE TABLE workflow_dispatch_callbacks (id TEXT PRIMARY KEY, outbox_id TEXT NOT NULL REFERENCES workflow_dispatch_outbox(id), task_id TEXT NOT NULL, session_id TEXT NOT NULL, result_id TEXT NOT NULL UNIQUE, result_digest TEXT NOT NULL, observed_revision INTEGER NOT NULL CHECK(observed_revision>0), accepted INTEGER NOT NULL CHECK(accepted IN (0,1)), disposition TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(outbox_id,task_id,session_id,result_id,result_digest));
CREATE TABLE workflow_dispatch_effects (id TEXT PRIMARY KEY, outbox_id TEXT NOT NULL REFERENCES workflow_dispatch_outbox(id), effect_key TEXT NOT NULL UNIQUE, effect_kind TEXT NOT NULL CHECK(effect_kind='host_launch_observed'), task_id TEXT NOT NULL, session_id TEXT NOT NULL, host_execution_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX workflow_dispatch_outbox_state_idx ON workflow_dispatch_outbox(state,recovery_owner);
CREATE INDEX workflow_dispatch_callbacks_outbox_idx ON workflow_dispatch_callbacks(outbox_id,created_at);
-- U1A compatibility marker plus inert later cutover/template identity layout.
-- The singleton cutover row is the only workflow schema/cutover marker.  It
-- does not reinterpret a legacy table or grant an old binary recovery
-- ownership. Reopen derives the complete canonical table/column/key/CHECK/
-- UNIQUE/FK/index/trigger layout from this exact DDL and rejects any mismatch;
-- names plus marker rows are not sufficient. The state machine is owned by
-- ``workflow_cutover_reconciler``.
CREATE TABLE workflow_cutover_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER NOT NULL CHECK(schema_version=1), state TEXT NOT NULL CHECK(state IN ('installed_legacy_only','enable_requested','compatibility_verified','enabled','disable_requested','draining','drained')), recovery_owner TEXT NOT NULL CHECK(recovery_owner='workflow_cutover_reconciler'), generation INTEGER NOT NULL CHECK(generation>=1), operation_key TEXT, disable_reason TEXT, updated_at TEXT NOT NULL);
CREATE TABLE workflow_cutover_events (id TEXT PRIMARY KEY, event_seq INTEGER NOT NULL UNIQUE CHECK(event_seq>0), state_before TEXT, state_after TEXT NOT NULL, operation_key TEXT, event_digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);

-- Organization-scoped stable identity is ``(namespace, template_name)`` where
-- namespace is exactly ``org/<org>/team/<team>``. Version bodies are immutable
-- and monotonically appended. The existing workflow_template_versions row is
-- the immutable body; this mapping supplies the F6 stable identity without
-- rewriting the earlier F1-F5 fixture rows.
CREATE TABLE workflow_template_identities (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, template_name TEXT NOT NULL, current_version INTEGER NOT NULL CHECK(current_version>=0), status TEXT NOT NULL CHECK(status IN ('active','retired')), created_at TEXT NOT NULL, UNIQUE(namespace,template_name));
CREATE TABLE workflow_template_identity_versions (template_identity_id TEXT NOT NULL REFERENCES workflow_template_identities(id), version INTEGER NOT NULL CHECK(version>0), template_version_id TEXT NOT NULL UNIQUE REFERENCES workflow_template_versions(id), content_digest TEXT NOT NULL, PRIMARY KEY(template_identity_id,version), UNIQUE(template_identity_id,content_digest));
CREATE TABLE workflow_template_publish_operations (org_slug TEXT NOT NULL, principal TEXT NOT NULL, operation_key TEXT NOT NULL, request_digest TEXT NOT NULL, template_identity_id TEXT NOT NULL REFERENCES workflow_template_identities(id), expected_current_version INTEGER NOT NULL CHECK(expected_current_version>=0), result_version INTEGER NOT NULL CHECK(result_version>0), template_version_id TEXT NOT NULL REFERENCES workflow_template_versions(id), PRIMARY KEY(org_slug,principal,operation_key));

-- Activation is a separate append-only CAS. Publishing a later template body
-- never updates this pointer. A deliberate reactivation appends a new immutable
-- activation revision and advances the per-instance pointer.
CREATE TABLE workflow_activations (id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES workflow_instances(id), activation_revision INTEGER NOT NULL CHECK(activation_revision>0), template_identity_id TEXT NOT NULL REFERENCES workflow_template_identities(id), template_version_id TEXT NOT NULL REFERENCES workflow_template_versions(id), authority_namespace TEXT NOT NULL, authority_generation INTEGER NOT NULL CHECK(authority_generation>0), authority_digest TEXT NOT NULL, request_digest TEXT NOT NULL, activated_by TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('active','superseded')), created_at TEXT NOT NULL, UNIQUE(instance_id,activation_revision));
CREATE TABLE workflow_active_activations (instance_id TEXT PRIMARY KEY REFERENCES workflow_instances(id), activation_id TEXT NOT NULL UNIQUE REFERENCES workflow_activations(id), activation_revision INTEGER NOT NULL CHECK(activation_revision>0));
CREATE TABLE workflow_activation_operations (org_slug TEXT NOT NULL, principal TEXT NOT NULL, operation_key TEXT NOT NULL, request_digest TEXT NOT NULL, activation_id TEXT NOT NULL REFERENCES workflow_activations(id), PRIMARY KEY(org_slug,principal,operation_key));

-- The bridge is the durable record-class discriminator. Recovery claims are
-- append-only effect ownership: an ordinary task absent from the bridge can be
-- claimed only by ``legacy_recovery``; an exact bridged task only by
-- ``workflow_recovery``. One primary key/effect key prevents dual launch.
CREATE TABLE workflow_recovery_claims (record_id TEXT PRIMARY KEY, record_class TEXT NOT NULL CHECK(record_class IN ('legacy_task','workflow_task')), recovery_owner TEXT NOT NULL CHECK(recovery_owner IN ('legacy_recovery','workflow_recovery')), claim_token TEXT NOT NULL UNIQUE, effect_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL CHECK(state IN ('claimed','effect_recorded')), created_at TEXT NOT NULL);
"""

CANONICAL_WORKFLOW_DRAFT_DDL = """\
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
"""

_INSTALL_EVENT = {
    "event": "adapter_installed",
    "state_before": None,
    "state_after": "installed_legacy_only",
    "schema_version": 1,
}
_INSTALL_EVENT_DIGEST = hashlib.sha256(
    json.dumps(_INSTALL_EVENT, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _execute_ddl(conn: sqlite3.Connection, ddl: str) -> None:
    statement = ""
    for line in ddl.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                conn.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("incomplete_canonical_workflow_schema")


def _layout(conn: sqlite3.Connection) -> tuple[object, ...]:
    objects = tuple(
        tuple(row)
        for row in conn.execute(
            r"""SELECT type,name,tbl_name,sql
               FROM sqlite_schema
               WHERE type IN ('table','index','trigger','view')
                 AND (name LIKE 'workflow\_%' ESCAPE '\'
                      OR tbl_name LIKE 'workflow\_%' ESCAPE '\')
               ORDER BY type,name,tbl_name"""
        )
    )
    normalized_objects = tuple(
        (kind, name, table, None if sql is None else " ".join(str(sql).split()))
        for kind, name, table, sql in objects
    )
    tables = tuple(row[1] for row in normalized_objects if row[0] == "table")
    table_metadata: list[tuple[object, ...]] = []
    index_metadata: list[tuple[object, ...]] = []
    for table in tables:
        quoted_table = str(table).replace('"', '""')
        table_metadata.append(
            (
                table,
                tuple(
                    tuple(row)
                    for row in conn.execute(f'PRAGMA table_xinfo("{quoted_table}")')
                ),
                tuple(
                    tuple(row)
                    for row in conn.execute(
                        f'PRAGMA foreign_key_list("{quoted_table}")'
                    )
                ),
            )
        )
        for index in conn.execute(f'PRAGMA index_list("{quoted_table}")'):
            index_tuple = tuple(index)
            quoted_index = str(index_tuple[1]).replace('"', '""')
            index_metadata.append(
                (
                    table,
                    index_tuple,
                    tuple(
                        tuple(row)
                        for row in conn.execute(
                            f'PRAGMA index_xinfo("{quoted_index}")'
                        )
                    ),
                )
            )
    return normalized_objects, tuple(table_metadata), tuple(index_metadata)


@lru_cache(maxsize=2)
def _canonical_layout(layout: Literal["F", "E"] = "F") -> tuple[object, ...]:
    expected = sqlite3.connect(":memory:")
    try:
        expected.execute("PRAGMA foreign_keys=ON")
        _execute_ddl(expected, CANONICAL_WORKFLOW_DDL)
        if layout == "E":
            _execute_ddl(expected, CANONICAL_WORKFLOW_DRAFT_DDL)
        elif layout != "F":
            raise ValueError("unsupported_workflow_layout")
        return _layout(expected)
    finally:
        expected.close()


def _object_keys(layout: tuple[object, ...]) -> set[tuple[object, ...]]:
    objects = layout[0]
    assert isinstance(objects, tuple)
    return {(row[0], row[1], row[2]) for row in objects}



_CUTOVER_STATES = (
    "installed_legacy_only", "enable_requested", "compatibility_verified",
    "enabled", "disable_requested", "draining", "drained",
)
_CUTOVER_OWNER = "workflow_cutover_reconciler"
_CUTOVER_POLICY = "workflow-cutover-verifier@1"


def _cutover_event_digest(
    event: dict, *, org_slug: str, previous_digest: str,
) -> str:
    """Reconstructible v1 UTF-8 preimage; no extra persisted receipt fields."""
    disabling = event["event_seq"] >= 5
    preimage = {
        "schema_version": 1,
        "recovery_owner": _CUTOVER_OWNER,
        "policy": _CUTOVER_POLICY,
        "org_slug": org_slug,
        "event": {k: event[k] for k in (
            "id", "event_seq", "state_before", "state_after",
            "operation_key", "created_at",
        )},
        "request": {
            "action": "disable" if disabling else "enable",
            "expected_generation": 4 if disabling else 1,
            "principal_id": "founder",
            "proof_kind": "founder_bearer",
        },
        "previous_digest": previous_digest,
    }
    raw = json.dumps(preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_cutover_data(
    conn: sqlite3.Connection, *, expected_org_slug: str | None,
) -> tuple[dict, list[dict]]:
    """Validate the whole one-way lifecycle, never just legal state names."""
    import re

    def refuse() -> None:
        raise ValueError("workflow_schema_marker_mismatch")

    def valid_time(value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0
        except (ValueError, AttributeError):
            return False

    keys = ("schema_version", "state", "recovery_owner", "generation",
            "operation_key", "disable_reason", "updated_at")
    rows = conn.execute("SELECT " + ",".join(keys) + " FROM workflow_cutover_state").fetchall()
    if len(rows) != 1:
        refuse()
    marker = dict(zip(keys, tuple(rows[0]), strict=True))
    generation = marker["generation"]
    if (marker["schema_version"] != 1 or marker["recovery_owner"] != _CUTOVER_OWNER
            or type(generation) is not int or not 1 <= generation <= len(_CUTOVER_STATES)):
        refuse()
    event_keys = ("id", "event_seq", "state_before", "state_after", "operation_key",
                  "event_digest", "created_at")
    events = [dict(zip(event_keys, tuple(row), strict=True)) for row in conn.execute(
        "SELECT " + ",".join(event_keys) + " FROM workflow_cutover_events ORDER BY event_seq"
    )]
    if len(events) != generation or (generation > 1 and not expected_org_slug):
        refuse()
    for seq, event in enumerate(events, 1):
        if (event["id"] != f"cutover-event-{seq}" or event["event_seq"] != seq
                or event["state_before"] != (None if seq == 1 else _CUTOVER_STATES[seq - 2])
                or event["state_after"] != _CUTOVER_STATES[seq - 1]
                or not valid_time(event["created_at"])):
            refuse()
        if seq == 1:
            if event["operation_key"] is not None or event["event_digest"] != _INSTALL_EVENT_DIGEST:
                refuse()
        else:
            key = event["operation_key"]
            if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key) is None:
                refuse()
            request_index = 4 if seq >= 5 else 1
            if key != events[request_index]["operation_key"]:
                refuse()
            if seq >= 5 and key == events[1]["operation_key"]:
                refuse()
            if event["event_digest"] != _cutover_event_digest(
                event, org_slug=expected_org_slug, previous_digest=events[seq - 2]["event_digest"],
            ):
                refuse()
    last = events[-1]
    if (marker["state"] != last["state_after"] or marker["operation_key"] != last["operation_key"]
            or marker["updated_at"] != last["created_at"]
            or marker["disable_reason"] != ("founder_disable_requested" if generation >= 5 else None)):
        refuse()
    return marker, events


def _validate_installed(
    conn: sqlite3.Connection, *, expected_org_slug: str | None = None,
    validate_data: bool = True,
) -> Literal["F", "E"]:
    actual = _layout(conn)
    layout = "E" if any(row[1] == "workflow_draft_adapter_versions" for row in actual[0]) else "F"
    expected = _canonical_layout(layout)
    if _object_keys(actual) != _object_keys(expected):
        raise ValueError("workflow_schema_object_set_mismatch")
    if actual != expected:
        raise ValueError("workflow_schema_layout_mismatch")

    versions = [
        tuple(row)
        for row in conn.execute("SELECT version FROM workflow_adapter_versions")
    ]
    if len(versions) == 1 and versions[0][0] != 1:
        raise ValueError("unsupported_workflow_adapter_version")
    if versions != [(1,)]:
        raise ValueError("workflow_schema_marker_mismatch")

    _validate_cutover_data(conn, expected_org_slug=expected_org_slug)
    if layout == "E":
        draft_versions = [tuple(row) for row in conn.execute("SELECT version FROM workflow_draft_adapter_versions")]
        if draft_versions != [(1,)]:
            raise ValueError("workflow_draft_schema_marker_mismatch")
        if validate_data:
            _validate_draft_data(conn, expected_org_slug=expected_org_slug)
    return layout


class WorkflowCompatibilityStore:
    """Minimal U1A owner for atomic installation and exact-layout reopen."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def install_or_recover(
        self,
        *,
        before_commit: Callable[[], None] | None = None,
        expected_org_slug: str | None = None,
    ) -> Literal["installed_legacy_only", "reopened"]:
        with self._database.workflow_schema_transaction() as conn:
            actual = _layout(conn)
            if actual[0]:
                _validate_installed(conn, expected_org_slug=expected_org_slug)
                return "reopened"

            _install_foundation(conn, expected_org_slug=expected_org_slug)
            if before_commit is not None:
                before_commit()
            return "installed_legacy_only"


def install_or_recover(
    database: Database,
    *,
    before_commit: Callable[[], None] | None = None,
    expected_org_slug: str | None = None,
) -> Literal["installed_legacy_only", "reopened"]:
    """Install or validate the inert v1 layout on an explicit org database."""
    return WorkflowCompatibilityStore(database).install_or_recover(
        before_commit=before_commit, expected_org_slug=expected_org_slug,
    )


def _install_foundation(conn: sqlite3.Connection, *, expected_org_slug: str | None) -> None:
    _execute_ddl(conn, CANONICAL_WORKFLOW_DDL)
    timestamp = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO workflow_adapter_versions VALUES (1)")
    conn.execute(
        "INSERT INTO workflow_cutover_state VALUES "
        "(1,1,\'installed_legacy_only\',\'workflow_cutover_reconciler\',"
        "1,NULL,NULL,?)",
        (timestamp,),
    )
    conn.execute(
        "INSERT INTO workflow_cutover_events VALUES (?,?,?,?,?,?,?)",
        (
            "cutover-event-1",
            1,
            None,
            "installed_legacy_only",
            None,
            _INSTALL_EVENT_DIGEST,
            timestamp,
        ),
    )
    _validate_installed(conn, expected_org_slug=expected_org_slug)


def draft_migration_guidance(*, org_slug: str, runtime_root: str = '<absolute-root>') -> str:
    """Concrete operator remedy; never an authorization to execute it."""
    import shlex
    root = runtime_root if runtime_root == '<absolute-root>' else shlex.quote(runtime_root)
    return (f'python scripts/migrate_workflow_draft_schema.py --runtime-root {root} '
            f'--org {shlex.quote(org_slug)}; retain a compatible reader for every E database')


def _records(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    cursor = conn.execute(sql, args)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, tuple(row), strict=True)) for row in cursor]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _stored_json(raw: object, digest: object, *, code: str = "workflow_source_data_corrupt") -> object:
    if not isinstance(raw, bytes) or not raw:
        raise ValueError(code)
    try:
        value = json.loads(raw)
        if _canonical_bytes(value) != raw or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(code)
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError(code) from exc


def _validate_source_data(conn: sqlite3.Connection) -> None:
    """DB-resident contracts only; historical eligibility is not re-authorized."""
    if ([row[0] for row in conn.execute('PRAGMA integrity_check')] != ['ok']
            or conn.execute('PRAGMA foreign_key_check').fetchone() is not None):
        raise ValueError('workflow_source_data_corrupt')
    for table, column, digest in (
        ('workflow_template_drafts', 'definition_bytes', 'definition_digest'),
        ('workflow_template_versions', 'definition_bytes', 'definition_digest'),
        ('workflow_authorization_revisions', 'authority_bytes', 'authority_digest'),
        ('workflow_binding_snapshots', 'binding_bytes', 'binding_digest'),
        ('workflow_contexts', 'context_bytes', 'context_digest'),
    ):
        for row in _records(conn, f'SELECT * FROM {table}'):
            value = _stored_json(row[column], row[digest])
            if table.startswith('workflow_template_'):
                from runtime.workflows.templates import _validate_definition
                if _validate_definition(value) != row[column]:
                    raise ValueError('workflow_source_data_corrupt')
    for version in _records(conn, 'SELECT * FROM workflow_template_versions'):
        draft = _records(conn, 'SELECT * FROM workflow_template_drafts WHERE id=?', (version['draft_id'],))[0]
        for key in ('namespace', 'template_name', 'definition_bytes', 'definition_digest',
                    'compiler_pin', 'validator_pin', 'source_pin'):
            if version[key] != draft[key]:
                raise ValueError('workflow_source_data_corrupt')
    for activation in _records(conn, 'SELECT * FROM workflow_activations'):
        row = _records(conn, 'SELECT i.id AS instance_id,t.namespace,t.id AS version_id,m.template_identity_id '
                       'FROM workflow_instances i JOIN workflow_template_versions t ON t.id=? '
                       'JOIN workflow_template_identity_versions m ON m.template_version_id=t.id WHERE i.id=?',
                       (activation['template_version_id'], activation['instance_id']))
        if (len(row) != 1 or row[0]['version_id'] != activation['template_version_id']
                or row[0]['namespace'] != activation['authority_namespace']
                or row[0]['template_identity_id'] != activation['template_identity_id']):
            raise ValueError('workflow_source_data_corrupt')
    for pointer in _records(conn, 'SELECT * FROM workflow_active_activations'):
        row = _records(conn, 'SELECT * FROM workflow_activations WHERE id=?', (pointer['activation_id'],))[0]
        if (row['instance_id'] != pointer['instance_id'] or row['activation_revision'] != pointer['activation_revision']
                or row['state'] != 'active'):
            raise ValueError('workflow_source_data_corrupt')


_DRAFT_PROJECTION = ('state', 'is_current', 'cancellation_requested', 'claim_token', 'claim_owner',
                     'host_launch_started', 'host_execution_id', 'session_id', 'final_result_id')
_DRAFT_MUTABLE = set(_DRAFT_PROJECTION) | {'last_error', 'updated_at'}
_DRAFT_EDGES = {
    'admitted': {(None, 'queued')}, 'claimed': {('queued', 'claimed')},
    'requeued': {('claimed', 'queued')}, 'launch_reserved': {('claimed', 'claimed')},
    'running': {('claimed', 'running')},
    'uncertain': {('claimed', 'uncertain'), ('running', 'uncertain')},
    'host_reconciled': {('uncertain', 'running'), ('uncertain', 'cancelled'), ('uncertain', 'failed'), ('uncertain', 'completed')},
    'cancelled': {(state, 'cancelled') for state in ('queued', 'claimed', 'running', 'uncertain')},
    'failed': {(state, 'failed') for state in ('claimed', 'running', 'uncertain')},
    'completed': {('running', 'completed'), ('uncertain', 'completed')},
}


def _validate_draft_data(conn: sqlite3.Connection, *, expected_org_slug: str | None) -> None:
    """Validate every retained SQL-seeded or future-produced attempt and event.

    No writer, dispatcher, settlement or host-evidence producer lives here.
    Event bytes use the accepted workflow-draft-event@1 immutable tuple and
    before/after projection, plus terminal evidence and normalized result.
    """
    def refuse() -> None:
        raise ValueError('workflow_draft_data_corrupt')

    intents = _records(conn, 'SELECT * FROM workflow_draft_dispatch_intents ORDER BY instance_id,attempt_sequence')
    if intents and not expected_org_slug:
        refuse()
    previous_by_instance: dict[str, dict] = {}
    accepted_results: dict[str, dict] = {}
    for intent in intents:
        for key, value in intent.items():
            if isinstance(value, str) and (not value or value.strip() != value):
                refuse()
        request = _stored_json(intent['request_bytes'], intent['request_digest'], code='workflow_draft_data_corrupt')
        scope = _stored_json(intent['task_scope_bytes'], intent['task_scope_digest'], code='workflow_draft_data_corrupt')
        if not isinstance(request, dict) or not isinstance(scope, dict):
            refuse()
        instance = _records(conn, 'SELECT * FROM workflow_instances WHERE id=?', (intent['instance_id'],))
        activation = _records(conn, 'SELECT * FROM workflow_activations WHERE id=?', (intent['activation_id'],))
        task = _records(conn, 'SELECT * FROM tasks WHERE id=?', (intent['task_id'],))
        context = _records(conn, 'SELECT * FROM workflow_contexts WHERE id=?', (intent['context_id'],))
        binding = _records(conn, 'SELECT * FROM workflow_binding_snapshots WHERE id=?', (intent['binding_snapshot_id'],))
        if not all(len(rows) == 1 for rows in (instance, activation, task, context, binding)):
            refuse()
        instance, activation, task, context, binding = instance[0], activation[0], task[0], context[0], binding[0]
        if (activation['instance_id'] != intent['instance_id'] or activation['activation_revision'] != intent['activation_revision']
                or activation['authority_namespace'] != intent['authority_namespace']
                or activation['authority_generation'] != intent['authority_generation']
                or activation['authority_digest'] != intent['authority_digest']
                or binding['template_version_id'] != activation['template_version_id']
                or context['binding_snapshot_id'] != intent['binding_snapshot_id']
                or task['assigned_agent'] != intent['assigned_principal']
                or scope != {'assigned_agent': task['assigned_agent'], 'team': task['team'], 'brief': task['brief']}
                or intent['effect_key'] != f"workflow-initial-draft:{intent['instance_id']}:{intent['attempt_sequence']}"
                or intent['host_execution_key'] != f"workflow-draft-host:{intent['id']}"):
            refuse()
        admission = {key: intent[key] for key in ('instance_id', 'activation_id', 'activation_revision', 'attempt_sequence',
                                                 'predecessor_intent_id', 'admission_principal', 'operation_key', 'request_digest')}
        admission['org_slug'] = expected_org_slug
        if intent['id'] != hashlib.sha256(_canonical_bytes(admission)).hexdigest():
            refuse()
        previous = previous_by_instance.get(intent['instance_id'])
        if previous is None:
            if (intent['attempt_sequence'] != 1 or instance['root_task_id'] != intent['task_id']
                    or instance['binding_snapshot_id'] != intent['binding_snapshot_id']
                    or instance['context_id'] != intent['context_id']):
                refuse()
        elif (intent['attempt_sequence'] != previous['attempt_sequence'] + 1
              or intent['assignment_generation'] != previous['assignment_generation'] + 1
              or intent['predecessor_intent_id'] != previous['id'] or previous['is_current'] != 0
              or previous['state'] not in ('cancelled', 'failed', 'completed')
              or (intent['admission_kind'] == 'retry' and previous['state'] != 'failed')):
            refuse()
        previous_by_instance[intent['instance_id']] = intent
        events = _records(conn, 'SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? ORDER BY event_seq', (intent['id'],))
        if not events or events[0]['created_at'] != intent['created_at']:
            refuse()
        projection = None
        digest = None
        immutable = {key: (value.hex() if isinstance(value, bytes) else value)
                     for key, value in intent.items() if key not in _DRAFT_MUTABLE}
        for seq, event in enumerate(events, 1):
            payload = _stored_json(event['event_bytes'], event['event_digest'], code='workflow_draft_data_corrupt')
            if not isinstance(payload, dict) or set(payload) != {'format', 'org_slug', 'event', 'previous_digest', 'intent', 'before', 'after', 'terminal_evidence', 'result'}:
                refuse()
            event_identity = {key: event[key] for key in ('id', 'event_seq', 'event_kind', 'created_at')}
            before, after = payload['before'], payload['after']
            if (payload['format'] != 'workflow-draft-event@1' or payload['org_slug'] != expected_org_slug
                    or event['event_seq'] != seq or event['id'] != f"{intent['id']}:{seq}"
                    or payload['event'] != event_identity or payload['previous_digest'] != digest
                    or payload['intent'] != immutable or before != projection
                    or not isinstance(after, dict) or set(after) != set(_DRAFT_PROJECTION)
                    or event['state_before'] != (None if before is None else before['state'])
                    or event['state_after'] != after['state']):
                refuse()
            kind = event['event_kind']
            try:
                timestamp = datetime.fromisoformat(event['created_at'])
                if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
                    refuse()
            except (ValueError, TypeError, AttributeError):
                refuse()
            for key in ('is_current', 'cancellation_requested', 'host_launch_started'):
                if type(after[key]) is not int or after[key] not in (0, 1):
                    refuse()
            if (after['state'] not in ('queued', 'claimed', 'running', 'uncertain', 'cancelled', 'failed', 'completed')
                    or (after['is_current'] == 0 and after['state'] not in ('cancelled', 'failed', 'completed'))
                    or (after['state'] in ('claimed', 'running', 'uncertain') and not (after['claim_token'] and after['claim_owner']))
                    or (after['state'] == 'queued' and (after['host_launch_started'] or after['host_execution_id'] or after['session_id']))
                    or (after['state'] in ('running', 'completed') and not (after['host_launch_started'] and after['host_execution_id'] and after['session_id']))
                    or (after['final_result_id'] is not None and (type(after['final_result_id']) is not int or not after['session_id']))):
                refuse()
            edge = (event['state_before'], event['state_after'])
            if kind in ('cancel_requested', 'callback_recorded', 'callback_rejected', 'retired'):
                if before is None or edge[0] != edge[1]:
                    refuse()
            elif edge not in _DRAFT_EDGES.get(kind, set()):
                refuse()
            if seq == 1 and after != dict(state='queued', is_current=1, cancellation_requested=0, claim_token=None,
                                         claim_owner=None, host_launch_started=0, host_execution_id=None, session_id=None, final_result_id=None):
                refuse()
            if before is not None:
                if ((before['host_launch_started'] and not after['host_launch_started'])
                        or (before['cancellation_requested'] and not after['cancellation_requested'])
                        or (before['session_id'] is not None and before['session_id'] != after['session_id'])
                        or (before['final_result_id'] is not None and before['final_result_id'] != after['final_result_id'])
                        or (before['host_execution_id'] is not None and before['host_execution_id'] != after['host_execution_id'])):
                    refuse()
            if before is not None and before['is_current'] != after['is_current'] and kind != 'retired':
                refuse()
            if before is not None and before['final_result_id'] != after['final_result_id'] and kind != 'callback_recorded':
                refuse()
            if kind in ('cancel_requested', 'retired', 'callback_rejected', 'callback_recorded'):
                permitted = {'cancel_requested': {'cancellation_requested'}, 'retired': {'is_current'},
                             'callback_rejected': set(), 'callback_recorded': {'final_result_id', 'session_id'}}[kind]
                if any(before[key] != after[key] for key in _DRAFT_PROJECTION if key not in permitted):
                    refuse()
            if kind == 'retired' and (edge[0] not in ('cancelled', 'failed', 'completed') or after['is_current'] != 0):
                refuse()
            if kind == 'requeued' and (before['host_launch_started'] or before['session_id'] is not None):
                refuse()
            if kind == 'running' and not before['host_launch_started']:
                refuse()
            if kind == 'launch_reserved' and (before['host_launch_started'] or not after['host_launch_started']):
                refuse()
            if kind == 'cancel_requested' and after['cancellation_requested'] != 1:
                refuse()
            if edge[1] in ('cancelled', 'failed', 'completed') and edge[0] != edge[1]:
                # Prelaunch queued cancellation needs no invented host proof.
                if after['host_launch_started']:
                    witness = payload['terminal_evidence']
                    if (not isinstance(witness, dict) or set(witness) != {'host_quiescent'}
                            or witness['host_quiescent'] is not True):
                        refuse()
                if edge[1] in ('failed', 'cancelled') and task['status'] != edge[1]:
                    refuse()
            if ((kind in ('callback_recorded', 'callback_rejected')) != (event['result_id'] is not None)):
                refuse()
            if kind == 'callback_rejected' and event['callback_accepted'] != 0:
                refuse()
            if event['result_id'] is not None:
                results = _records(conn, 'SELECT * FROM task_results WHERE id=?', (event['result_id'],))
                if len(results) != 1:
                    refuse()
                result = results[0]
                result_closure = {'record': result, 'id': event['result_id'], 'digest': event['result_digest'],
                                  'disposition': event['disposition'], 'accepted': event['callback_accepted']}
                if (_canonical_bytes(payload['result']) != _canonical_bytes(result_closure) or hashlib.sha256(_canonical_bytes(result)).hexdigest() != event['result_digest']
                        or result['task_id'] != intent['task_id'] or result['agent'] != intent['assigned_principal']
                        or result['session_id'] != event['session_id'] or event['session_id'] != after['session_id']
                        or conn.execute('SELECT 1 FROM workflow_dispatch_callbacks WHERE result_id=?', (str(event['result_id']),)).fetchone()):
                    refuse()
                if kind == 'callback_recorded':
                    if (event['callback_accepted'] != 1 or after['final_result_id'] != result['id']
                            or intent['id'] in accepted_results or after['cancellation_requested']):
                        refuse()
                    accepted_results[intent['id']] = result_closure
            elif kind not in ('completed', 'host_reconciled', 'retired') and payload['result'] is not None:
                refuse()
            if after['state'] == 'completed':
                result = accepted_results.get(intent['id'])
                if (after['cancellation_requested'] or result is None or result['record']['status'] != 'completed' or result['id'] != after['final_result_id']
                        or payload['result'] != result or task['status'] != 'completed'):
                    refuse()
            projection, digest = after, event['event_digest']
        if projection != {key: intent[key] for key in _DRAFT_PROJECTION} or events[-1]['created_at'] != intent['updated_at']:
            refuse()
    if any(not intent['is_current'] for intent in previous_by_instance.values()):
        refuse()
    if conn.execute('SELECT 1 FROM workflow_draft_dispatch_events e LEFT JOIN workflow_draft_dispatch_intents i ON i.id=e.intent_id WHERE i.id IS NULL').fetchone():
        refuse()


def validate_workflow_schema(conn: sqlite3.Connection, *, expected_org_slug: str | None) -> Literal['F', 'E']:
    """Complete layout/discriminator/history and stored source/data validation."""
    layout = _validate_installed(conn, expected_org_slug=expected_org_slug)
    _validate_source_data(conn)
    return layout


def migrate_draft_schema(conn: sqlite3.Connection, *, expected_org_slug: str) -> Literal['migrated', 'ready']:
    """Explicit operator/fresh-owner primitive; never opens or commits a DB."""
    if not conn.in_transaction or conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('workflow_draft_migration_requires_writer_and_foreign_keys')
    if validate_workflow_schema(conn, expected_org_slug=expected_org_slug) == 'E':
        return 'ready'
    _execute_ddl(conn, CANONICAL_WORKFLOW_DRAFT_DDL)
    conn.execute('INSERT INTO workflow_draft_adapter_versions VALUES (1)')
    validate_workflow_schema(conn, expected_org_slug=expected_org_slug)
    return 'migrated'


def initialize_complete_org_schema(database: Database, *, expected_org_slug: str) -> None:
    """Deliberate creation only; the POST owner proves the skeleton is fresh."""
    with database.workflow_schema_transaction() as conn:
        if _layout(conn)[0]:
            raise ValueError('workflow_fresh_creation_requires_empty_workflow_layout')
        _install_foundation(conn, expected_org_slug=expected_org_slug)
        migrate_draft_schema(conn, expected_org_slug=expected_org_slug)
