import { describe, expect, test } from 'vitest';
import type { WorkHoursLayer, WorkingHoursSettings } from '@/lib/api/types';
import {
  buildLayerPatch,
  cadenceSummary,
  effectiveSchedule,
  eligibleSet,
  isEligible,
  onStatus,
  parseRoutineTasks,
  reconcile,
  renderLeaf,
} from './merge';

function layer(partial: Partial<WorkHoursLayer>): WorkHoursLayer {
  const { window: w, ...rest } = partial;
  return {
    mode: null,
    interval: null,
    days: null,
    catch_up_on_startup: null,
    ...rest,
    window: { start: null, end: null, timezone: null, ...(w ?? {}) },
  };
}

function emptyLayer(): WorkHoursLayer {
  return layer({});
}

function wh(partial: Partial<WorkingHoursSettings>): WorkingHoursSettings {
  return {
    enabled: true,
    agents: { mode: 'all', include: [], exclude: [] },
    default: emptyLayer(),
    teams: {},
    overrides: {},
    ...partial,
  };
}

describe('reconcile — per-leaf provenance + last-wins effective merge', () => {
  test('mosaic: interval from agent, days from team, window from org', () => {
    const config = wh({
      default: layer({
        mode: 'windowed',
        window: { start: '09:00', end: '17:00', timezone: 'UTC' },
        interval: '2h',
      }),
      teams: {
        eng: layer({
          window: { start: '08:00', end: null, timezone: 'America/Los_Angeles' },
          days: ['mon', 'tue', 'wed', 'thu', 'fri'],
        }),
      },
      overrides: {
        dev_agent: layer({
          interval: '30m',
          window: { start: null, end: '19:00', timezone: null },
        }),
      },
    });

    const rec = reconcile(config, 'dev_agent', 'eng');
    const byLeaf = Object.fromEntries(rec.rows.map((r) => [r.leaf, r.cell]));

    // mode: only org → org
    expect(byLeaf['mode'].effective).toBe('windowed');
    expect(byLeaf['mode'].source).toBe('org');

    // window.start: org 09:00, team 08:00 wins (agent unset)
    expect(byLeaf['window.start'].effective).toBe('08:00');
    expect(byLeaf['window.start'].source).toBe('team');

    // window.end: org 17:00, agent 19:00 wins
    expect(byLeaf['window.end'].effective).toBe('19:00');
    expect(byLeaf['window.end'].source).toBe('agent');

    // window.timezone: org UTC, team America/LA wins
    expect(byLeaf['window.timezone'].effective).toBe('America/Los_Angeles');
    expect(byLeaf['window.timezone'].source).toBe('team');

    // interval: org 2h, agent 30m wins
    expect(byLeaf['interval'].effective).toBe('30m');
    expect(byLeaf['interval'].source).toBe('agent');

    // days: only team → team
    expect(byLeaf['days'].effective).toEqual(['mon', 'tue', 'wed', 'thu', 'fri']);
    expect(byLeaf['days'].source).toBe('team');
  });

  test('unset everywhere → source unset, effective null', () => {
    const rec = reconcile(wh({}), 'nobody', null);
    const mode = rec.rows.find((r) => r.leaf === 'mode')!.cell;
    expect(mode.source).toBe('unset');
    expect(mode.effective).toBeNull();
    expect(renderLeaf(mode.effective)).toBe('—');
  });

  test('agent with no team ignores team layers entirely', () => {
    const config = wh({
      default: layer({ mode: 'continuous', interval: '1h' }),
      teams: { eng: layer({ interval: '30m' }) },
    });
    const rec = reconcile(config, 'solo', null);
    const interval = rec.rows.find((r) => r.leaf === 'interval')!.cell;
    expect(interval.effective).toBe('1h');
    expect(interval.source).toBe('org');
  });

  test('empty days array at a tier counts as unset (falls through)', () => {
    const config = wh({
      default: layer({ days: ['mon'] }),
      overrides: { a: layer({ days: [] }) },
    });
    const rec = reconcile(config, 'a', null);
    const days = rec.rows.find((r) => r.leaf === 'days')!.cell;
    expect(days.effective).toEqual(['mon']);
    expect(days.source).toBe('org');
  });
});

describe('cadenceSummary', () => {
  test('windowed', () => {
    const config = wh({
      default: layer({
        mode: 'windowed',
        window: { start: '08:00', end: '19:00', timezone: 'America/Los_Angeles' },
        interval: '30m',
        days: ['mon', 'tue', 'wed', 'thu', 'fri'],
      }),
    });
    const eff = effectiveSchedule(reconcile(config, 'x', null));
    expect(cadenceSummary(eff)).toBe(
      'every 30m · 08:00–19:00 mon,tue,wed,thu,fri America/Los_Angeles',
    );
  });

  test('continuous', () => {
    const config = wh({
      default: layer({ mode: 'continuous', interval: '2h' }),
    });
    const eff = effectiveSchedule(reconcile(config, 'x', null));
    expect(cadenceSummary(eff)).toBe('every 2h (24/7)');
  });

  test('no mode anywhere → inherits-nothing copy', () => {
    const eff = effectiveSchedule(reconcile(wh({}), 'x', null));
    expect(cadenceSummary(eff)).toBe('(inherits org default)');
  });
});

describe('eligibility + on-status', () => {
  test('mode all → eligible unless excluded', () => {
    const config = wh({ agents: { mode: 'all', include: [], exclude: ['bot'] } });
    expect(isEligible(config, 'dev_agent')).toBe(true);
    expect(isEligible(config, 'bot')).toBe(false);
  });

  test('mode whitelist → eligible only if included and not excluded', () => {
    const config = wh({
      agents: { mode: 'whitelist', include: ['dev_agent', 'qa'], exclude: ['qa'] },
    });
    expect(isEligible(config, 'dev_agent')).toBe(true);
    expect(isEligible(config, 'qa')).toBe(false); // excluded beats include
    expect(isEligible(config, 'other')).toBe(false); // not whitelisted
  });

  test('onStatus = feature.enabled AND eligible', () => {
    const on = wh({ enabled: true, agents: { mode: 'all', include: [], exclude: [] } });
    expect(onStatus(on, 'dev_agent')).toBe(true);

    const featureOff = wh({ enabled: false });
    expect(onStatus(featureOff, 'dev_agent')).toBe(false);

    const excluded = wh({
      enabled: true,
      agents: { mode: 'all', include: [], exclude: ['dev_agent'] },
    });
    expect(onStatus(excluded, 'dev_agent')).toBe(false);
  });

  test('eligibleSet preview', () => {
    const names = ['a', 'b', 'c'];
    expect(eligibleSet(names, { mode: 'all', include: [], exclude: ['b'] })).toEqual([
      'a',
      'c',
    ]);
    expect(
      eligibleSet(names, { mode: 'whitelist', include: ['a', 'c'], exclude: ['c'] }),
    ).toEqual(['a']);
  });
});

describe('parseRoutineTasks', () => {
  test('extracts bullets under the ## Routine Tasks heading', () => {
    const prompt = [
      '# Agent',
      'Intro text.',
      '## Routine Tasks',
      '- Review open PRs and leave review comments',
      '* Triage new issues labeled bug',
      '1. Sweep the backlog',
      '',
      '## Next Section',
      '- not a routine task',
    ].join('\n');
    expect(parseRoutineTasks(prompt)).toEqual([
      'Review open PRs and leave review comments',
      'Triage new issues labeled bug',
      'Sweep the backlog',
    ]);
  });

  test('no heading → empty', () => {
    expect(parseRoutineTasks('# Agent\nNo routine section here.')).toEqual([]);
    expect(parseRoutineTasks(undefined)).toEqual([]);
  });

  test('heading present but no bullets → empty (no-op wake warning trigger)', () => {
    expect(parseRoutineTasks('## Routine Tasks\n\n## Other')).toEqual([]);
  });
});

describe('buildLayerPatch — partial + explicit-null clear', () => {
  test('only present keys emitted; null clears (reset-to-inherited)', () => {
    const patch = buildLayerPatch({ interval: '30m', mode: null });
    expect(patch).toEqual({ interval: '30m', mode: null });
    expect('days' in patch).toBe(false);
    expect('window' in patch).toBe(false);
  });

  test('window sub-object only emitted when a window leaf is present', () => {
    expect(buildLayerPatch({ interval: '1h' }).window).toBeUndefined();
    const patch = buildLayerPatch({ start: '09:00', end: null });
    expect(patch.window).toEqual({ start: '09:00', end: null });
  });

  test('clearing days sends null', () => {
    expect(buildLayerPatch({ days: null })).toEqual({ days: null });
  });

  test('absent key is left untouched (server deep-merge)', () => {
    const patch = buildLayerPatch({ timezone: 'UTC' });
    expect(patch).toEqual({ window: { timezone: 'UTC' } });
  });
});
