import { describe, expect, test } from 'vitest';
import { translate, type MessageKey, type MessageParams } from '@/lib/i18n';
import type { AgentAssignmentFacts } from './skills-detail';
import {
  CONFIG_REVIEW_NOTE,
  changeCount,
  desiredAssigned,
  isChanged,
  previewProvenance,
  reviewChanges,
  rosterAssignments,
  toggleAssignment,
  toggleLabel,
  type PendingAssignments,
} from './skills-assign';

/** Copy assertions run against the rendered English catalog text (THR-118 W4c). */
const en = (key: MessageKey, params?: MessageParams): string => translate('en', key, params);

// Committed per-agent facts spanning the full vocabulary: an effective agent,
// an assigned-not-yet-effective agent, and a not-assigned agent.
const EFFECTIVE: AgentAssignmentFacts = {
  agent: 'partner_liaison',
  assigned: true,
  effective: true,
  state: 'effective',
};
const NOT_YET: AgentAssignmentFacts = {
  agent: 'support_agent',
  assigned: true,
  effective: false,
  state: 'assigned_not_yet_effective',
};
const UNASSIGNED: AgentAssignmentFacts = {
  agent: 'ops_agent',
  assigned: false,
  effective: false,
};
const ALL = [EFFECTIVE, NOT_YET, UNASSIGNED];

describe('rosterAssignments — full candidate roster from real agents ∪ status', () => {
  // The status response carries ONLY assigned agents (production skips
  // unassigned). partner_liaison/support_agent are assigned; ops_agent and
  // finance_agent are roster-only unassigned candidates.
  const STATUS = [EFFECTIVE, NOT_YET];

  test('synthesizes a not_assigned row for a roster agent absent from status', () => {
    const rows = rosterAssignments(
      ['partner_liaison', 'support_agent', 'ops_agent', 'finance_agent'],
      STATUS,
    );
    // Assigned agents keep their committed row verbatim.
    expect(rows.find((r) => r.agent === 'partner_liaison')).toEqual(EFFECTIVE);
    expect(rows.find((r) => r.agent === 'support_agent')).toEqual(NOT_YET);
    // Roster-only agents are synthesized as not_assigned candidates.
    expect(rows.find((r) => r.agent === 'ops_agent')).toEqual({
      agent: 'ops_agent',
      assigned: false,
      effective: false,
    });
    expect(rows.find((r) => r.agent === 'finance_agent')).toEqual({
      agent: 'finance_agent',
      assigned: false,
      effective: false,
    });
  });

  test('preserves roster order', () => {
    const rows = rosterAssignments(
      ['ops_agent', 'support_agent', 'partner_liaison'],
      STATUS,
    );
    expect(rows.map((r) => r.agent)).toEqual([
      'ops_agent',
      'support_agent',
      'partner_liaison',
    ]);
  });

  test('appends an assigned agent missing from the roster (never dropped)', () => {
    const rows = rosterAssignments(['ops_agent'], STATUS);
    expect(rows.map((r) => r.agent)).toEqual([
      'ops_agent',
      'partner_liaison',
      'support_agent',
    ]);
  });

  test('an empty roster falls back to the assigned agents from status', () => {
    expect(rosterAssignments([], STATUS)).toEqual(STATUS);
  });
});

describe('desiredAssigned / isChanged', () => {
  test('falls back to the committed state when the agent is not queued', () => {
    expect(desiredAssigned(EFFECTIVE, {})).toBe(true);
    expect(desiredAssigned(UNASSIGNED, {})).toBe(false);
    expect(isChanged(EFFECTIVE, {})).toBe(false);
    expect(isChanged(UNASSIGNED, {})).toBe(false);
  });

  test('reflects the queued desired state', () => {
    const queue: PendingAssignments = { ops_agent: true, partner_liaison: false };
    expect(desiredAssigned(UNASSIGNED, queue)).toBe(true);
    expect(desiredAssigned(EFFECTIVE, queue)).toBe(false);
    expect(isChanged(UNASSIGNED, queue)).toBe(true);
    expect(isChanged(EFFECTIVE, queue)).toBe(true);
  });
});

describe('toggleAssignment', () => {
  test('queues an assign for a not-assigned agent', () => {
    const q = toggleAssignment(UNASSIGNED, {});
    expect(q).toEqual({ ops_agent: true });
  });

  test('queues an unassign for an assigned agent', () => {
    const q = toggleAssignment(EFFECTIVE, {});
    expect(q).toEqual({ partner_liaison: false });
  });

  test('toggling back to the committed state DROPS the queue entry (no no-op commit)', () => {
    const once = toggleAssignment(UNASSIGNED, {});
    const twice = toggleAssignment(UNASSIGNED, once);
    expect(twice).toEqual({});
  });

  test('is pure — does not mutate the input queue', () => {
    const q: PendingAssignments = {};
    toggleAssignment(UNASSIGNED, q);
    expect(q).toEqual({});
  });
});

describe('toggleLabel — product language only', () => {
  test('an assigned agent offers Unassign; a not-assigned agent offers Assign', () => {
    expect(en(toggleLabel(EFFECTIVE, {}))).toBe('Unassign');
    expect(en(toggleLabel(UNASSIGNED, {}))).toBe('Assign');
  });

  test('follows the queued desired state', () => {
    const q = toggleAssignment(UNASSIGNED, {}); // now desired-assigned
    expect(en(toggleLabel(UNASSIGNED, q))).toBe('Unassign');
  });
});

describe('previewProvenance — optimistic state under the queue (reuses agentProvenance)', () => {
  test('unchanged agents keep their committed provenance', () => {
    expect(previewProvenance(EFFECTIVE, {}).status).toBe('effective');
    expect(previewProvenance(NOT_YET, {}).status).toBe('not_yet_effective');
    expect(previewProvenance(UNASSIGNED, {}).status).toBe('not_assigned');
  });

  test('a newly-assigned agent previews as "Takes effect next session"', () => {
    const q = toggleAssignment(UNASSIGNED, {});
    const p = previewProvenance(UNASSIGNED, q);
    expect(p.status).toBe('not_yet_effective');
    expect(en(p.statusLabel)).toBe('Takes effect next session');
    expect(p.takesEffectNextSession).toBe(true);
  });

  test('a newly-unassigned agent previews as "Not assigned"', () => {
    const q = toggleAssignment(EFFECTIVE, {});
    const p = previewProvenance(EFFECTIVE, q);
    expect(p.status).toBe('not_assigned');
    expect(en(p.statusLabel)).toBe('Not assigned');
  });
});

describe('reviewChanges / changeCount — config-review summary', () => {
  test('empty queue yields no changes', () => {
    expect(reviewChanges(ALL, {})).toEqual([]);
    expect(changeCount(ALL, {})).toBe(0);
  });

  test('lists an assign and an unassign with the request verb + product-language copy', () => {
    const queue: PendingAssignments = { ops_agent: true, partner_liaison: false };
    const changes = reviewChanges(ALL, queue);
    expect(changeCount(ALL, queue)).toBe(2);

    const assign = changes.find((c) => c.agent === 'ops_agent')!;
    expect(assign.action).toBe('allow'); // REQUEST-ONLY verb
    expect(en(assign.label)).toBe('Assign');
    expect(en(assign.summary, { agent: assign.agent })).toMatch(/shown this skill as guidance at its next session/i);

    const unassign = changes.find((c) => c.agent === 'partner_liaison')!;
    expect(unassign.action).toBe('remove'); // REQUEST-ONLY verb
    expect(en(unassign.label)).toBe('Unassign');
    expect(en(unassign.summary, { agent: unassign.agent })).toMatch(/no longer be shown this skill as guidance/i);
  });

  test('preserves the input agent order', () => {
    const queue: PendingAssignments = { partner_liaison: false, ops_agent: true };
    expect(reviewChanges(ALL, queue).map((c) => c.agent)).toEqual([
      'partner_liaison',
      'ops_agent',
    ]);
  });
});

describe('copy discipline — no forbidden token families in rendered strings', () => {
  // The RENDERED copy this module emits: toggle labels, change summaries, and
  // the commit note. The api verb 'allow'/'remove' is REQUEST-ONLY and must
  // never appear as a rendered label/summary. Reject the full forbidden family
  // AND user-facing "active".
  const forbidden = /materializ|admit|permission|approve|grant|\ballow\b|\bremove\b|\bpending\b|\bactive\b/i;

  test('toggle labels, change summaries, and the commit note are clean', () => {
    const queue: PendingAssignments = { ops_agent: true, partner_liaison: false };
    const rendered: string[] = [
      en(CONFIG_REVIEW_NOTE),
      en(toggleLabel(EFFECTIVE, {})),
      en(toggleLabel(UNASSIGNED, {})),
      // Agent names are daemon values; render with a neutral one so only the
      // catalog copy is scanned.
      ...reviewChanges(ALL, queue).flatMap((c) => [en(c.label), en(c.summary, { agent: 'x' })]),
      ...ALL.flatMap((a) => [
        en(previewProvenance(a, queue).statusLabel),
        en(previewProvenance(a, queue).reason),
      ]),
    ];
    for (const t of rendered) {
      expect(t).not.toMatch(forbidden);
    }
  });

  test('the commit note states guidance-visibility, not a tool/command change', () => {
    expect(en(CONFIG_REVIEW_NOTE)).toMatch(/guidance/i);
    expect(en(CONFIG_REVIEW_NOTE)).toMatch(/do not change available tools or commands/i);
  });
});
