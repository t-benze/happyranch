"""Installed U1A workflow schema and compatibility boundary.

F is the unchanged inert version-one foundation. Existing OrgState.load installs
only F and validates complete F/E/G/H; explicit new-org creation initializes H.
The explicit operator scripts alone upgrade existing F/E. Generic Database construction,
including runtime-audit.db, has no workflow side effect.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
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

CANONICAL_WORKFLOW_SUBMISSION_DDL = """\
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
"""


CANONICAL_WORKFLOW_AUTHOR_HANDOFF_DDL = """\
CREATE TABLE workflow_author_handoffs (
  id TEXT NOT NULL PRIMARY KEY CHECK(typeof(id)='text' AND length(id) BETWEEN 1 AND 128),
  intent_id TEXT NOT NULL UNIQUE REFERENCES workflow_draft_dispatch_intents(id),
  instance_id TEXT NOT NULL REFERENCES workflow_instances(id),
  org_slug TEXT NOT NULL CHECK(typeof(org_slug)='text' AND length(org_slug) BETWEEN 1 AND 40),
  principal TEXT NOT NULL CHECK(typeof(principal)='text' AND length(principal) BETWEEN 1 AND 128),
  source_task_id TEXT NOT NULL REFERENCES tasks(id),
  source_session_id TEXT NOT NULL CHECK(typeof(source_session_id)='text' AND length(source_session_id) BETWEEN 1 AND 128),
  operation_key TEXT NOT NULL CHECK(typeof(operation_key)='text' AND length(operation_key) BETWEEN 1 AND 128),
  revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision=1),
  submission_id TEXT NOT NULL UNIQUE CHECK(typeof(submission_id)='text' AND length(submission_id) BETWEEN 1 AND 128),
  candidate_bytes BLOB NOT NULL CHECK(typeof(candidate_bytes)='blob' AND length(candidate_bytes) BETWEEN 1 AND 1048576),
  candidate_digest TEXT NOT NULL CHECK(typeof(candidate_digest)='text' AND length(candidate_digest)=64 AND candidate_digest NOT GLOB '*[^0-9a-f]*'),
  request_bytes BLOB NOT NULL CHECK(typeof(request_bytes)='blob' AND length(request_bytes) BETWEEN 1 AND 2097152),
  request_digest TEXT NOT NULL CHECK(typeof(request_digest)='text' AND length(request_digest)=64 AND request_digest NOT GLOB '*[^0-9a-f]*'),
  admission_bytes BLOB NOT NULL CHECK(typeof(admission_bytes)='blob' AND length(admission_bytes) BETWEEN 1 AND 16777216),
  admission_digest TEXT NOT NULL UNIQUE CHECK(typeof(admission_digest)='text' AND length(admission_digest)=64 AND admission_digest NOT GLOB '*[^0-9a-f]*'),
  created_at TEXT NOT NULL CHECK(typeof(created_at)='text' AND length(created_at)=27),
  UNIQUE(org_slug,principal,operation_key),
  UNIQUE(instance_id,revision)
);
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


@lru_cache(maxsize=4)
def _canonical_layout(layout: Literal["F", "E", "G", "H"] = "F") -> tuple[object, ...]:
    expected = sqlite3.connect(":memory:")
    try:
        expected.execute("PRAGMA foreign_keys=ON")
        _execute_ddl(expected, _g_foundation_ddl() if layout in ("G", "H") else CANONICAL_WORKFLOW_DDL)
        if layout in ("E", "G", "H"):
            _execute_ddl(expected, CANONICAL_WORKFLOW_DRAFT_DDL)
        elif layout != "F":
            raise ValueError("unsupported_workflow_layout")
        if layout in ("G", "H"):
            _execute_ddl(expected, _g_additions_ddl())
        if layout == "H":
            _execute_ddl(expected, CANONICAL_WORKFLOW_AUTHOR_HANDOFF_DDL)
        return _layout(expected)
    finally:
        expected.close()


def _object_keys(layout: tuple[object, ...]) -> set[tuple[object, ...]]:
    objects = layout[0]
    assert isinstance(objects, tuple)
    return {(row[0], row[1], row[2]) for row in objects}


def _g_definitions() -> tuple[str, str, str]:
    """Approved desired definitions, independent of any inspected database."""
    start = CANONICAL_WORKFLOW_SUBMISSION_DDL.index('CREATE TABLE workflow_submissions')
    middle = CANONICAL_WORKFLOW_SUBMISSION_DDL.index('CREATE TABLE workflow_events')
    end = CANONICAL_WORKFLOW_SUBMISSION_DDL.index('-- G-only additive objects.')
    return (CANONICAL_WORKFLOW_SUBMISSION_DDL[start:middle].strip(),
            CANONICAL_WORKFLOW_SUBMISSION_DDL[middle:end].strip(),
            CANONICAL_WORKFLOW_SUBMISSION_DDL[end:])


def _g_foundation_ddl() -> str:
    ddl = CANONICAL_WORKFLOW_DDL
    for table, replacement in zip(('workflow_submissions', 'workflow_events'), _g_definitions()[:2], strict=True):
        start = ddl.index(f'CREATE TABLE {table} (')
        end = ddl.index(';', start) + 1
        ddl = ddl[:start] + replacement + ddl[end:]
    return ddl


def _g_additions_ddl() -> str:
    return _g_definitions()[2]



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
) -> Literal["F", "E", "G", "H"]:
    actual = _layout(conn)
    names = {row[1] for row in actual[0]}
    layout = "H" if "workflow_author_handoffs" in names else ("G" if "workflow_submission_schema_versions" in names else ("E" if "workflow_draft_adapter_versions" in names else "F"))
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
    if layout != 'H' and any(conn.execute(f"SELECT 1 FROM {table} WHERE compiler_pin='workflow-compiler@3' LIMIT 1").fetchone()
                             for table in ('workflow_template_drafts', 'workflow_template_versions')):
        raise ValueError('workflow_author_handoff_schema_required')

    _validate_cutover_data(conn, expected_org_slug=expected_org_slug)
    if layout in ("E", "G", "H"):
        draft_versions = [tuple(row) for row in conn.execute("SELECT version FROM workflow_draft_adapter_versions")]
        if draft_versions != [(1,)]:
            raise ValueError("workflow_draft_schema_marker_mismatch")
        if validate_data:
            _validate_draft_data(conn, expected_org_slug=expected_org_slug)
    if layout in ("G", "H"):
        if [tuple(row) for row in conn.execute("SELECT version FROM workflow_submission_schema_versions")] != [(1,)]:
            raise ValueError("workflow_submission_schema_marker_mismatch")
        if validate_data:
            _validate_submission_data(conn, expected_org_slug=expected_org_slug)
    if layout == "H" and validate_data:
        _validate_author_handoff_data(conn, expected_org_slug=expected_org_slug)
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
                from runtime.workflows.templates import _validate_definition_pins
                if _validate_definition_pins(value, compiler_pin=row['compiler_pin'],
                        validator_pin=row['validator_pin'], source_pin=row['source_pin']) != row[column]:
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


def validate_workflow_schema(conn: sqlite3.Connection, *, expected_org_slug: str | None) -> Literal['F', 'E', 'G', 'H']:
    """Complete layout/discriminator/history and stored source/data validation."""
    layout = _validate_installed(conn, expected_org_slug=expected_org_slug)
    _validate_source_data(conn)
    return layout


def migrate_draft_schema(conn: sqlite3.Connection, *, expected_org_slug: str) -> Literal['migrated', 'ready']:
    """Explicit operator/fresh-owner primitive; never opens or commits a DB."""
    if not conn.in_transaction or conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('workflow_draft_migration_requires_writer_and_foreign_keys')
    if validate_workflow_schema(conn, expected_org_slug=expected_org_slug) in ('E', 'G', 'H'):
        return 'ready'
    _execute_ddl(conn, CANONICAL_WORKFLOW_DRAFT_DDL)
    conn.execute('INSERT INTO workflow_draft_adapter_versions VALUES (1)')
    validate_workflow_schema(conn, expected_org_slug=expected_org_slug)
    return 'migrated'


def initialize_complete_org_schema(database: Database, *, expected_org_slug: str,
                                   layout: Literal["G", "H"] = "G") -> None:
    """Deliberate creation only; the POST owner proves the skeleton is fresh."""
    if layout not in ("G", "H"):
        raise ValueError("unsupported_workflow_layout")
    with database.workflow_schema_transaction() as conn:
        if _layout(conn)[0]:
            raise ValueError('workflow_fresh_creation_requires_empty_workflow_layout')
        _install_foundation(conn, expected_org_slug=expected_org_slug)
        migrate_draft_schema(conn, expected_org_slug=expected_org_slug)
        # Proven empty creation: no inbound work exists and FK enforcement stays on.
        conn.execute("DROP TABLE workflow_events")
        conn.execute("DROP TABLE workflow_submissions")
        _execute_ddl(conn, CANONICAL_WORKFLOW_SUBMISSION_DDL)
        conn.execute("CREATE INDEX workflow_events_instance_idx ON workflow_events(instance_id)")
        conn.execute("INSERT INTO workflow_submission_schema_versions VALUES (1)")
        if layout == "H":
            _execute_ddl(conn, CANONICAL_WORKFLOW_AUTHOR_HANDOFF_DDL)
        validate_workflow_schema(conn, expected_org_slug=expected_org_slug)


def submission_migration_guidance(*, org_slug: str, runtime_root: str = '<absolute-root>') -> str:
    """Existing reader guidance; this grants no permission for live migration."""
    import shlex
    root = runtime_root if runtime_root == '<absolute-root>' else shlex.quote(runtime_root)
    return (f'python scripts/migrate_workflow_submission_schema.py --runtime-root {root} '
            f'--org {shlex.quote(org_slug)}; stop the daemon and reconcile source owners first; '
            'every G database requires a compatible reader')


def _event_revision(conn: sqlite3.Connection, event: dict) -> int:
    """Authenticate an original event without choosing a current/newest round.

    The historical isolated F2 joined preimage is b'joined' + body. Its raw
    operation body may be opaque; only a unique retained, authenticated round
    closure can map that form. A canonical explicit subject is checked against
    every retained relation. Unsupported or ambiguous archival input refuses.
    """
    raw = event['event_bytes']
    kind = event['event_kind']
    code = (f"workflow_legacy_event_unmappable: event={event['id']} "
            f"instance={event['instance_id']}; source owner must restore authentic "
            'submission/round/event/replay archival closure')
    if not isinstance(raw, bytes) or not raw:
        raise ValueError(code)
    ordinary = hashlib.sha256(raw).hexdigest()
    historical_join = kind == 'joined' and hashlib.sha256(b'joined' + raw).hexdigest() == event['event_digest']
    if event['event_digest'] != ordinary and not historical_join:
        raise ValueError(code)
    replays = _records(conn, 'SELECT * FROM workflow_operation_replays WHERE effect_id=?', (event['id'],))
    if any(r['instance_id'] != event['instance_id'] or r['request_digest'] != ordinary for r in replays):
        raise ValueError(code)
    subject = None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and _canonical_bytes(parsed) == raw:
            subject = parsed
    except (ValueError, UnicodeError):
        pass
    if subject is not None and 'event_kind' in subject and subject['event_kind'] != kind:
        raise ValueError(code)
    candidates = []
    for round_ in _records(conn, 'SELECT * FROM workflow_rounds WHERE instance_id=?', (event['instance_id'],)):
        submissions = _records(conn, 'SELECT * FROM workflow_submissions WHERE id=?', (round_['submission_id'],))
        if len(submissions) != 1:
            continue
        submission = submissions[0]
        revision = submission['revision']
        if (submission['instance_id'] != event['instance_id'] or type(revision) is not int
                or revision <= 0 or round_['current_revision'] != revision
                or not isinstance(submission['submission_bytes'], bytes)
                or hashlib.sha256(submission['submission_bytes']).hexdigest() != submission['submission_digest']):
            continue
        if subject is not None:
            identities = {'instance_id': event['instance_id'], 'round_id': round_['id'],
                          'submission_id': submission['id'], 'revision': revision,
                          'submission_digest': submission['submission_digest']}
            if (not any(k in subject for k in ('round_id', 'submission_id'))
                    or any(subject[k] != v or (k == 'revision' and type(subject[k]) is not int)
                           for k, v in identities.items() if k in subject)):
                continue
        elif not historical_join:
            continue
        receipts = _records(conn, 'SELECT r.* FROM workflow_review_receipts r JOIN workflow_review_requests q '
                            'ON q.id=r.request_id WHERE q.round_id=?', (round_['id'],))
        if any(r['submission_id'] != submission['id'] or r['submission_digest'] != submission['submission_digest']
               for r in receipts):
            continue
        receipt_valid = True
        for receipt in receipts:
            requests = _records(conn, 'SELECT * FROM workflow_review_requests WHERE id=?', (receipt['request_id'],))
            if len(requests) != 1:
                receipt_valid = False
                break
            request = requests[0]
            if (not isinstance(receipt['proof_bytes'], bytes)
                    or hashlib.sha256(receipt['proof_bytes']).hexdigest() != receipt['proof_digest']
                    or not isinstance(request['request_scope_bytes'], bytes)
                    or hashlib.sha256(request['request_scope_bytes']).hexdigest() != request['request_scope_digest']
                    or request['request_scope_digest'] != receipt['request_scope_digest']
                    or request['assignment_generation'] != receipt['assignment_generation']
                    or (historical_join and (receipt['outcome'] != 'approved' or request['status'] != 'approved'))):
                receipt_valid = False
                break
        if not receipt_valid:
            continue
        if historical_join and (not replays or len(receipts) != 3):
            continue
        candidates.append((round_['id'], submission['id'], revision))
    if len(candidates) != 1:
        raise ValueError(code + f'; candidates={candidates}')
    return candidates[0][2]


def _validate_submission_data(conn: sqlite3.Connection, *, expected_org_slug: str | None) -> None:
    """G storage validation only; no submission, linking, or finalizer producer."""
    code = 'workflow_submission_data_corrupt'
    operations = {r['submission_id']: r for r in _records(conn, 'SELECT * FROM workflow_submission_operations')}
    for submission in _records(conn, 'SELECT * FROM workflow_submissions'):
        if (type(submission['revision']) is not int or submission['revision'] <= 0
                or not isinstance(submission['submission_bytes'], bytes)
                or hashlib.sha256(submission['submission_bytes']).hexdigest() != submission['submission_digest']):
            raise ValueError(code)
        op = operations.get(submission['id'])
        if op is None:
            if submission['source_result_id'] is None:
                raise ValueError(code)
            continue
        if (submission['source_result_id'] is not None or not expected_org_slug
                or op['org_slug'] != expected_org_slug or op['instance_id'] != submission['instance_id']
                or op['principal'] != submission['author_principal']
                or op['source_task_id'] != submission['source_task_id']
                or op['source_session_id'] != submission['source_session_id']
                or any(not isinstance(op[k], str) or not op[k] or op[k].strip() != op[k]
                       for k in ('operation_key', 'principal', 'source_task_id', 'source_session_id', 'created_at'))):
            raise ValueError(code)
        proof = _stored_json(op['provenance_bytes'], op['provenance_digest'], code=code)
        intents = _records(conn, 'SELECT * FROM workflow_draft_dispatch_intents WHERE instance_id=? '
                           'AND task_id=? AND session_id=?',
                           (op['instance_id'], op['source_task_id'], op['source_session_id']))
        if (len(intents) != 1 or not isinstance(proof, dict)
                or set(proof) != {'format', 'operation', 'submission', 'source_intent', 'request_bytes'}
                or proof['format'] != 'workflow-submission-operation@1'
                or _canonical_bytes(proof['operation']) != _canonical_bytes({k: v for k, v in op.items() if k not in ('provenance_bytes', 'provenance_digest')})
                or _canonical_bytes(proof['submission']) != _canonical_bytes({k: (v.hex() if isinstance(v, bytes) else v) for k, v in submission.items()})
                or _canonical_bytes(proof['source_intent']) != _canonical_bytes({k: (v.hex() if isinstance(v, bytes) else v)
                                              for k, v in intents[0].items() if k not in _DRAFT_MUTABLE})
                or intents[0]['assigned_principal'] != op['principal']):
            raise ValueError(code)
        try:
            request = bytes.fromhex(proof['request_bytes'])
        except (TypeError, ValueError):
            raise ValueError(code) from None
        payload = _stored_json(request, op['request_digest'], code=code)
        if (not isinstance(payload, dict) or payload.get('submission_id') != submission['id']
                or payload.get('submission_digest') != submission['submission_digest']
                or payload.get('instance_id') != submission['instance_id']
                or type(payload.get('revision')) is not int or payload['revision'] != submission['revision']):
            raise ValueError(code)
    for round_ in _records(conn, 'SELECT r.*,s.instance_id AS subject_instance,s.revision AS subject_revision '
                          'FROM workflow_rounds r JOIN workflow_submissions s ON s.id=r.submission_id'):
        if round_['instance_id'] != round_['subject_instance'] or round_['current_revision'] != round_['subject_revision']:
            raise ValueError(code)
    for event in _records(conn, 'SELECT * FROM workflow_events'):
        if type(event['revision']) is not int or event['revision'] != _event_revision(conn, event):
            raise ValueError(code)
    for link in _records(conn, 'SELECT * FROM workflow_submission_result_links'):
        op = operations.get(link['submission_id'])
        if op is None or type(link['task_result_id']) is not int:
            raise ValueError(code)
        results = _records(conn, 'SELECT * FROM task_results WHERE id=?', (link['task_result_id'],))
        intents = _records(conn, 'SELECT * FROM workflow_draft_dispatch_intents WHERE task_id=? AND session_id=?',
                           (link['source_task_id'], link['source_session_id']))
        if len(results) != 1 or len(intents) != 1:
            raise ValueError(code)
        result, intent = results[0], intents[0]
        payload = _stored_json(link['result_bytes'], link['result_digest'], code=code)
        callbacks = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? "
                             "AND event_kind='callback_recorded' AND result_id=?", (intent['id'], result['id']))
        if (_canonical_bytes(payload) != _canonical_bytes(result) or result['task_id'] != link['source_task_id']
                or result['session_id'] != link['source_session_id'] or result['agent'] != op['principal']
                or link['source_task_id'] != op['source_task_id'] or link['source_session_id'] != op['source_session_id']
                or intent['final_result_id'] != result['id'] or len(callbacks) != 1
                or callbacks[0]['result_digest'] != link['result_digest']):
            raise ValueError(code)


def author_handoff_migration_guidance() -> str:
    return ('Stop the org daemon; with a separately approved compatible H reader run '
            'scripts/migrate_workflow_author_handoff_schema.py --org-root <absolute-org-root> '
            '--org-slug <org-slug> --check, then the same command without --check. '
            'F/E must use the existing approved migration to G first. Startup/reopen/enable never upgrades.')


def require_author_handoff_schema(conn: sqlite3.Connection, *, expected_org_slug: str) -> None:
    """Readiness only; existing-org owners never install the extension."""
    if validate_workflow_schema(conn, expected_org_slug=expected_org_slug) != 'H':
        from runtime.workflows.activation import WorkflowActivationError
        raise WorkflowActivationError('workflow_author_handoff_schema_required', owner='operator',
                                      required_action=author_handoff_migration_guidance())


def _handoff_json(raw: bytes, expected_digest: str | None = None) -> dict:
    """Closed new-family canonical decoding; no coercion or alternate encoding."""
    code = 'workflow_author_handoff_storage_corrupt'

    def pairs(items: list[tuple[str, object]]) -> dict:
        value: dict = {}
        for key, item in items:
            if key in value:
                raise ValueError(code)
            value[key] = item
        return value

    def number(value: str) -> None:
        raise ValueError(code)

    try:
        if not isinstance(raw, bytes) or not raw or raw.startswith(b'\xef\xbb\xbf'):
            raise ValueError(code)
        value = json.loads(raw.decode('utf8'), object_pairs_hook=pairs,
                           parse_float=number, parse_constant=number)
        if (not isinstance(value, dict) or _canonical_bytes(value) != raw
                or (expected_digest is not None and hashlib.sha256(raw).hexdigest() != expected_digest)):
            raise ValueError(code)
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError(code) from exc


def _handoff_record(conn: sqlite3.Connection, row: dict, *, expected_org_slug: str | None) -> dict:
    """Authenticate immutable H history against the retained original source.

    Mutable current authority is deliberately checked by the service, separately
    from this historical reader. No H update, deletion or repair is admitted.
    """
    code = 'workflow_author_handoff_storage_corrupt'

    def check(condition: bool) -> None:
        if not condition:
            raise ValueError(code)

    def closed(value: object, keys: str) -> dict:
        check(isinstance(value, dict) and set(value) == set(keys.split()))
        return value

    def one(table: str, identity: str) -> dict:
        rows = _records(conn, f'SELECT * FROM {table} WHERE id=?', (identity,))
        check(len(rows) == 1)
        return rows[0]

    def hex_bytes(value: object, limit: int) -> bytes:
        check(isinstance(value, str) and re.fullmatch(r'(?:[0-9a-f]{2})+', value) is not None)
        raw = bytes.fromhex(value)
        check(1 <= len(raw) <= limit)
        return raw

    try:
        check(row['org_slug'] == expected_org_slug and re.fullmatch(r'[a-z0-9-]{1,40}', row['org_slug']) is not None)
        for key in ('id', 'intent_id', 'instance_id', 'principal', 'source_task_id', 'source_session_id',
                    'operation_key', 'submission_id'):
            check(isinstance(row[key], str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', row[key]) is not None)
        check(type(row['revision']) is int and row['revision'] == 1)
        for key, limit in (('candidate_bytes', 1048576), ('request_bytes', 2097152), ('admission_bytes', 16777216)):
            raw = row[key]
            check(isinstance(raw, bytes) and 1 <= len(raw) <= limit)
            digest_value = row[key.replace('_bytes', '_digest')]
            check(isinstance(digest_value, str) and re.fullmatch(r'[0-9a-f]{64}', digest_value) is not None
                  and hashlib.sha256(raw).hexdigest() == digest_value)
        row['candidate_bytes'].decode('utf8')
        check(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', row['created_at']) is not None)
        datetime.strptime(row['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ')
        proof = closed(_handoff_json(row['admission_bytes'], row['admission_digest']),
                       'format handoff client_request source_intent source_generation pins admission_authority')
        check(proof['format'] == 'workflow-author-handoff@1')
        expected_row = {k: v.hex() if isinstance(v, bytes) else v for k, v in row.items()
                        if k not in ('admission_bytes', 'admission_digest')}
        check(proof['handoff'] == expected_row)
        command = closed(proof['client_request'], 'format operation_key activation_id expected_revision submission_bytes_base64 submission_digest')
        check(command['format'] == 'workflow-author-handoff-command@1' and type(command['expected_revision']) is int
              and command['expected_revision'] == 1 and command['operation_key'] == row['operation_key'])
        candidate = base64.b64decode(command['submission_bytes_base64'], validate=True)
        check(candidate == row['candidate_bytes'] and command['submission_digest'] == row['candidate_digest']
              and base64.b64encode(candidate).decode('ascii') == command['submission_bytes_base64'])
        request = {**command, 'format': 'workflow-author-handoff-request@1', 'handoff_id': row['id'],
                   'submission_id': row['submission_id'], 'instance_id': row['instance_id'], 'revision': 1}
        check(_canonical_bytes(request) == row['request_bytes'])
        check(row['id'] == 'workflow-author-handoff:' + hashlib.sha256(_canonical_bytes(
            [row['org_slug'], row['principal'], row['operation_key']])).hexdigest())
        check(row['submission_id'] == 'workflow-submission:' + hashlib.sha256(_canonical_bytes(
            [row['instance_id'], 1, row['org_slug'], row['principal'], row['operation_key'], 'workflow-author-handoff@1'])).hexdigest())
        intent = one('workflow_draft_dispatch_intents', row['intent_id'])
        check(proof['source_intent'] == {k: v.hex() if isinstance(v, bytes) else v
                                        for k, v in intent.items() if k not in _DRAFT_MUTABLE})
        check(len(proof['source_intent']) == 25 and intent['admission_kind'] == 'initial'
              and intent['predecessor_intent_id'] is None and intent['activation_revision'] == 1
              and intent['attempt_sequence'] == 1 and intent['assignment_generation'] == 1
              and intent['recovery_owner'] == 'workflow_recovery'
              and intent['task_id'] == row['source_task_id'] and intent['instance_id'] == row['instance_id']
              and intent['assigned_principal'] == row['principal'] and command['activation_id'] == intent['activation_id'])
        for key in ('request_bytes', 'task_scope_bytes'):
            check(1 <= len(intent[key]) <= 1048576)
        generation = closed(proof['source_generation'], 'org_slug task_id principal session_id activation_revision assignment_generation attempt_sequence claim_token claim_owner host_execution_key host_execution_id state is_current host_launch_started cancellation_requested final_result_id')
        check(generation['org_slug'] == row['org_slug'] and generation['task_id'] == row['source_task_id']
              and generation['principal'] == row['principal'] and generation['session_id'] == row['source_session_id'])
        for key in ('activation_revision', 'assignment_generation', 'attempt_sequence', 'is_current', 'host_launch_started'):
            check(type(generation[key]) is int and generation[key] == 1)
        check(type(generation['cancellation_requested']) is int and generation['cancellation_requested'] == 0
              and generation['final_result_id'] is None and generation['state'] in ('claimed', 'running')
              and generation['claim_owner'] == 'workflow_dispatcher'
              and generation['host_execution_key'] == intent['host_execution_key'])
        for key in ('claim_token', 'claim_owner', 'host_execution_key'):
            check(isinstance(generation[key], str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', generation[key]) is not None)
        check(generation['host_execution_id'] is None or (isinstance(generation['host_execution_id'], str)
              and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', generation['host_execution_id']) is not None))
        events = _records(conn, 'SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? ORDER BY event_seq', (intent['id'],))
        witnessed = False
        for event in events:
            # S2 callback records retain native historical numeric types. Only
            # the newly introduced H family uses the stricter numeric grammar.
            payload = _stored_json(event['event_bytes'], event['event_digest'], code=code)
            if payload['after'] == {k: generation[k] for k in _DRAFT_PROJECTION if k != 'session_id'} | {'session_id': generation['session_id']}:
                check(datetime.fromisoformat(event['created_at']) <= datetime.strptime(row['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc))
                witnessed = True
        check(witnessed)
        pins = closed(proof['pins'], 'template activation authorization binding context scope contract')
        activation = one('workflow_activations', intent['activation_id'])
        instance = one('workflow_instances', row['instance_id'])
        check(pins['activation'] == dict(id=intent['activation_id'], revision=1, request_digest=activation['request_digest']))
        from runtime.workflows.templates import WorkflowTemplateStore, _document_contract
        mapping = _records(conn, 'SELECT * FROM workflow_template_identity_versions WHERE template_version_id=?', (activation['template_version_id'],))
        check(len(mapping) == 1)
        template = WorkflowTemplateStore._read_version(conn, identity_id=activation['template_identity_id'],
            version=mapping[0]['version'], expected_version_id=activation['template_version_id'])
        check(pins['template'] == dict(identity_id=template.identity_id, version_id=template.version_id, version=template.version,
              definition_digest=template.definition_digest, compiler_pin='workflow-compiler@3',
              validator_pin='workflow-validator@3', source_pin='operator-input@3')
              and (template.compiler_pin, template.validator_pin, template.source_pin) ==
              ('workflow-compiler@3', 'workflow-validator@3', 'operator-input@3'))
        contract = _document_contract(template)
        context = one('workflow_contexts', intent['context_id'])
        binding = one('workflow_binding_snapshots', intent['binding_snapshot_id'])
        authorization = one('workflow_authorization_revisions', binding['authorization_revision_id'])
        for kind, identity, raw, dg in (
                ('authorization', authorization['id'], authorization['authority_bytes'], authorization['authority_digest']),
                ('binding', binding['id'], binding['binding_bytes'], binding['binding_digest']),
                ('context', context['id'], context['context_bytes'], context['context_digest']),
                ('scope', intent['task_id'], intent['task_scope_bytes'], intent['task_scope_digest']),
                ('contract', template.version_id, _canonical_bytes(contract), hashlib.sha256(_canonical_bytes(contract)).hexdigest())):
            pin = closed(pins[kind], 'id bytes_hex digest')
            check(pin == dict(id=identity, bytes_hex=raw.hex(), digest=dg)
                  and len(identity) <= 256 and 1 <= len(raw) <= 1048576)
            _handoff_json(hex_bytes(pin['bytes_hex'], 1048576), pin['digest'])
        grant = _handoff_json(authorization['authority_bytes'], authorization['authority_digest'])
        bound = _handoff_json(binding['binding_bytes'], binding['binding_digest'])
        ctx = _handoff_json(context['context_bytes'], context['context_digest'])
        check(grant['format'] == 'workflow-authorization@3' and bound['format'] == 'workflow-binding@3'
              and ctx['format'] == 'workflow-initial-draft-context@3' and contract['format'] == 'workflow-document-contract@3'
              and ctx['document_contract'] == contract and ctx['authorization'] == grant and ctx['binding'] == bound
              and ctx['binding_snapshot_id'] == binding['id'] and ctx['authorization_revision_id'] == authorization['id']
              and ctx['intent_id'] == intent['id'] and ctx['org_slug'] == expected_org_slug
              and instance['root_task_id'] == intent['task_id'] and instance['context_id'] == context['id']
              and instance['binding_snapshot_id'] == binding['id'] and instance['owner_principal'] == intent['admission_principal'])
        captured = closed(proof['admission_authority'], 'ready pointer profiles mirror cutover actions')
        ready = closed(captured['ready'], 'namespace generation snapshot_bytes_hex snapshot_digest')
        snapshot = _handoff_json(hex_bytes(ready['snapshot_bytes_hex'], 1048576), ready['snapshot_digest'])
        journal = _records(conn, 'SELECT * FROM workflow_publication_journals WHERE namespace=? AND generation=? AND snapshot_digest=?',
                           (ready['namespace'], ready['generation'], ready['snapshot_digest']))
        check(len(journal) == 1 and journal[0]['snapshot_bytes'].hex() == ready['snapshot_bytes_hex']
              and journal[0]['state'] == 'cache_installed' and snapshot == ctx['authority_snapshot']
              and ready['namespace'] == f'org/{expected_org_slug}' and type(ready['generation']) is int
              and ready['generation'] == intent['authority_generation'] and ready['snapshot_digest'] == intent['authority_digest'])
        check(captured['pointer'] == dict(current_generation=ready['generation'], journal_id=journal[0]['id'],
              snapshot_digest=ready['snapshot_digest'], state='ready', profile_fence=journal[0]['profile_fence']))
        check(captured['cutover'] == 'enabled' and captured['actions'] == ['stage-immutable-document', 'submit-immutable-document']
              and set(captured['actions']) <= set(grant['original_request']['allowed_actions']))
        def namespace(value: object) -> None:
            check(type(value) is str and bool(value.strip()) and '\x00' not in value
                  and 1 <= len(value.encode('utf8')) <= 256)

        def profile_name(value: object) -> None:
            # SourceProfileName is the complete authentic producer string,
            # not the abstract workflow Role grammar or a normalized alias.
            check(type(value) is str and bool(value))

        def i0(value: object) -> None:
            check(type(value) is int and 0 <= value <= 9223372036854775807)

        def digest_value(value: object) -> None:
            check(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)

        snapshot_profiles = snapshot['machine_global_profiles']
        check(isinstance(snapshot_profiles, list) and len(snapshot_profiles) <= 1024)
        names: list[str] = []
        for profile in snapshot_profiles:
            closed(profile, 'consumers generation profile_digest profile_name')
            profile_name(profile['profile_name'])
            i0(profile['generation'])
            digest_value(profile['profile_digest'])
            check(isinstance(profile['consumers'], list))
            for consumer in profile['consumers']:
                namespace(consumer)
            check(profile['consumers'] == sorted(set(profile['consumers'])))
            names.append(profile['profile_name'])
        check(names == sorted(set(names)))
        check(isinstance(captured['profiles'], list) and len(captured['profiles']) <= 1024)
        for profile in captured['profiles']:
            closed(profile, 'name digest')
            profile_name(profile['name'])
            digest_value(profile['digest'])
        check(captured['profiles'] == [dict(name=p['profile_name'], digest=p['profile_digest'])
                                      for p in snapshot_profiles])
        check(isinstance(captured['mirror'], list) and len(captured['mirror']) <= 16384)
        grouped: list[tuple] = []
        dependencies: list[dict] = []
        stores: dict[str, dict] = {}
        registry: dict[str, dict] = {}
        for item in captured['mirror']:
            kind = item.get('kind') if isinstance(item, dict) else None
            fields = {'dependency': 'kind org_namespace profile_name consumer_identity bound_generation state',
                      'store': 'kind profile_name generation profile_digest state',
                      'registry': 'kind profile_name published_generation'}
            check(type(kind) is str and kind in fields)
            closed(item, fields[kind])
            profile_name(item['profile_name'])
            if kind == 'dependency':
                namespace(item['org_namespace'])
                namespace(item['consumer_identity'])
                i0(item['bound_generation'])
                check(type(item['state']) is str and item['state'] in ('active', 'unbound', 'removed'))
                grouped.append((0, item['org_namespace'], item['profile_name'], item['consumer_identity']))
                dependencies.append(item)
            elif kind == 'store':
                i0(item['generation'])
                digest_value(item['profile_digest'])
                check(type(item['state']) is str and item['state'] in ('active', 'removed'))
                grouped.append((1, item['profile_name']))
                stores[item['profile_name']] = item
            else:
                i0(item['published_generation'])
                grouped.append((2, item['profile_name']))
                registry[item['profile_name']] = item
        check(grouped == sorted(set(grouped)))
        # Reconstruct authority::_profile_projection from the saved full mirror.
        # Current mutable tables/files are not witnesses to historical readiness.
        required: dict[str, dict] = {}
        for dependency in dependencies:
            if dependency['org_namespace'] != ready['namespace'] or dependency['state'] == 'removed':
                continue
            name = dependency['profile_name']
            store = stores.get(name)
            published = registry.get(name)
            check(dependency['state'] == 'active' and store is not None and published is not None)
            bound_generation = dependency['bound_generation']
            check(store['state'] == 'active' and store['generation'] == bound_generation
                  and published['published_generation'] == bound_generation)
            profile = required.setdefault(name, dict(consumers=[], generation=bound_generation,
                                                    profile_digest=store['profile_digest'], profile_name=name))
            check(profile['generation'] == bound_generation and profile['profile_digest'] == store['profile_digest'])
            profile['consumers'].append(dependency['consumer_identity'])
        check([required[name] for name in sorted(required)] == snapshot_profiles)
        return proof
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, sqlite3.DatabaseError) as exc:
        raise ValueError(code) from exc


def _validate_author_handoff_data(conn: sqlite3.Connection, *, expected_org_slug: str | None) -> None:
    for row in _records(conn, 'SELECT * FROM workflow_author_handoffs'):
        proof = _handoff_record(conn, row, expected_org_slug=expected_org_slug)
        operations = _records(conn, 'SELECT * FROM workflow_submission_operations WHERE '
                              '(org_slug=? AND principal=? AND operation_key=?) OR submission_id IN '
                              '(SELECT id FROM workflow_submissions WHERE instance_id=? AND revision=1)',
                              (row['org_slug'], row['principal'], row['operation_key'], row['instance_id']))
        if operations:
            # G's unchanged complete five-key operation/result reader runs first.
            if (len(operations) != 1 or operations[0]['submission_id'] != row['submission_id']
                    or operations[0]['request_digest'] != row['request_digest']):
                raise ValueError('workflow_author_handoff_storage_corrupt')
            original = _handoff_json(operations[0]['provenance_bytes'], operations[0]['provenance_digest'])
            if original['request_bytes'] != row['request_bytes'].hex() or original['source_intent'] != proof['source_intent']:
                raise ValueError('workflow_author_handoff_storage_corrupt')
            submission = original['submission']
            if (submission['submission_bytes'] != row['candidate_bytes'].hex()
                    or submission['submission_digest'] != row['candidate_digest']
                    or submission['source_result_id'] is not None):
                raise ValueError('workflow_author_handoff_storage_corrupt')
        elif conn.execute('SELECT 1 FROM workflow_submissions WHERE id=? OR (instance_id=? AND revision=1)',
                          (row['submission_id'], row['instance_id'])).fetchone():
            raise ValueError('workflow_author_handoff_storage_corrupt')
        # The U3a(b) reader authenticates all declared requests and their actual
        # owned task/event closure. Missing producer dependencies still refuse.
        try:
            from runtime.workflows.submissions import validate_handoff_graph
        except ImportError as exc:
            raise ValueError('workflow_author_handoff_graph_validator_unavailable') from exc
        validate_handoff_graph(conn, row, proof)


def migrate_author_handoff_schema(conn: sqlite3.Connection, *, expected_org_slug: str) -> Literal['migrated', 'ready']:
    """Explicit offline G-to-H only; preserve all retained relations atomically."""
    if conn.in_transaction or conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('workflow_author_handoff_migration_requires_idle_foreign_keys')
    layout = validate_workflow_schema(conn, expected_org_slug=expected_org_slug)
    _validate_release_database(conn, layout)
    if layout == 'H':
        return 'ready'
    if layout != 'G':
        raise ValueError('workflow_author_handoff_requires_g: ' + submission_migration_guidance(org_slug=expected_org_slug))
    _validate_submission_source_ownership(conn, layout)
    try:
        conn.execute('BEGIN IMMEDIATE')
        layout = validate_workflow_schema(conn, expected_org_slug=expected_org_slug)
        _validate_release_database(conn, layout)
        if layout == 'H':
            conn.rollback()
            return 'ready'
        if layout != 'G':
            raise ValueError('workflow_author_handoff_requires_g')
        _validate_submission_source_ownership(conn, layout)
        retained = _retained_rows(conn)
        _execute_ddl(conn, CANONICAL_WORKFLOW_AUTHOR_HANDOFF_DDL)
        validate_workflow_schema(conn, expected_org_slug=expected_org_slug)
        _validate_release_database(conn, 'H')
        after = _retained_rows(conn)
        if any(after[table] != rows for table, rows in retained.items()):
            raise ValueError('workflow_author_handoff_retained_relation_mismatch')
        conn.commit()
        return 'migrated'
    except BaseException:
        conn.rollback()
        raise


def _validate_release_database(conn: sqlite3.Connection, layout: str) -> None:
    """Compare every whole-database SQL object to an independent release DB."""
    from runtime.orchestrator.authority import _release_schema_digest, _RELEASE_REFERENCE_HISTORIES
    rows = conn.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name').fetchall()
    actual = hashlib.sha256('\n'.join(str(r[0]) for r in rows).encode()).hexdigest()
    from runtime.infrastructure.task_pause_controls import validate_pause_schema
    pause_layout = validate_pause_schema(conn, allow_absent=True)
    expected = tuple(_release_schema_digest(layout, history, pause_layout=pause_layout)
                     for history in _RELEASE_REFERENCE_HISTORIES)
    if 'unavailable' in expected or actual not in expected:
        raise ValueError('workflow_submission_whole_database_mismatch')


def _validate_submission_source_ownership(conn: sqlite3.Connection, layout: str) -> None:
    """An absent PID never discharges durable possible-launch/recovery work."""
    queries = (
        ('workflow_publication_leases', '1'), ('workflow_profile_leases', '1'),
        ('workflow_publication_journals', "state NOT IN ('cache_installed','aborted')"),
        ('workflow_profile_operations', "state NOT IN ('published','aborted')"),
        ('workflow_dispatch_outbox', "state IN ('claimed','running','uncertain') OR (host_launch_started=1 AND state NOT IN ('cancelled','completed'))"),
        ('workflow_request_task_bridges', "state IN ('claimed','running','uncertain')"),
        ('workflow_recovery_claims', "state='claimed'"),
    )
    if layout in ('E', 'G', 'H'):
        queries += (('workflow_draft_dispatch_intents', "state IN ('claimed','running','uncertain') OR "
                     "(cancellation_requested=1 AND state NOT IN ('cancelled','failed','completed')) OR "
                     "(host_launch_started=1 AND state NOT IN ('cancelled','failed','completed'))"),)
    for table, predicate in queries:
        if conn.execute(f'SELECT 1 FROM {table} WHERE {predicate} LIMIT 1').fetchone():
            raise ValueError(f'workflow_submission_source_owner_active: {table}; reconcile its durable owner and host closure')


def _retained_rows(conn: sqlite3.Connection) -> dict[str, tuple]:
    """Exact rowid, original column types/bytes and transitive inbound rows."""
    result = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"):
        quoted = str(table).replace('"', '""')
        result[table] = tuple(tuple(row) for row in conn.execute(f'SELECT rowid,* FROM "{quoted}" ORDER BY rowid'))
    return result


def migrate_submission_schema(conn: sqlite3.Connection, *, expected_org_slug: str) -> Literal['migrated', 'ready']:
    """Own one raw, idle, FK-enabled connection transaction; never a daemon DB.

    Offline daemon observation belongs to the operator script. This primitive
    independently fences persisted owners and rechecks after writer acquisition.
    """
    if conn.in_transaction or conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('workflow_submission_migration_requires_idle_foreign_keys')
    layout = validate_workflow_schema(conn, expected_org_slug=expected_org_slug)
    _validate_release_database(conn, layout)
    if layout in ('G', 'H'):
        return 'ready'
    _validate_submission_source_ownership(conn, layout)
    for event in _records(conn, 'SELECT * FROM workflow_events'):
        _event_revision(conn, event)
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 0:
            raise ValueError('workflow_submission_fk_policy_unavailable')
        conn.execute('BEGIN IMMEDIATE')
        layout = validate_workflow_schema(conn, expected_org_slug=expected_org_slug)
        _validate_release_database(conn, layout)
        if layout in ('G', 'H'):
            conn.rollback()
            return 'ready'
        _validate_submission_source_ownership(conn, layout)
        revisions = {e['id']: _event_revision(conn, e) for e in _records(conn, 'SELECT * FROM workflow_events')}
        retained = _retained_rows(conn)
        conn.execute('CREATE TEMP TABLE submission_stage AS SELECT rowid AS original_rowid,* FROM workflow_submissions')
        conn.execute('CREATE TEMP TABLE event_stage AS SELECT rowid AS original_rowid,* FROM workflow_events')
        conn.execute('DROP TABLE workflow_events')
        conn.execute('DROP TABLE workflow_submissions')
        submissions, events, additions = _g_definitions()
        _execute_ddl(conn, submissions)
        _execute_ddl(conn, events)
        conn.execute('INSERT INTO workflow_submissions(rowid,id,instance_id,revision,submission_bytes,submission_digest,'
                     'storage_ref,source_task_id,source_session_id,source_result_id,author_principal) SELECT * FROM submission_stage')
        for row in conn.execute('SELECT * FROM event_stage ORDER BY original_rowid').fetchall():
            values = tuple(row)
            conn.execute('INSERT INTO workflow_events(rowid,id,instance_id,revision,event_kind,event_bytes,event_digest,created_at) '
                         'VALUES (?,?,?,?,?,?,?,?)', values[:3] + (revisions[values[1]],) + values[3:])
        conn.execute('CREATE INDEX workflow_events_instance_idx ON workflow_events(instance_id)')
        if layout == 'F':
            _execute_ddl(conn, CANONICAL_WORKFLOW_DRAFT_DDL)
            conn.execute('INSERT INTO workflow_draft_adapter_versions VALUES (1)')
        _execute_ddl(conn, additions)
        conn.execute('INSERT INTO workflow_submission_schema_versions VALUES (1)')
        validate_workflow_schema(conn, expected_org_slug=expected_org_slug)
        _validate_release_database(conn, 'G')
        after = _retained_rows(conn)
        for table, rows in retained.items():
            observed = after[table]
            if table == 'workflow_events':
                observed = tuple(row[:3] + row[4:] for row in observed)
            if observed != rows:
                raise ValueError(f'workflow_submission_retained_relation_mismatch: {table}')
        conn.execute('DROP TABLE temp.submission_stage')
        conn.execute('DROP TABLE temp.event_stage')
        conn.commit()
        return 'migrated'
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute('PRAGMA foreign_keys=ON')
        if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise ValueError('workflow_submission_fk_policy_restore_failed')
