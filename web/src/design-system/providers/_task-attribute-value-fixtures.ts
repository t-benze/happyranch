/**
 * Deterministic task-attribute VALUE fixtures (THR-390 PR7), in the PR2 wire
 * shapes exactly. SYNTHETIC data only: prototype/mock observations, never
 * daemon proof. Each named fixture exists for a state a real daemon cannot
 * produce for a browser (browser reads are always the Founder path, so
 * task-level refusals and contradictory actor probes are mock-only).
 * Type-only imports, so a Node evidence driver can load the same bodies.
 */
import type {
  AttributeDefinition,
  AttributeEvent,
  TaskAttributeRead,
  TaskAttributesRead,
} from '@/lib/api/taskAttributeTypes';

const T0 = '2026-11-20T02:00:00Z';
const T1 = '2026-11-21T08:05:00Z';
const T2 = '2026-11-22T06:30:00Z';

function def(id: string, f: Partial<AttributeDefinition> & Pick<AttributeDefinition, 'key' | 'type' | 'name'>): AttributeDefinition {
  return {
    definition_id: id, version: 1, description: null, constraints: {}, created_at: T0, recorded_at: T0,
    first_used_at: T0, archived_at: null, archived: false, ...f,
  };
}

const EFFORT = def('def_effort', { key: 'effort', type: 'numeric', name: 'Effort', constraints: { min: 1, max: 10, integer_only: true } });
const CUSTOMER = def('def_customer', { key: 'customer_facing', type: 'boolean', name: 'Customer facing' });
const AREA = def('def_area', { key: 'area', type: 'string', name: 'Area', constraints: { min_length: 1, max_length: 40 } });

const ADMISSION = { state: 'recorded' as const, actor_id: 'fixture_agent', actor_kind: 'agent', at: T0 };
const ALLOW = { can_edit: true, refusal_reason: null };

function event(d: AttributeDefinition, revision: number, actor: { id: string; kind: string; name: string | null }, at: string,
  old: AttributeEvent['old'], next: AttributeEvent['new'], source: AttributeEvent['source'] = 'edit'): AttributeEvent {
  return {
    event_id: revision * 10 + d.key.length, definition_id: d.definition_id, definition_version: d.version, definition: d,
    revision, actor_id: actor.id, actor_kind: actor.kind, actor: { canonical_id: actor.id, kind: actor.kind, addressable_name: actor.name },
    at, source, old, new: next,
  };
}

const FOUNDER = { id: 'founder', kind: 'founder', name: null };

function readyRead(): TaskAttributesRead {
  const effortAdmit = event(EFFORT, 1, { id: 'fixture_agent', kind: 'agent', name: null }, T0, { present: false }, { present: true, value: 4 }, 'admission');
  const effortEdit = event(EFFORT, 2, FOUNDER, T1, { present: true, value: 4 }, { present: true, value: 5 });
  const attrs: TaskAttributeRead[] = [
    {
      definition: EFFORT, current: { present: true, value: 5, revision: 2, definition_version: 1, updated_at: T1 },
      initial: { state: 'set', value: 4, definition_version: 1, definition: EFFORT }, admission: ADMISSION,
      provenance: 'supplied_at_admission', history: { history_state: 'complete', events: [effortAdmit, effortEdit] }, editability: ALLOW,
    },
    {
      definition: CUSTOMER, current: { present: false, revision: 0 }, initial: { state: 'unset' }, admission: ADMISSION,
      provenance: 'not_supplied_before_dispatch', history: { history_state: 'complete', events: [] }, editability: ALLOW,
    },
    {
      definition: AREA, current: { present: false, revision: 0 }, initial: { state: 'unset' }, admission: ADMISSION,
      provenance: 'not_supplied_before_dispatch', history: { history_state: 'complete', events: [] }, editability: ALLOW,
    },
  ];
  return { task_id: 'TASK-MOCK-1', editability: ALLOW, attributes: attrs };
}

/** Top-level and per-field refusal with `code`. */
function refused(code: string): TaskAttributesRead {
  const r = readyRead();
  const deny = { can_edit: false, refusal_reason: { code } };
  return { ...r, editability: deny, attributes: r.attributes.map((a) => ({ ...a, editability: deny })) };
}

/** Two events where kind-driven and id-driven rendering disagree (COR2-02). */
function kindProbe(): TaskAttributesRead {
  const r = readyRead();
  const a = r.attributes[0] as TaskAttributeRead;
  const probeA = event(EFFORT, 3, { id: 'founder', kind: 'agent', name: null }, T2, { present: true, value: 5 }, { present: true, value: 6 });
  const probeB = event(EFFORT, 4, { id: 'synth_owner', kind: 'founder', name: 'SYNTH-Owner' }, T2, { present: true, value: 6 }, { present: true, value: 7 });
  r.attributes[0] = { ...a, current: { present: true, value: 7, revision: 4 }, history: { history_state: 'complete', events: [...a.history.events, probeA, probeB] } };
  return r;
}

export interface TaskAttributeValueFixture {
  /** Why this state needs a mock (evidence only, never shown in the UI). */
  reason: string;
  /** First read (navigation). */
  read: TaskAttributesRead;
  /** Later reads (editor mount, Reload latest); absent → `read`. */
  reread?: TaskAttributesRead;
  /** Canned PUT answer; absent → the mock applies the write. */
  save?: { status: number; body: unknown };
}

const REFUSAL_CODES = [
  'not_task_owner_or_owning_manager', 'authority_unknown', 'session_not_current',
  'recovery_purpose_forbidden', 'task_not_active', 'some_future_code',
] as const;

export const TASK_ATTRIBUTE_VALUE_FIXTURES: Record<string, TaskAttributeValueFixture> = {
  'tav.ready': { reason: 'Default prototype state with Founder authority.', read: readyRead() },
  'tav.kindProbe': { reason: 'A browser only ever writes founder/founder; kind-vs-id probe events are synthetic.', read: kindProbe() },
  ...Object.fromEntries(REFUSAL_CODES.flatMap((code) => [
    [`tav.refused.${code}`, { reason: 'Task-level refusals never reach a browser (always Founder).', read: refused(code) }],
    [`tav.entryRefused.${code}`, { reason: 'Editor-mount re-read refusal is mock-only.', read: readyRead(), reread: refused(code) }],
    [`tav.save403.${code}`, {
      reason: 'Save-time task refusal is mock-only.', read: readyRead(), reread: refused(code),
      save: { status: 403, body: { detail: { code } } },
    }],
  ])),
};

export const DEFAULT_TASK_ATTRIBUTE_VALUE_FIXTURE = 'tav.ready';
