/** Inert isolated sandbox contract. It never sends a response or allocates an author. */
import type { WorkflowsApi, WorkflowQuery } from './DataContext';
import type * as Seq from '@/lib/api/workflowSequential';
import type { SequentialProjectionUnavailable } from '@/lib/api/types';
function empty<T>(data?: T): WorkflowQuery<T> {
  return { data, isLoading: false, isError: false, error: null, isFetching: false, refetch: async () => undefined };
}
export const mockWorkflowsApi: WorkflowsApi = {
  useWorkflowSetupStatus: () => empty({ format: 'workflow-setup-status@1', org_slug: 'sandbox',
    state: 'unknown', reason: 'evidence_unavailable', attempt_time: null, backup_path: null, integrity_report: null, writer_blockers: null }),
  useSequentialSettingsFence: () => null,
  useSequentialSettingsActions: () => ({
    save: async () => { throw new Error('workflow_display_only'); },
    admission: async () => { throw new Error('workflow_display_only'); },
    adoptConflict: () => { throw new Error('workflow_display_only'); },
    readOperation: async () => { throw new Error('workflow_display_only'); },
  }),
  useSequentialSetupDefinitions: () => empty(), useSequentialSetupSettings: () => empty(),
  useSequentialDefinitions: () => empty(), useSequentialDefinition: () => empty(),
  useSequentialSettings: () => empty(), useSequentialRuns: () => empty(),
  useSequentialRun: () => empty(), useTaskSequentialWorkflow: () => empty(),
};

/*
 * S7b deterministic @2 projection states (synthetic values only). The S7a @2 hooks read
 * the real API directly (not through WorkflowsApi), so these typed fixtures are the
 * screenshot harness's served daemon responses: they type-check against the S7a wire
 * types and cover plural_links, held_cause, history_label, unavailable and late_org_response.
 */

const MOCK_ORG = 'demo';
const digest = (n: number) => `sha256:${String(n).repeat(64).slice(0, 64)}`;
const pin: Seq.GenericDefinitionPin = {
  identity: 'generic-review', version: 2, definition_digest: digest(1), source_digest: digest(2),
  compiler: 'workflow-sequential-compiler@2', validator: 'workflow-sequential-validator@2',
  interpreter: 'workflow-sequential-interpreter@2', capabilities: 'workflow-sequential-capabilities@2',
};
type Task = Seq.SequentialRun['tasks'][number];
const source = (task_id: string): Task => ({ task_id, relationship: 'source', visit_id: null });
const visitLink = (task_id: string, visit_id: string): Task => ({ task_id, relationship: 'visit', visit_id });
function generic(id: string, over: Partial<Seq.GenericSequentialRun>): Seq.GenericSequentialRun {
  return {
    abi: '@2', history_label: 'current_v2', id, definition_id: 'DEF-0002', definition: pin,
    settings_id: 'SET-0002', settings: { generation: 3, snapshot_digest: digest(3) },
    initiator_task_id: 'TASK-1100', initiator_agent_id: 'engineering_manager',
    state: 'running', terminal_outcome: null, progress: 'executing', active_visit_id: null, hold: null,
    visits: 1, result_events: 0, stored_events: 2, recoveries: 0, tasks: [source('TASK-1100')], ...over,
  };
}
const RUN_RUNNING = generic('RUN-0001', { active_visit_id: 'VIS-0102', visits: 2, result_events: 1, stored_events: 5,
  tasks: [source('TASK-1100'), visitLink('TASK-1101', 'VIS-0101'), visitLink('TASK-1102', 'VIS-0102')] });
const RUN_HELD = generic('RUN-0002', { state: 'held', progress: null, visits: 3, result_events: 3, stored_events: 9, recoveries: 1,
  hold: { event_id: 'EVT-0209', node_id: 'review', visit_id: 'VIS-0203', cause: 'declared_edge' },
  tasks: [source('TASK-1100'), visitLink('TASK-1201', 'VIS-0201'), visitLink('TASK-1202', 'VIS-0202'), visitLink('TASK-1203', 'VIS-0203')] });
const RUN_HELD_ASSIGNEE = generic('RUN-0003', { state: 'held', progress: null, initiator_task_id: 'TASK-1300', initiator_agent_id: 'product_manager',
  hold: { event_id: 'EVT-0302', node_id: 'draft', visit_id: 'VIS-0301', cause: 'assignee_unavailable' },
  tasks: [source('TASK-1300'), visitLink('TASK-1301', 'VIS-0301')] });
const RUN_SETTLING = generic('RUN-0004', { progress: 'result_pending_settlement', initiator_task_id: 'TASK-1300', initiator_agent_id: 'product_manager',
  tasks: [source('TASK-1300'), visitLink('TASK-1401', 'VIS-0401')] });
const RUN_CANCELLING = generic('RUN-0005', { progress: 'cancelling', tasks: [source('TASK-1100'), visitLink('TASK-1501', 'VIS-0501')] });
const RUN_COMPLETED = generic('RUN-0006', { state: 'finished', terminal_outcome: 'completed', progress: null, visits: 2, result_events: 2, stored_events: 6,
  tasks: [source('TASK-1100'), visitLink('TASK-1601', 'VIS-0601'), visitLink('TASK-1602', 'VIS-0602')] });
const RUN_FAILED = generic('RUN-0007', { state: 'finished', terminal_outcome: 'failed', progress: null, tasks: [source('TASK-1300'), visitLink('TASK-1701', 'VIS-0701')] });
const RUN_CANCELLED = generic('RUN-0008', { state: 'finished', terminal_outcome: 'cancelled', progress: null, visits: 1, result_events: 1, stored_events: 4,
  tasks: [source('TASK-1100'), visitLink('TASK-1801', 'VIS-0801')] });
const HISTORICAL_RUN: Seq.SequentialRun = {
  id: 'RUN-H001', definition_id: 'DEF-CODING-2', definition: { ...pin, identity: 'coding', version: 2, compiler: 'workflow-sequential-compiler@1',
    validator: 'workflow-sequential-validator@1', interpreter: 'workflow-sequential-interpreter@1', capabilities: 'workflow-sequential-capabilities@1' },
  settings_id: 'SET-CODING', settings: { generation: 1, snapshot_digest: digest(4) }, state: 'completed', active_visit_id: null,
  counters: { epoch: 1, content_returns: 0, visits: 4, events: 8 },
  tasks: [source('TASK-0900'), { task_id: 'TASK-0901', relationship: 'carrier', visit_id: null }, visitLink('TASK-0902', 'VIS-H01')],
};
const HISTORICAL: Seq.HistoricalSequentialRun = { abi: '@1', history_label: 'historical_v1', run: HISTORICAL_RUN };
const ALL_RUNS: Seq.SequentialRunLinkV2[] = [RUN_RUNNING, RUN_HELD, RUN_HELD_ASSIGNEE, RUN_SETTLING, RUN_CANCELLING, RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED, HISTORICAL];

function assignment(id: string, node_id: string, role: string, generation: number, principal: string,
  principal_source: Seq.SequentialAssignment['principal_source'], predecessor_id: string | null,
  model: [string | null, Seq.SequentialAssignment['origins']['model']], effort: [string | null, Seq.SequentialAssignment['origins']['model']]): Seq.SequentialAssignment {
  return { id, node_id, role, generation, principal, principal_source, predecessor_id, snapshot_digest: digest(5 + generation),
    requested: { model: model[1] === 'run' ? model[0] : null, reasoning_effort: effort[1] === 'run' ? effort[0] : null },
    origins: { model: model[1], reasoning_effort: effort[1] },
    selection: { model_override: null, reasoning_effort_override: principal_source === 'control' ? 'high' : null, override_source: principal_source === 'control' ? 'manager' : null },
    effective: { model: model[1] === 'clear' ? null : model[0], reasoning_effort: effort[1] === 'clear' ? null : effort[0] },
    executor: { name: 'claude', kind: 'claude_code', workspace_adapter_id: null, command_adapter_id: null } };
}
type Visit = Seq.GenericSequentialRunDetail['visits'][number];
function visit(id: string, ordinal: number, node_id: string, task_id: string, principal: string, assignment_id: string, assignment_generation: number,
  state: string, outcome: string | null, accepted: number | null, nativeResult: number | null, findings: string[]): Visit {
  return { id, node_id, ordinal, state, selected: true, assignment_id, assignment_generation, accepted_result_id: accepted, outcome, findings,
    content_provenance: 'agent_reported',
    native: { kind: 'engine_authenticated', task_id, principal, session_id: nativeResult === null ? null : `sess-${id.toLowerCase()}`,
      result_id: nativeResult, terminal_digest: nativeResult === null ? null : digest(7), outer_digest: nativeResult === null ? null : digest(8) } };
}
const DEFINITION_TEXT = JSON.stringify({ format: 'workflow-sequential-definition@2', identity: 'generic-review', version: 2,
  description: 'Draft, review and publish a document.', entry_node: 'draft',
  nodes: [{ id: 'draft', role: 'author' }, { id: 'review', role: 'reviewer' }, { id: 'publish', role: 'publisher' }] });
const HELD_DETAIL: Seq.GenericSequentialRunDetail = {
  format: 'workflow-sequential-run-view@2', org_slug: MOCK_ORG, run: RUN_HELD, definition_json: DEFINITION_TEXT,
  request: 'Draft the Q4 onboarding guide, have it reviewed, then publish it.',
  inputs: [{ name: 'request', contract: 'bounded-text@1', value: 'Draft the Q4 onboarding guide, have it reviewed, then publish it.' },
    { name: 'audience', contract: 'bounded-text@1', value: 'New engineering hires' }],
  assignments: [
    assignment('ASG-0201', 'draft', 'author', 1, 'technical_writer', 'org', null, ['claude-sonnet-5-5', 'run'], ['medium', 'agent_default']),
    assignment('ASG-0202', 'review', 'reviewer', 1, 'code_reviewer', 'bundled', null, ['claude-opus-5-5', 'agent_default'], [null, 'clear']),
    assignment('ASG-0203', 'review', 'reviewer', 2, 'qa_engineer', 'control', 'ASG-0202', ['claude-opus-5-5', 'agent_default'], ['high', 'run']),
  ],
  controls: [
    { id: 'CTL-0201', event_id: 'EVT-0205', event_sequence: 5, operation_key: 'op-retry-review-1', action: 'retry',
      actor_task_id: 'TASK-1100', actor_principal: 'engineering_manager', actor_session_id: 'sess-mgr-0001', actor_result_id: 4101,
      expected_cas: 4, expected_held_event_id: 'EVT-0204', new_visit_id: 'VIS-0202', request_digest: digest(1), receipt_digest: digest(2) },
    { id: 'CTL-0202', event_id: 'EVT-0207', event_sequence: 7, operation_key: 'op-reassign-review-1', action: 'reassign',
      actor_task_id: 'TASK-1100', actor_principal: 'engineering_manager', actor_session_id: 'sess-mgr-0002', actor_result_id: 4102,
      expected_cas: 6, expected_held_event_id: 'EVT-0206', new_visit_id: 'VIS-0203', request_digest: digest(3), receipt_digest: digest(4) },
  ],
  native_cancellations: [],
  visits: [
    visit('VIS-0201', 1, 'draft', 'TASK-1201', 'technical_writer', 'ASG-0201', 1, 'completed', 'produced', 5101, 5101, ['Draft covers setup, access and first-week tasks.']),
    visit('VIS-0202', 2, 'review', 'TASK-1202', 'code_reviewer', 'ASG-0202', 1, 'failed', null, null, 5102, ['Callback carried an undeclared outcome.']),
    visit('VIS-0203', 3, 'review', 'TASK-1203', 'qa_engineer', 'ASG-0203', 2, 'held', 'merged', 5103, 5103,
      ['Reported: merged at head 0123456789abcdef0123456789abcdef01234567 and APPROVED.', 'Holding for founder sign-off as declared.']),
  ],
  outputs: [
    { id: 'OUT-0201', visit_id: 'VIS-0201', slot: 'draft', contract: 'immutable-document@1', content_digest: digest(9),
      content_utf8: '# Q4 onboarding guide\n\n1. Request access.\n2. Pair with a buddy.\n', task_id: 'TASK-1201', session_id: 'sess-vis-0201', result_id: 5101, content_provenance: 'agent_reported' },
    { id: 'OUT-0203', visit_id: 'VIS-0203', slot: 'review', contract: 'canonical-json-object@1', content_digest: digest(6),
      content_utf8: '{"head":"0123456789abcdef0123456789abcdef01234567","merged":true,"verdict":"APPROVE"}', task_id: 'TASK-1203', session_id: 'sess-vis-0203', result_id: 5103, content_provenance: 'agent_reported' },
  ],
};
const CANCELLED_DETAIL: Seq.GenericSequentialRunDetail = {
  ...HELD_DETAIL, run: RUN_CANCELLED, request: 'Publish the release notes.',
  inputs: [{ name: 'request', contract: 'bounded-text@1', value: 'Publish the release notes.' }],
  assignments: [HELD_DETAIL.assignments[0]], controls: [],
  native_cancellations: [
    { event_id: 'EVT-0803', event_sequence: 3, kind: 'cancelled', origin: 'founder_cancel', source_task_id: 'TASK-1100', source_status: 'cancelled', visit_id: 'VIS-0801', held_event_id: null },
    { event_id: 'EVT-0804', event_sequence: 4, kind: 'cancellation_finished', origin: 'source_terminal', source_task_id: 'TASK-1100', source_status: 'cancelled', visit_id: 'VIS-0801', held_event_id: 'EVT-0802' },
  ],
  visits: [visit('VIS-0801', 1, 'draft', 'TASK-1801', 'technical_writer', 'ASG-0201', 1, 'cancelled', 'produced', null, 5801, ['Draft finished just before cancellation.'])],
  outputs: [],
};
const HISTORICAL_DETAIL: Seq.HistoricalSequentialRunDetail = {
  format: 'workflow-sequential-history-view@2', org_slug: MOCK_ORG, abi: '@1', history_label: 'historical_v1',
  detail: {
    format: 'workflow-sequential-run-view@1', run: HISTORICAL_RUN, request: 'Fix the flaky login test.',
    bindings: [{ role: 'developer', principal: 'frontend_engineer' }, { role: 'reviewer', principal: 'code_reviewer' }],
    limits: { content_returns: 2, max_content_epochs: 3, max_visits: 12, max_events: 48 },
    effective_limits: { content_returns: 2, max_content_epochs: 3, max_visits: 12, max_events: 48 },
    reducer_counters: { epoch: 1, content_returns: 0, visits: 4, events: 8 },
    inputs: [{ name: 'request', contract: 'bounded-text@1', value: 'Fix the flaky login test.' }], holds: [],
    visits: [{ id: 'VIS-H01', node_id: 'develop', ordinal: 1, epoch: 1, task_id: 'TASK-0902', principal: 'frontend_engineer',
      state: 'completed', selected: true, outcome: 'produced', findings: [] },
    { id: 'VIS-H04', node_id: 'finalize', ordinal: 4, epoch: 1, task_id: 'TASK-0905', principal: 'engineering_manager',
      state: 'completed', selected: true, outcome: 'merged', findings: [] }],
    outputs: [{ id: 'OUT-H04', visit_id: 'VIS-H04', slot: 'merge', contract: 'guarded-repository-merge@1', epoch: 1, content_digest: digest(5),
      content_utf8: '{"format":"workflow-sequential-coding-final-summary@1","outcome":"merged"}' }],
  },
};
const unavailable = (code: SequentialProjectionUnavailable['code']): SequentialProjectionUnavailable =>
  ({ format: 'workflow-sequential-unavailable@2', state: 'unavailable', org_slug: MOCK_ORG, code });

/** Every named S7b case as the daemon would serve it (HTTP 200 bodies, or the 409 unavailable body). */
export const sequentialV2MockStates = {
  org: MOCK_ORG,
  runs: { format: 'workflow-sequential-runs-view@2', org_slug: MOCK_ORG, storage: 'readable', runs: ALL_RUNS } satisfies Seq.SequentialRunsV2,
  runsAbsent: { format: 'workflow-sequential-runs-view@2', org_slug: MOCK_ORG, storage: 'absent', runs: [] } satisfies Seq.SequentialRunsV2,
  runsForeignOrg: { format: 'workflow-sequential-runs-view@2', org_slug: 'other-org', storage: 'readable', runs: [RUN_COMPLETED] } satisfies Seq.SequentialRunsV2,
  details: { 'RUN-0002': HELD_DETAIL, 'RUN-0008': CANCELLED_DETAIL, 'RUN-H001': HISTORICAL_DETAIL } satisfies Record<string, Seq.SequentialRunDetailV2>,
  /** TASK-1100 initiated several runs; it is linked to each of them plus one @1 run. */
  taskRuns: { format: 'workflow-sequential-task-runs@2', org_slug: MOCK_ORG, task_id: 'TASK-1100', storage: 'readable',
    runs: [RUN_RUNNING, RUN_HELD, RUN_CANCELLING, RUN_COMPLETED, RUN_CANCELLED, { ...HISTORICAL, run: { ...HISTORICAL_RUN, tasks: [...HISTORICAL_RUN.tasks, source('TASK-1100')] } }] } satisfies Seq.SequentialTaskRuns,
  unavailable: {
    history_unverified: unavailable('history_unverified'), read_budget_exhausted: unavailable('read_budget_exhausted'),
    authority_unverified: unavailable('authority_unverified'), authority_denied: unavailable('authority_denied'),
  } satisfies Record<SequentialProjectionUnavailable['code'], SequentialProjectionUnavailable>,
} as const;
