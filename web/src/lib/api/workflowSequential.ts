/** Sequential Founder reads/settings mirror; existing bearer, no start. */
import { ApiError, request } from './client';
import { getToken } from '../auth';
import type { WorkflowSetupStatus, SequentialProjectionUnavailable } from './types';

export const getWorkflowSetupStatus = (slug: string, signal?: AbortSignal): Promise<WorkflowSetupStatus> =>
  request(`/runtime/workflow-setup/orgs/${encodeURIComponent(slug)}`, { signal });

export interface DefinitionPin {
  identity: string; version: number; definition_digest: string; source_digest: string;
  compiler: 'workflow-sequential-compiler@1'; validator: 'workflow-sequential-validator@1';
  interpreter: 'workflow-sequential-interpreter@1'; capabilities: 'workflow-sequential-capabilities@1';
}
export interface SequentialLimits {
  content_returns: number; max_content_epochs: number; max_visits: number; max_events: number;
}
export interface SequentialBinding { role: string; principal: string }
export interface SequentialDefinition { id: string; pin: DefinitionPin; definition_json: string }
export interface SequentialDefinitions {
  format: 'workflow-sequential-definitions-view@1';
  storage: 'new-engine unavailable' | 'storage readable'; definitions: SequentialDefinition[];
}
export interface SequentialSettings {
  format: 'workflow-sequential-settings-view@1'; id: string; generation: number; snapshot_digest: string;
  snapshot: { format: 'workflow-sequential-settings-storage@1'; definition: DefinitionPin;
    bindings: SequentialBinding[]; limits: SequentialLimits; admission: 'Disabled' | 'Allowed' };
}
/** Agent-session contract only, like repository-preflight. No browser client. */
export interface SequentialStartContext {
  format: 'workflow-sequential-start-context@1'; start_format: 'workflow-sequential-start@1';
  max_runs: 1; assignments: 'fixed'; source_semantics: 'source-terminal';
  definitions: {
    selector: string; pin: DefinitionPin; description: string;
    roles: { role: string; requirement: { kind: 'agent'; executable: true;
      eligibility: 'worker' | 'registered-team-manager'; independent_of: string[] };
      responsibilities: string[] }[];
    required_inputs: { name: string; contract: 'bounded-text@1'; required: boolean }[];
    definition_limits: SequentialLimits; registered: boolean; settings: SequentialSettings | null;
    admission: 'Disabled' | 'Allowed'; defaults: SequentialBinding[]; limits: SequentialLimits | null;
    unavailable_cause: 'sequential_storage_unavailable' | 'sequential_definition_not_registered'
      | 'sequential_settings_absent' | 'sequential_settings_disabled' | 'sequential_host_unavailable' | null;
  }[];
}
export interface SequentialRun {
  id: string; definition_id: string; definition: DefinitionPin; settings_id: string;
  settings: { generation: number; snapshot_digest: string };
  state: 'active' | 'held' | 'completed' | 'failed' | 'cancelled'; active_visit_id: string | null;
  counters: { epoch: number; content_returns: number; visits: number; events: number };
  tasks: { task_id: string; relationship: 'source' | 'carrier' | 'visit'; visit_id: string | null }[];
}
export interface SequentialRuns {
  format: 'workflow-sequential-runs-view@1';
  storage: 'new-engine unavailable' | 'storage readable'; runs: SequentialRun[];
}
export interface SequentialRunDetail {
  format: 'workflow-sequential-run-view@1'; run: SequentialRun; request: string;
  bindings: SequentialBinding[]; limits: SequentialLimits;
  effective_limits: SequentialLimits;
  reducer_counters: SequentialRun['counters'];
  inputs: { name: string; contract: 'bounded-text@1'; value: string }[];
  holds: { visit_id: string | null; cause: string; next_owner: string; action: string }[];
  visits: { id: string; node_id: string; ordinal: number; epoch: number; task_id: string; principal: string;
    state: string; selected: boolean; outcome: string | null; findings: string[] }[];
  outputs: { id: string; visit_id: string; slot: string; contract: string; epoch: number;
    content_digest: string; content_utf8: string }[];
}
export interface SequentialTaskWorkflow {
  format: 'workflow-sequential-task-link@1'; task_id: string; run: SequentialRun | null;
}
export const listSequentialDefinitions = (slug: string, signal?: AbortSignal): Promise<SequentialDefinitions> =>
  request(`/orgs/${slug}/workflows/sequential/definitions`, { signal });
export const getSequentialDefinition = (slug: string, id: string, signal?: AbortSignal): Promise<SequentialDefinition> =>
  request(`/orgs/${slug}/workflows/sequential/definitions/${encodeURIComponent(id)}`, { signal });
export const getSequentialSettings = (slug: string, identity: string, signal?: AbortSignal): Promise<SequentialSettings> =>
  request(`/orgs/${slug}/workflows/sequential/settings`, { params: { definition_identity: identity }, signal });
export const listSequentialRuns = (slug: string, signal?: AbortSignal): Promise<SequentialRuns> =>
  request(`/orgs/${slug}/workflows/sequential/runs`, { signal });
export const getSequentialRun = (slug: string, id: string, signal?: AbortSignal): Promise<SequentialRunDetail> =>
  request(`/orgs/${slug}/workflows/sequential/runs/${encodeURIComponent(id)}`, { signal });
export const getTaskSequentialWorkflow = (slug: string, id: string, signal?: AbortSignal): Promise<SequentialTaskWorkflow> =>
  request(`/orgs/${slug}/tasks/${encodeURIComponent(id)}/sequential-workflow`, { signal });

export interface SequentialSettingsCommand {
  format: 'workflow-sequential-settings-put@1'; operation_key: string; expected_generation: number;
  definition: DefinitionPin; bindings: SequentialBinding[]; limits: SequentialLimits;
}
export interface SequentialSettingsReceipt {
  format: 'workflow-sequential-settings-receipt@1'; org_slug: string; operation_key: string;
  command: SequentialSettingsCommand;
  receipt: { format: 'workflow-sequential-settings-receipt-storage@1'; operation_id: string;
    settings_id: string; generation: number; snapshot_digest: string };
  original: SequentialSettings;
}

export interface GenericSequentialLimits {
  max_visits: number; max_events: number; max_recoveries: number; node_revisions?: Record<string, number>;
}
export interface GenericSequentialSettings {
  format: 'workflow-sequential-settings-view@2'; id: string; generation: number; snapshot_digest: string;
  snapshot: { format: 'workflow-sequential-settings-storage@2'; definition: GenericDefinitionPin;
    bindings: SequentialBinding[]; limits: GenericSequentialLimits; admission: 'Disabled' | 'Allowed' };
}
export type SetupSettings = SequentialSettings | GenericSequentialSettings;
export type SetupPin = DefinitionPin | GenericDefinitionPin;
export interface SetupDefinitions {
  format: 'workflow-sequential-definitions-view@2'; storage: SequentialDefinitions['storage'];
  definitions: { id: string; pin: SetupPin; definition_json: string }[];
}
export interface GenericSequentialSettingsCommand {
  format: 'workflow-sequential-settings-put@2'; operation_key: string; expected_generation: number;
  definition: GenericDefinitionPin; bindings: SequentialBinding[]; limits: GenericSequentialLimits;
}
export interface SequentialAdmissionCommand {
  format: 'workflow-sequential-admission@1'; operation_key: string; expected_generation: number;
  definition: SetupPin; admission: 'Allowed' | 'Disabled';
}
export type SetupSaveCommand = SequentialSettingsCommand | GenericSequentialSettingsCommand;
export type SetupCommand = SetupSaveCommand | SequentialAdmissionCommand;
export interface GenericSequentialSettingsReceipt {
  format: 'workflow-sequential-settings-receipt@2'; org_slug: string; operation_key: string;
  command: GenericSequentialSettingsCommand; receipt: SequentialSettingsReceipt['receipt']; original: GenericSequentialSettings;
}
export interface SequentialAdmissionReceipt {
  format: 'workflow-sequential-admission-receipt@1'; org_slug: string; operation_key: string;
  command: SequentialAdmissionCommand; receipt: SequentialSettingsReceipt['receipt']; original: SetupSettings;
}
export type SetupReceipt = SequentialSettingsReceipt | GenericSequentialSettingsReceipt | SequentialAdmissionReceipt;
export const listSequentialSetupDefinitions = (slug: string, signal?: AbortSignal): Promise<SetupDefinitions> =>
  request(`/orgs/${slug}/workflows/sequential/views/v2/definitions`, { signal });
export const getSequentialSetupDefinition = (slug: string, id: string, signal?: AbortSignal): Promise<SetupDefinitions['definitions'][number]> =>
  request(`/orgs/${slug}/workflows/sequential/views/v2/definitions/${encodeURIComponent(id)}`, { signal });
export const getSequentialSetupSettings = (slug: string, identity: string, signal?: AbortSignal): Promise<SetupSettings> =>
  request(`/orgs/${slug}/workflows/sequential/views/v2/settings`, { params: { definition_identity: identity }, signal });

/** Closed commands have ASCII keys and integer numbers. Freeze exact original
 * bytes before awaiting token/transport; never normalize an uncertain resend. */
function canonical(value: unknown): unknown {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) throw new TypeError('sequential_settings_integer');
    return value;
  }
  if (typeof value === 'string') {
    if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) throw new TypeError('sequential_settings_unicode');
    return value;
  }
  if (value === null || typeof value === 'boolean') return value;
  if (Array.isArray(value)) return Object.freeze(value.map(canonical));
  if (typeof value !== 'object' || Object.getPrototypeOf(value) !== Object.prototype) throw new TypeError('sequential_settings_json');
  return Object.freeze(Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical((value as Record<string, unknown>)[key])])));
}
export function freezeSequentialSettingsCommand<T extends SetupCommand>(command: T): T {
  const copy = canonical(command) as T;
  if (new TextEncoder().encode(JSON.stringify(copy)).length > 16384) throw new TypeError('sequential_settings_size');
  return copy;
}
function receipt(value: SequentialSettingsReceipt, slug: string, key: string): SequentialSettingsReceipt {
  canonical(value);
  if (value.format !== 'workflow-sequential-settings-receipt@1' || value.org_slug !== slug
    || value.operation_key !== key || value.command.operation_key !== key
    || value.receipt.settings_id !== value.original.id || value.receipt.generation !== value.original.generation
    || value.receipt.snapshot_digest !== value.original.snapshot_digest) throw new TypeError('sequential_settings_receipt');
  return value;
}
/** One PUT, including on 401. Uses the existing Founder token custody. */
export const putSequentialSettings = async (slug: string, command: SequentialSettingsCommand): Promise<SequentialSettingsReceipt> => {
  const body = freezeSequentialSettingsCommand(command);
  const saved = await request<SequentialSettingsReceipt>(`/orgs/${slug}/workflows/sequential/settings`, {
    method: 'PUT', body, auth: { token: getToken },
  });
  const value = receipt(saved, slug, body.operation_key);
  if (JSON.stringify(canonical(value.command)) !== JSON.stringify(body)) throw new TypeError('sequential_settings_original_command');
  return value;
};
/** Original receipt only. Never substitutes the latest current-generation GET. */
export const getSequentialSettingsOperation = async (slug: string, key: string, signal?: AbortSignal): Promise<SequentialSettingsReceipt> =>
  receipt(await request(`/orgs/${slug}/workflows/sequential/settings/operations`, { params: { operation_key: key }, signal }), slug, key);

/** Validate original setup history before it can release mutation custody. */
function setupReceipt(value: SetupReceipt, slug: string, command: SetupCommand): SetupReceipt {
  const fail = () => { throw new TypeError('sequential_settings_receipt'); };
  const same = (a: unknown, b: unknown) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
  const keys = (v: object, expected: string[]) => same(Object.keys(v).sort(), expected.sort());
  canonical(value);
  const admission = command.format === 'workflow-sequential-admission@1';
  const generic = command.definition.compiler === 'workflow-sequential-compiler@2';
  const original = value.original, stored = value.receipt, snapshot = original.snapshot;
  if (!keys(value, ['format', 'org_slug', 'operation_key', 'command', 'receipt', 'original'])
    || value.format !== (admission ? 'workflow-sequential-admission-receipt@1' : generic ? 'workflow-sequential-settings-receipt@2' : 'workflow-sequential-settings-receipt@1')
    || value.org_slug !== slug || value.operation_key !== command.operation_key || !same(value.command, command)
    || !keys(stored, ['format', 'operation_id', 'settings_id', 'generation', 'snapshot_digest'])
    || stored.format !== 'workflow-sequential-settings-receipt-storage@1' || typeof stored.operation_id !== 'string' || !stored.operation_id
    || !keys(original, ['format', 'id', 'generation', 'snapshot_digest', 'snapshot'])
    || stored.settings_id !== original.id || typeof original.id !== 'string' || !original.id
    || !Number.isSafeInteger(original.generation) || original.generation !== command.expected_generation + 1
    || stored.generation !== original.generation || stored.snapshot_digest !== original.snapshot_digest
    || !/^[0-9a-f]{64}$/.test(original.snapshot_digest)
    || original.format !== (generic ? 'workflow-sequential-settings-view@2' : 'workflow-sequential-settings-view@1')
    || snapshot.format !== (generic ? 'workflow-sequential-settings-storage@2' : 'workflow-sequential-settings-storage@1')
    || !keys(snapshot, ['format', 'definition', 'bindings', 'limits', 'admission'])
    || !same(snapshot.definition, command.definition) || !['Allowed', 'Disabled'].includes(snapshot.admission)
    || !Array.isArray(snapshot.bindings) || !snapshot.bindings.every(b => keys(b, ['role', 'principal']) && typeof b.role === 'string' && typeof b.principal === 'string')) fail();
  if (admission) {
    if (snapshot.admission !== command.admission) fail();
  } else if (!same(snapshot.bindings, command.bindings) || !same(snapshot.limits, command.limits)) fail();
  const limits = snapshot.limits;
  if (generic) {
    if (!('max_recoveries' in limits) || !keys(limits, ['max_visits', 'max_events', 'max_recoveries', ...('node_revisions' in limits ? ['node_revisions'] : [])])
      || !Number.isSafeInteger(limits.max_recoveries) || limits.max_recoveries < 0 || limits.max_recoveries > 64
      || (limits.node_revisions !== undefined && (typeof limits.node_revisions !== 'object' || limits.node_revisions === null || Array.isArray(limits.node_revisions)
        || !Object.values(limits.node_revisions).every(v => Number.isSafeInteger(v) && v >= 0 && v <= 63)))) fail();
  } else if (!('content_returns' in limits) || !keys(limits, ['content_returns', 'max_content_epochs', 'max_visits', 'max_events'])
    || !Number.isSafeInteger(limits.content_returns) || limits.content_returns < 0 || limits.content_returns > 2
    || !Number.isSafeInteger(limits.max_content_epochs) || limits.max_content_epochs < 1 || limits.max_content_epochs > 3) fail();
  if (!Number.isSafeInteger(limits.max_visits) || limits.max_visits < 1 || limits.max_visits > 64
    || !Number.isSafeInteger(limits.max_events) || limits.max_events < 1 || limits.max_events > 4096) fail();
  return value;
}
function setupPath(command: SetupCommand): string {
  return command.format === 'workflow-sequential-admission@1' ? 'settings/admission'
    : command.format === 'workflow-sequential-settings-put@2' ? 'settings/v2' : 'settings';
}
/** Exactly one send, with frozen kind/body before token acquisition. */
export const putSequentialSetup = async (slug: string, command: SetupCommand): Promise<SetupReceipt> => {
  const body = freezeSequentialSettingsCommand(command);
  return setupReceipt(await request(`/orgs/${slug}/workflows/sequential/${setupPath(body)}`, {
    method: 'PUT', body, auth: { token: getToken },
  }), slug, body);
};
/** Original lookup validates against the entire original command, including its action. */
export const getSequentialSetupOperation = async (slug: string, command: SetupCommand, signal?: AbortSignal): Promise<SetupReceipt> =>
  setupReceipt(await request(`/orgs/${slug}/workflows/sequential/${setupPath(command)}/operations`, {
    params: { operation_key: command.operation_key }, signal,
  }), slug, command);

/** Additive S7a reads. @1 types and clients above retain their original ABI. */
export type GenericDefinitionPin = Omit<DefinitionPin, 'compiler' | 'validator' | 'interpreter' | 'capabilities'> & {
  compiler: 'workflow-sequential-compiler@2'; validator: 'workflow-sequential-validator@2';
  interpreter: 'workflow-sequential-interpreter@2'; capabilities: 'workflow-sequential-capabilities@2';
};
export type GenericHoldCause = 'task_failed_without_result' | 'task_timed_out_without_result'
  | 'task_failed_after_result' | 'task_timed_out_after_result' | 'result_bad_shape' | 'result_too_large'
  | 'unknown_outcome' | 'assignee_unavailable' | 'revision_limit_exhausted' | 'declared_edge'
  | 'undeclared_outcome' | 'limit_exhausted' | 'settlement_unverified' | 'output_evidence_mismatch'
  | 'exhausted' | 'required_input_unavailable' | 'terminal_evidence_unavailable';
export interface GenericSequentialRun {
  abi: '@2'; history_label: 'current_v2'; id: string; definition_id: string; definition: GenericDefinitionPin;
  settings_id: string; settings: SequentialRun['settings']; initiator_task_id: string; initiator_agent_id: string;
  state: 'running' | 'held' | 'finished'; terminal_outcome: 'completed' | 'failed' | 'cancelled' | null;
  progress: 'queued' | 'executing' | 'result_pending_settlement' | 'cancelling' | null;
  active_visit_id: string | null;
  hold: { event_id: string; node_id: string; visit_id: string; cause: GenericHoldCause } | null;
  visits: number; result_events: number; stored_events: number; recoveries: number; tasks: SequentialRun['tasks'];
}
export interface HistoricalSequentialRun { abi: '@1'; history_label: 'historical_v1'; run: SequentialRun }
export type SequentialRunLinkV2 = GenericSequentialRun | HistoricalSequentialRun;
export interface SequentialRunsV2 {
  format: 'workflow-sequential-runs-view@2'; org_slug: string; storage: 'absent' | 'readable'; runs: SequentialRunLinkV2[];
}
export interface SequentialTaskRuns {
  format: 'workflow-sequential-task-runs@2'; org_slug: string; task_id: string;
  storage: 'absent' | 'readable'; runs: SequentialRunLinkV2[];
}
export interface SequentialAssignment {
  id: string; node_id: string; role: string; generation: number; principal: string;
  principal_source: 'bundled' | 'org' | 'run' | 'control'; predecessor_id: string | null; snapshot_digest: string;
  requested: Record<string, string | null>;
  origins: { model: 'agent_default' | 'run' | 'clear'; reasoning_effort: 'agent_default' | 'run' | 'clear' };
  selection: { model_override: string | null; reasoning_effort_override: string | null; override_source: 'founder' | 'manager' | null };
  effective: { model: string | null; reasoning_effort: string | null };
  executor: { name: string; kind: string; workspace_adapter_id: string | null; command_adapter_id: string | null };
}
export interface SequentialControlRecord {
  id: string; event_id: string; event_sequence: number; operation_key: string;
  action: 'retry' | 'rebrief' | 'reassign' | 'cancel'; actor_task_id: string; actor_principal: string;
  actor_session_id: string; actor_result_id: number; expected_cas: number; expected_held_event_id: string | null;
  new_visit_id: string | null; request_digest: string; receipt_digest: string;
}
export interface GenericSequentialRunDetail {
  format: 'workflow-sequential-run-view@2'; org_slug: string; run: GenericSequentialRun; definition_json: string; request: string;
  inputs: SequentialRunDetail['inputs']; assignments: SequentialAssignment[]; controls: SequentialControlRecord[];
  native_cancellations: { event_id: string; event_sequence: number; kind: 'cancelled' | 'cancellation_finished';
    origin: 'founder_cancel' | 'source_terminal'; source_task_id: string;
    source_status: 'pending' | 'in_progress' | 'escalated' | 'completed' | 'failed' | 'cancelled' | 'superseded';
    visit_id: string; held_event_id: string | null }[];
  visits: { id: string; node_id: string; ordinal: number; state: string; selected: boolean;
    assignment_id: string; assignment_generation: number; accepted_result_id: number | null; outcome: string | null; findings: string[];
    content_provenance: 'agent_reported';
    native: { kind: 'engine_authenticated'; task_id: string; principal: string; session_id: string | null;
      result_id: number | null; terminal_digest: string | null; outer_digest: string | null } }[];
  outputs: { id: string; visit_id: string; slot: string; contract: 'canonical-json-object@1' | 'immutable-document@1';
    content_digest: string; content_utf8: string; task_id: string; session_id: string; result_id: number;
    content_provenance: 'agent_reported' }[];
}
export interface HistoricalSequentialRunDetail {
  format: 'workflow-sequential-history-view@2'; org_slug: string; abi: '@1'; history_label: 'historical_v1'; detail: SequentialRunDetail;
}
export type SequentialRunDetailV2 = GenericSequentialRunDetail | HistoricalSequentialRunDetail;
export type SequentialProjection<T> = T | SequentialProjectionUnavailable;

/** Preserve finite 409 evidence failures; 403/404 must reach shared access fencing. */
async function projectionV2<T>(path: string, slug: string, signal?: AbortSignal): Promise<SequentialProjection<T>> {
  try { return await request<T>(path, { signal }); }
  catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      const value = error.detail as Partial<SequentialProjectionUnavailable> | null;
      if (value?.format === 'workflow-sequential-unavailable@2' && value.state === 'unavailable'
        && value.org_slug === slug && ['history_unverified', 'read_budget_exhausted', 'authority_unverified', 'authority_denied'].includes(value.code ?? '')) {
        return value as SequentialProjectionUnavailable;
      }
    }
    throw error;
  }
}
export const listSequentialRunsV2 = (slug: string, signal?: AbortSignal): Promise<SequentialProjection<SequentialRunsV2>> =>
  projectionV2(`/orgs/${encodeURIComponent(slug)}/workflows/sequential/views/v2/runs`, slug, signal);
export const getSequentialRunV2 = (slug: string, id: string, signal?: AbortSignal): Promise<SequentialProjection<SequentialRunDetailV2>> =>
  projectionV2(`/orgs/${encodeURIComponent(slug)}/workflows/sequential/views/v2/runs/${encodeURIComponent(id)}`, slug, signal);
export const getTaskSequentialWorkflows = (slug: string, id: string, signal?: AbortSignal): Promise<SequentialProjection<SequentialTaskRuns>> =>
  projectionV2(`/orgs/${encodeURIComponent(slug)}/tasks/${encodeURIComponent(id)}/sequential-workflows`, slug, signal);
