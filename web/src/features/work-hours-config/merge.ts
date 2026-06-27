/**
 * Pure client-side derivation for the Work-Hours Config UI (THR-035).
 *
 * The SERVER is the single validation authority. NOTHING in this module
 * validates a schedule (no divides-24h, no start<end, no interval≤window, no
 * window-completeness). It only:
 *   - derives per-leaf PROVENANCE + the EFFECTIVE value by last-wins merge
 *     across the three raw tiers (mirrors WorkingHoursConfig.resolve_for's
 *     leaf-by-leaf overlay — DISPLAY logic, not validation),
 *   - summarises a resolved cadence for the roster row,
 *   - derives the read-only `On` status (feature.enabled AND eligible),
 *   - parses the read-only `## Routine Tasks` markdown bullets,
 *   - builds partial working_hours patches (with explicit null = reset).
 */
import type {
  WorkHoursLayer,
  WorkHoursLayerPatch,
  WorkingHoursSettings,
} from '@/lib/api/types';

// The exact resolvable leaves, in display order (mirrors the backend leaf set;
// `enabled` is NOT a leaf — it is a single feature-level switch).
export const LEAVES = [
  'mode',
  'window.start',
  'window.end',
  'window.timezone',
  'interval',
  'days',
  'catch_up_on_startup',
] as const;

export type LeafKey = (typeof LEAVES)[number];

/** Where a leaf's effective value came from. */
export type Provenance = 'org' | 'team' | 'agent' | 'unset';

export interface LeafCell {
  /** The raw value declared at each tier (undefined render-string handled by caller). */
  org: unknown;
  team: unknown;
  agent: unknown;
  /** The winning value after last-wins merge (null/undefined if unset everywhere). */
  effective: unknown;
  /** Which tier won. `unset` = no tier declared this leaf. */
  source: Provenance;
}

export const LEAF_LABELS: Record<LeafKey, string> = {
  mode: 'mode',
  'window.start': 'window.start',
  'window.end': 'window.end',
  'window.timezone': 'window.timezone',
  interval: 'interval',
  days: 'days',
  catch_up_on_startup: 'catch_up_on_startup',
};

function leafValue(layer: WorkHoursLayer | undefined, leaf: LeafKey): unknown {
  if (!layer) return null;
  switch (leaf) {
    case 'mode':
      return layer.mode;
    case 'window.start':
      return layer.window?.start ?? null;
    case 'window.end':
      return layer.window?.end ?? null;
    case 'window.timezone':
      return layer.window?.timezone ?? null;
    case 'interval':
      return layer.interval;
    case 'days':
      return layer.days;
    case 'catch_up_on_startup':
      return layer.catch_up_on_startup;
    default:
      return null;
  }
}

/** A leaf is "set" at a tier when its value is neither null nor undefined.
 *  (Empty arrays are NOT a set value for `days` — the backend treats null as
 *  unset; an empty list is never produced by the views, so we coalesce both to
 *  unset for display.) */
function isSet(v: unknown): boolean {
  if (v === null || v === undefined) return false;
  if (Array.isArray(v) && v.length === 0) return false;
  return true;
}

export interface Reconciliation {
  teamName: string | null;
  rows: { leaf: LeafKey; label: string; cell: LeafCell }[];
}

/**
 * Build the per-leaf reconciliation (the §4.3 centerpiece). Last-wins across
 * [org default, team layer (if any), agent override (if any)].
 */
export function reconcile(
  wh: WorkingHoursSettings,
  agentName: string,
  teamName: string | null,
): Reconciliation {
  const orgLayer = wh.default;
  const teamLayer = teamName ? wh.teams[teamName] : undefined;
  const agentLayer = wh.overrides[agentName];

  const rows = LEAVES.map((leaf) => {
    const org = leafValue(orgLayer, leaf);
    const team = leafValue(teamLayer, leaf);
    const agent = leafValue(agentLayer, leaf);

    // Last-wins: agent beats team beats org.
    let effective: unknown = null;
    let source: Provenance = 'unset';
    if (isSet(org)) {
      effective = org;
      source = 'org';
    }
    if (isSet(team)) {
      effective = team;
      source = 'team';
    }
    if (isSet(agent)) {
      effective = agent;
      source = 'agent';
    }

    return {
      leaf,
      label: LEAF_LABELS[leaf],
      cell: { org, team, agent, effective, source },
    };
  });

  return { teamName, rows };
}

/** Render a leaf cell value for display (— for unset). */
export function renderLeaf(v: unknown): string {
  if (!isSet(v)) return '—';
  if (Array.isArray(v)) return v.join(', ');
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return String(v);
}

/** A compact effective-schedule view keyed by leaf (for cadence summary). */
export interface EffectiveSchedule {
  mode: string | null;
  start: string | null;
  end: string | null;
  timezone: string | null;
  interval: string | null;
  days: string[] | null;
  catchUp: boolean | null;
}

export function effectiveSchedule(rec: Reconciliation): EffectiveSchedule {
  const get = (leaf: LeafKey): unknown =>
    rec.rows.find((r) => r.leaf === leaf)?.cell.effective ?? null;
  return {
    mode: (get('mode') as string | null) ?? null,
    start: (get('window.start') as string | null) ?? null,
    end: (get('window.end') as string | null) ?? null,
    timezone: (get('window.timezone') as string | null) ?? null,
    interval: (get('interval') as string | null) ?? null,
    days: (get('days') as string[] | null) ?? null,
    catchUp: (get('catch_up_on_startup') as boolean | null) ?? null,
  };
}

/**
 * Human-readable cadence summary for the roster row. DISPLAY only — does not
 * assert completeness. If `mode` is unset at every tier the agent simply
 * inherits nothing, so we say so rather than fabricate a schedule.
 */
export function cadenceSummary(eff: EffectiveSchedule): string {
  if (!eff.mode) return '(inherits org default)';
  const every = eff.interval ? `every ${eff.interval}` : 'every —';
  if (eff.mode === 'continuous') {
    return `${every} (24/7)`;
  }
  // windowed
  const window =
    eff.start && eff.end ? `${eff.start}–${eff.end}` : 'window —';
  const days = eff.days && eff.days.length > 0 ? eff.days.join(',') : 'days —';
  const tz = eff.timezone ?? '';
  return `${every} · ${window} ${days}${tz ? ` ${tz}` : ''}`.trim();
}

/**
 * Eligibility derivation — the single org-level gate (`agents` selector).
 * Mirrors the backend whitelist/exclude semantics for DISPLAY of the chip.
 */
export function isEligible(
  wh: WorkingHoursSettings,
  agentName: string,
): boolean {
  const { mode, include, exclude } = wh.agents;
  if (exclude.includes(agentName)) return false;
  if (mode === 'whitelist') return include.includes(agentName);
  return true; // mode 'all'
}

/** The resulting eligible set for a hypothetical selector (live preview). */
export function eligibleSet(
  agentNames: string[],
  selector: { mode: string; include: string[]; exclude: string[] },
): string[] {
  return agentNames.filter((name) => {
    if (selector.exclude.includes(name)) return false;
    if (selector.mode === 'whitelist') return selector.include.includes(name);
    return true;
  });
}

/**
 * Read-only `On` status for the roster: ON iff the feature switch is on AND the
 * agent is eligible. NOT a per-agent toggle — it merely reflects feature state
 * gated by eligibility (spec S1).
 */
export function onStatus(
  wh: WorkingHoursSettings,
  agentName: string,
): boolean {
  return wh.enabled && isEligible(wh, agentName);
}

/**
 * Parse the `## Routine Tasks` markdown section from an agent's system_prompt.
 * Display-only — returns the bullet text lines (in order). Stops at the next
 * `##`/`#` heading. Recognises `-`, `*`, `+` and numbered bullets.
 */
export function parseRoutineTasks(systemPrompt: string | undefined): string[] {
  if (!systemPrompt) return [];
  const lines = systemPrompt.split('\n');
  const headingIdx = lines.findIndex((l) =>
    /^#{1,6}\s+routine tasks\s*$/i.test(l.trim()),
  );
  if (headingIdx === -1) return [];
  const bullets: string[] = [];
  for (let i = headingIdx + 1; i < lines.length; i++) {
    const raw = lines[i];
    const trimmed = raw.trim();
    if (/^#{1,6}\s+/.test(trimmed)) break; // next heading
    const m = trimmed.match(/^(?:[-*+]|\d+[.)])\s+(.*)$/);
    if (m && m[1].trim()) bullets.push(m[1].trim());
  }
  return bullets;
}

// ---------------------------------------------------------------------------
// Patch builders
// ---------------------------------------------------------------------------

/**
 * Build a per-tier layer patch from a draft. Only keys present in `draft` are
 * emitted; an explicit `null` clears (reset-to-inherited). Keys absent from the
 * draft are left untouched (server deep-merge). The window sub-object is only
 * emitted when at least one of its leaves is present in the draft.
 */
export interface LayerDraft {
  mode?: string | null;
  start?: string | null;
  end?: string | null;
  timezone?: string | null;
  interval?: string | null;
  days?: string[] | null;
  catch_up_on_startup?: boolean | null;
}

export function buildLayerPatch(draft: LayerDraft): WorkHoursLayerPatch {
  const patch: WorkHoursLayerPatch = {};
  if ('mode' in draft) patch.mode = draft.mode ?? null;
  if ('interval' in draft) patch.interval = draft.interval ?? null;
  if ('days' in draft) patch.days = draft.days ?? null;
  if ('catch_up_on_startup' in draft) {
    patch.catch_up_on_startup = draft.catch_up_on_startup ?? null;
  }
  const window: NonNullable<WorkHoursLayerPatch['window']> = {};
  let hasWindow = false;
  if ('start' in draft) {
    window.start = draft.start ?? null;
    hasWindow = true;
  }
  if ('end' in draft) {
    window.end = draft.end ?? null;
    hasWindow = true;
  }
  if ('timezone' in draft) {
    window.timezone = draft.timezone ?? null;
    hasWindow = true;
  }
  if (hasWindow) patch.window = window;
  return patch;
}
