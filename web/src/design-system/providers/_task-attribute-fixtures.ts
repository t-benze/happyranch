/**
 * Deterministic task-attribute definition fixtures (THR-390 PR6), in the PR2
 * wire shapes exactly. SYNTHETIC data only; these are prototype/mock
 * observations, never daemon proof.
 *
 * The mock provider (`_mock-task-attributes.ts`) serves the default fixture.
 * Each named fixture exists for a state the real daemon cannot produce for a
 * browser (PR2 browser reads are always human, so read-only, unknown
 * authority, unknown refusal codes, permission refusals and contradictory
 * probes are mock-only). This module has type-only imports, so a Node
 * evidence driver can load the same bodies.
 */
import type {
  AttributeDefinition,
  AttributeDefinitionEditability,
  AttributeDefinitionRead,
  AttributeDefinitionsRead,
} from '@/lib/api/taskAttributeTypes';

export interface TaskAttributeFixtureResponse {
  status: number;
  body: unknown;
}

export interface TaskAttributeFixture {
  /** Why this state needs a mock (shown in evidence, never in the UI). */
  reason: string;
  list: AttributeDefinitionsRead;
  /** Per-definition reads by definition_id. */
  definitions: Record<string, AttributeDefinitionRead>;
  /** Older versions by definition_id (ascending by version). */
  versions?: Record<string, AttributeDefinitionRead[]>;
  /** Canned write answers; absent → the mock applies the write. */
  writes?: Partial<Record<'create' | 'update' | 'archive', TaskAttributeFixtureResponse>>;
}

const T0 = '2026-11-20T02:00:00Z';
const T1 = '2026-11-21T08:07:00Z';
const T2 = '2026-11-22T06:30:00Z';

const ALLOWED: AttributeDefinitionEditability = {
  can_create: true, can_edit_metadata: true, can_edit_structure: true, can_archive: true, refusal_reason: null,
};
const deny = (code: string): AttributeDefinitionEditability => ({
  can_create: false, can_edit_metadata: false, can_edit_structure: false, can_archive: false, refusal_reason: { code },
});

function def(id: string, fields: Partial<AttributeDefinition> & Pick<AttributeDefinition, 'key' | 'type' | 'name'>): AttributeDefinition {
  return {
    definition_id: id, version: 1, description: null, constraints: {}, created_at: T0, recorded_at: T0,
    first_used_at: null, archived_at: null, archived: false, ...fields,
  };
}

/** The server's per-definition editability for a definition (PR2 rules). */
function serverEditability(d: AttributeDefinition): AttributeDefinitionEditability {
  if (d.archived) return deny('definition_archived');
  return { ...ALLOWED, can_edit_structure: d.first_used_at === null };
}

const EFFORT = def('def_effort', {
  key: 'effort', type: 'numeric', name: 'Effort', version: 1,
  description: 'SYNTHETIC rubric 1–10', constraints: { min: 1, max: 10, integer_only: true },
});
const COMPLEXITY = def('def_complexity', { key: 'complexity', type: 'numeric', name: 'Complexity', constraints: { min: 0, max: 5 } });
const CUSTOMER = def('def_customer', { key: 'customer_facing', type: 'boolean', name: 'Customer facing' });
const AREA = def('def_area', { key: 'area', type: 'string', name: 'Area', constraints: { min_length: 1, max_length: 40 } });
const LEGACY = def('def_legacy', {
  key: 'legacy_risk', type: 'numeric', name: 'Legacy risk', version: 2, recorded_at: T1, archived: true, archived_at: T1,
});
const BASE = [EFFORT, COMPLEXITY, CUSTOMER, AREA, LEGACY];

function reads(defs: AttributeDefinition[], override: Record<string, AttributeDefinitionEditability> = {}): Record<string, AttributeDefinitionRead> {
  return Object.fromEntries(defs.map((d) => [d.definition_id, { definition: d, editability: override[d.definition_id] ?? serverEditability(d) }]));
}

const ready: TaskAttributeFixture = {
  reason: 'Default prototype state: the F1 seed with Founder authority.',
  list: { definitions: BASE, editability: ALLOWED },
  definitions: reads(BASE),
  versions: { def_legacy: [{ definition: { ...LEGACY, version: 1, archived: false, archived_at: null, recorded_at: T0 }, editability: deny('definition_archived') }] },
};

const listDenied = (code: string, reason: string, defs = BASE): TaskAttributeFixture => ({
  reason, list: { definitions: defs, editability: deny(code) }, definitions: reads(defs, Object.fromEntries(defs.map((d) => [d.definition_id, deny(code)]))),
});

export const TASK_ATTRIBUTE_FIXTURES = {
  'ta.list.ready': ready,
  'ta.list.empty.readonly': listDenied('human_only', 'Browser reads are always human on PR2; read-only needs a mock.', []),
  'ta.list.readonly': listDenied('human_only', 'Browser reads are always human on PR2; read-only needs a mock.'),
  'ta.list.authUnknown': listDenied('authority_unknown', 'PR2 never reports unknown authority to a browser read.'),
  'ta.list.refusal.future': listDenied('some_future_code', 'An unknown refusal code must render generic copy.'),
  'ta.list.probe.activeRefused': {
    reason: 'Contradictory probe: an active-looking row whose own read refuses.',
    list: { definitions: BASE, editability: ALLOWED },
    definitions: reads(BASE, { def_area: deny('human_only') }),
  },
  'ta.list.probe.archivedMetaAllowed': {
    reason: 'Contradictory probe (PR2 never produces it): archived data with explicit server permission.',
    list: { definitions: BASE, editability: ALLOWED },
    definitions: reads(BASE, { def_legacy: { ...ALLOWED, can_edit_structure: false } }),
  },
  'ta.list.probe.topLevelDenied': {
    reason: 'Top-level denial must win; per-definition reads would allow.',
    list: { definitions: BASE, editability: deny('human_only') },
    definitions: reads(BASE),
  },
  'ta.list.probe.rowFuture': {
    reason: 'One row read refuses with an unknown code.',
    list: { definitions: BASE, editability: ALLOWED },
    definitions: reads(BASE, { def_complexity: deny('some_future_code') }),
  },
  'ta.def.inferenceProbe': {
    reason: 'first_used_at set but the server still allows structure edits: fields must be editable.',
    list: { definitions: BASE, editability: ALLOWED },
    definitions: { ...reads(BASE), def_effort: { definition: { ...EFFORT, first_used_at: T1 }, editability: ALLOWED } },
  },
  'ta.def.readonly': listDenied('human_only', 'Detail opened from a read-only list.'),
  'ta.def.echoMismatch': {
    reason: 'A 200 whose body does not reflect the submitted change: no success may be shown.',
    list: { definitions: BASE, editability: ALLOWED },
    definitions: reads(BASE),
    writes: { update: { status: 200, body: { definition: { ...COMPLEXITY, version: 2, recorded_at: T2 }, editability: ALLOWED } } },
  },
  'ta.def.archivedTop': {
    reason: 'The shipped UI never writes at an archived current version; the server branch is proven by a direct request.',
    list: { definitions: BASE, editability: ALLOWED },
    definitions: reads(BASE),
    writes: (() => {
      const current = { definition: { ...AREA, version: 2, recorded_at: T2, archived: true, archived_at: T2 }, editability: deny('definition_archived') };
      const refusal = { status: 409, body: { detail: { code: 'definition_archived', current } } };
      return { update: refusal, archive: refusal };
    })(),
  },
  'ta.write.humanOnly': {
    reason: 'Revoking the real bearer would touch credential custody; a 403 human_only write needs a mock.',
    list: { definitions: BASE, editability: ALLOWED },
    definitions: reads(BASE),
    writes: {
      create: { status: 403, body: { detail: { code: 'human_only' } } },
      update: { status: 403, body: { detail: { code: 'human_only' } } },
      archive: { status: 403, body: { detail: { code: 'human_only' } } },
    },
  },
} satisfies Record<string, TaskAttributeFixture>;

export type TaskAttributeFixtureKey = keyof typeof TASK_ATTRIBUTE_FIXTURES;
export const DEFAULT_TASK_ATTRIBUTE_FIXTURE: TaskAttributeFixtureKey = 'ta.list.ready';
