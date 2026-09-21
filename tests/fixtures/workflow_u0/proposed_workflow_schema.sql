-- TASK-7263 U0 only: executable on isolated test adapters, never installed.
PRAGMA foreign_keys=ON;
-- This is the adapter's sole durable version discriminator.  It is created
-- and committed in the same isolated transaction as every proposed table.
CREATE TABLE workflow_adapter_versions (version INTEGER PRIMARY KEY CHECK(version=1));
CREATE TABLE workflow_template_drafts (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, definition_bytes BLOB NOT NULL, definition_digest TEXT NOT NULL UNIQUE, compiler_pin TEXT NOT NULL, validator_pin TEXT NOT NULL, source_pin TEXT NOT NULL, author_principal TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE workflow_template_versions (id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES workflow_template_drafts(id), namespace TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0), definition_bytes BLOB NOT NULL, definition_digest TEXT NOT NULL UNIQUE, compiler_pin TEXT NOT NULL, validator_pin TEXT NOT NULL, source_pin TEXT NOT NULL, published_by TEXT NOT NULL, UNIQUE(namespace,version));
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
-- These three test-only relations are the F2 provenance bridge.  They are not
-- publication/outbox protocol: that separate F4/F5 work remains pending.
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
-- F4/F5 proposed authority-publication model.  These are isolated evidence
-- relations, not a runtime migration or an installed coordination protocol.
CREATE TABLE workflow_authority_pointers (namespace TEXT PRIMARY KEY, current_generation INTEGER NOT NULL CHECK(current_generation>=0), journal_id TEXT, snapshot_digest TEXT, state TEXT NOT NULL CHECK(state IN ('ready','fenced')), profile_fence INTEGER NOT NULL DEFAULT 0 CHECK(profile_fence>=0), CHECK((current_generation=0 AND journal_id IS NULL AND snapshot_digest IS NULL) OR (current_generation>0 AND journal_id IS NOT NULL AND snapshot_digest IS NOT NULL)));
CREATE TABLE workflow_publication_journals (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0), expected_generation INTEGER NOT NULL CHECK(expected_generation>=0), snapshot_bytes BLOB NOT NULL, snapshot_digest TEXT NOT NULL, publisher TEXT NOT NULL, publisher_invocation TEXT NOT NULL, profile_fence INTEGER NOT NULL CHECK(profile_fence>=0), state TEXT NOT NULL CHECK(state IN ('prepared','file_phase_reserved','canonical_published','forward_recovery_required','pointer_committed','cache_installed','aborted')), recovery_owner TEXT NOT NULL, file_phase_owner TEXT, CHECK(generation=expected_generation+1), CHECK((state='file_phase_reserved' AND file_phase_owner IS NOT NULL) OR state!='file_phase_reserved'));
CREATE TABLE workflow_publication_leases (namespace TEXT PRIMARY KEY, owner_token TEXT NOT NULL, owner_pid INTEGER NOT NULL CHECK(owner_pid>0));
CREATE TABLE workflow_admission_records (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0), request_digest TEXT NOT NULL, admitted_by TEXT NOT NULL, UNIQUE(namespace,id), FOREIGN KEY(namespace) REFERENCES workflow_authority_pointers(namespace));
CREATE INDEX workflow_publication_journals_namespace_state_idx ON workflow_publication_journals(namespace,state,generation);
CREATE INDEX workflow_admission_records_namespace_generation_idx ON workflow_admission_records(namespace,generation);
-- F4 proposed machine-global profile membership/activation coordinator.  This is
-- an isolated evidence relation set, not an installed migration and not a claim
-- of a distributed atomic commit.  The coordinator is same-host cooperative
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
