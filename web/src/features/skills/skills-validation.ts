/**
 * Pure, provider-agnostic helpers for the Runtime Validation surface (THR-092
 * Slice 6). The severity → product-badge mapper, the reason_code → plain-
 * language mapper, the event → row view-model, and the filters → query-param
 * builder all live here, apart from the JSX, so the copy discipline is
 * unit-tested.
 *
 * COPY DISCIPLINE (hard): every rendered string this module produces uses
 * guidance-visibility product language. It NEVER emits the forbidden token
 * family (materializ… / admit / permission / approve / grant / pending) or a
 * user-facing "active". Raw daemon enum strings (severity codes, reason codes,
 * sources) are mapped to human copy here and never rendered verbatim.
 */
import type { ValidationEvent } from '@/hooks/skills';

// ── severity → product badge ────────────────────────────────────────────

export type ValidationTone = 'positive' | 'neutral' | 'attention';

export interface SeverityBadge {
  text: string;
  tone: ValidationTone;
}

/** Turn a machine severity into a title-case word without leaking the raw
 *  enum. Used as the fallback for a severity the daemon adds later. */
function humanize(code: string): string {
  const words = code.trim().replace(/[_-]+/g, ' ').trim();
  if (!words) return 'Event';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * severity → product-language badge. The daemon records `pass` on a clean
 * technical validation and `error` on a failure (routes/skills.py); other
 * severities (`warn`, `info`) are reserved for materialization / contract-
 * predicate events. Failure maps to "Needs attention" — the SAME product
 * label the catalog + detail surfaces use — never permission/approval wording.
 */
export function severityBadge(severity: string): SeverityBadge {
  switch (severity) {
    case 'pass':
    case 'ok':
      return { text: 'Passed', tone: 'positive' };
    case 'error':
    case 'fail':
    case 'failed':
      return { text: 'Needs attention', tone: 'attention' };
    case 'warn':
    case 'warning':
      return { text: 'Warning', tone: 'attention' };
    case 'info':
      return { text: 'Info', tone: 'neutral' };
    default:
      return { text: humanize(severity), tone: 'neutral' };
  }
}

// ── reason_code → plain language ────────────────────────────────────────

// The frozen set of technical-validation reason codes the daemon emits
// (routes/skills.py), plus the materialization / contract-predicate codes the
// Runtime Validation surface is specced to explain in product language (spec
// v3 §8). Anything unmapped is humanized — a raw enum is never rendered.
const REASON_COPY: Record<string, string> = {
  skill_md_empty: 'The skill guide (SKILL.md) is empty.',
  missing_id: 'The skill is missing an id.',
  missing_slug: 'The skill is missing a slug.',
  missing_name: 'The skill is missing a name.',
  missing_version: 'The skill guide is missing a version.',
  skill_md_no_heading: 'The skill guide needs a top-level heading.',
  invalid_references_type: 'The references section is not formatted correctly.',
  invalid_reference_value: 'A reference entry has an invalid value.',
  invalid_reference_filename: 'A reference points to an invalid file name.',
  invalid_assets_type: 'The assets section is not formatted correctly.',
  invalid_asset_value: 'An asset entry has an invalid value.',
  invalid_asset_filename: 'An asset points to an invalid file name.',
  slug_collision: 'This slug is already used by another skill.',
  system_contract_forbidden:
    'A custom skill cannot be saved as a system contract.',
  materialization_error:
    'The skill could not be prepared for the next session.',
  contract_predicate_error:
    'A system-contract rule could not be checked for this agent.',
  next_session_materialization: 'Takes effect next session.',
};

/** reason_code → one plain-language line. Unknown codes are humanized so the
 *  raw enum jargon is never shown to an operator. */
export function reasonCodeLabel(code: string): string {
  return REASON_COPY[code] ?? humanize(code) + '.';
}

// ── agent / source labels ───────────────────────────────────────────────

/** A null agent means the event came from a context-applied rule (e.g. a
 *  system contract shown to every agent), not a per-agent assignment — render
 *  a product label, NEVER a blank cell. */
export function agentLabel(agent: string | null): string {
  return agent ?? 'Applied by context — all agents';
}

const SOURCE_COPY: Record<string, string> = {
  user_authored: 'Custom',
  first_party: 'Bundled',
  system_contract: 'System contract',
  runtime: 'Runtime',
};

/** source → product-language label mirroring the catalog's Bundled/Custom
 *  vocabulary. */
export function sourceLabel(source: string): string {
  return SOURCE_COPY[source] ?? humanize(source);
}

// ── time formatting ─────────────────────────────────────────────────────

export interface EventTime {
  /** Compact relative age, e.g. "just now" / "5m" / "3h" / "2d". */
  relative: string;
  /** Full, unambiguous timestamp for the title/tooltip. */
  absolute: string;
}

/** Format an event's `created_at`. `nowMs` is passed in (not read from the
 *  clock) so the relative age is deterministic under test. */
export function formatEventTime(iso: string, nowMs: number): EventTime {
  const then = Date.parse(iso);
  const absolute = Number.isNaN(then)
    ? iso
    : new Date(then).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
  if (Number.isNaN(then)) return { relative: iso, absolute };
  const min = Math.floor((nowMs - then) / 60000);
  let relative: string;
  if (min < 1) relative = 'just now';
  else if (min < 60) relative = `${min}m`;
  else {
    const hr = Math.round(min / 60);
    if (hr < 24) relative = `${hr}h`;
    else relative = `${Math.round(hr / 24)}d`;
  }
  return { relative, absolute };
}

// ── event → row view-model ──────────────────────────────────────────────

export interface ValidationRow {
  id: number;
  skillId: string;
  skillName: string;
  agentLabel: string;
  source: string;
  sourceLabel: string;
  severity: SeverityBadge;
  ok: boolean;
  okLabel: string;
  version: string;
  findings: string[];
  reasonLines: string[];
  time: EventTime;
}

/** Project one daemon `ValidationEvent` into a fully product-language row
 *  view-model. All copy mapping happens here so the component is pure JSX. */
export function toValidationRow(
  event: ValidationEvent,
  nowMs: number,
): ValidationRow {
  return {
    id: event.id,
    skillId: event.skill_id,
    skillName: event.slug,
    agentLabel: agentLabel(event.agent),
    source: event.source,
    sourceLabel: sourceLabel(event.source),
    severity: severityBadge(event.severity),
    ok: event.ok,
    okLabel: event.ok ? 'Passed' : 'Not passed',
    version: event.version,
    findings: event.findings ?? [],
    reasonLines: (event.reason_codes ?? []).map(reasonCodeLabel),
    time: formatEventTime(event.created_at, nowMs),
  };
}

// ── filters → query params ──────────────────────────────────────────────

export type SourceFilter = 'all' | 'user_authored' | 'first_party' | 'runtime';
export type SeverityFilter = 'all' | 'pass' | 'error' | 'warn' | 'info';
export type TimeFilter = 'all' | '24h' | '7d' | '30d';

export interface ValidationFilters {
  /** '' = all skills, else a skill_id (maps to the `skill` param). */
  skill: string;
  /** '' = all agents, else an agent name (maps to the `agent` param). */
  agent: string;
  source: SourceFilter;
  time: TimeFilter;
  severity: SeverityFilter;
}

export const EMPTY_FILTERS: ValidationFilters = {
  skill: '',
  agent: '',
  source: 'all',
  time: 'all',
  severity: 'all',
};

export interface ValidationQuery {
  skill?: string;
  agent?: string;
  source?: string;
  since?: string;
  severity?: string;
}

const TIME_WINDOW_MS: Record<Exclude<TimeFilter, 'all'>, number> = {
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
};

/**
 * Map the filter state to the endpoint query params. Returns `undefined` when
 * no filter is set, so the list query shares a key with the unfiltered options
 * query (React Query dedupes them into a single fetch). `nowMs` is passed in so
 * the computed `since` is deterministic under test.
 */
export function buildValidationQuery(
  f: ValidationFilters,
  nowMs: number,
): ValidationQuery | undefined {
  const q: ValidationQuery = {};
  if (f.skill) q.skill = f.skill;
  if (f.agent) q.agent = f.agent;
  if (f.source !== 'all') q.source = f.source;
  if (f.severity !== 'all') q.severity = f.severity;
  if (f.time !== 'all') {
    q.since = new Date(nowMs - TIME_WINDOW_MS[f.time]).toISOString();
  }
  return Object.keys(q).length === 0 ? undefined : q;
}

// ── filter option sets ──────────────────────────────────────────────────

export interface FilterOption {
  value: string;
  label: string;
}

/** Distinct skills present in the (unfiltered) event set → labeled options for
 *  the skill filter. Value is the skill_id (the `skill` param); label is the
 *  human slug. */
export function skillOptions(events: ValidationEvent[]): FilterOption[] {
  const seen = new Map<string, string>();
  for (const e of events) {
    if (!seen.has(e.skill_id)) seen.set(e.skill_id, e.slug);
  }
  return [...seen.entries()]
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

/** Distinct named agents present in the event set → options for the agent
 *  filter. Context-applied events (null agent) contribute no option — they are
 *  not a per-agent selection. */
export function agentOptions(events: ValidationEvent[]): FilterOption[] {
  const seen = new Set<string>();
  for (const e of events) {
    if (e.agent) seen.add(e.agent);
  }
  return [...seen]
    .sort((a, b) => a.localeCompare(b))
    .map((value) => ({ value, label: value }));
}

export const SOURCE_OPTIONS: { value: SourceFilter; label: string }[] = [
  { value: 'all', label: 'All sources' },
  { value: 'user_authored', label: 'Custom' },
  { value: 'first_party', label: 'Bundled' },
  { value: 'runtime', label: 'Runtime' },
];

export const SEVERITY_OPTIONS: { value: SeverityFilter; label: string }[] = [
  { value: 'all', label: 'All results' },
  { value: 'pass', label: 'Passed' },
  { value: 'error', label: 'Needs attention' },
  { value: 'warn', label: 'Warning' },
  { value: 'info', label: 'Info' },
];

export const TIME_OPTIONS: { value: TimeFilter; label: string }[] = [
  { value: 'all', label: 'Any time' },
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
];
