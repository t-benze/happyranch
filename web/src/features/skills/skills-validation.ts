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
 *
 * THR-118 W4c: every product-language string resolves through the typed
 * catalog (`skills.validation.*`). Mappers take the active `t` (and `locale`
 * for dates) so a locale switch re-renders them in place; a humanized
 * fallback for an unknown daemon token is derived from the daemon value itself
 * and therefore stays untranslated.
 */
import type { ValidationEvent } from '@/hooks/skills';
import { formatDateShapeFor, type Locale, type MessageKey } from '@/lib/i18n';
import type { Translate } from './strings';

// ── severity → product badge ────────────────────────────────────────────

export type ValidationTone = 'positive' | 'neutral' | 'attention';

export interface SeverityBadge {
  text: string;
  tone: ValidationTone;
}

/** Turn a machine severity into a title-case word without leaking the raw
 *  enum. Used as the fallback for a severity the daemon adds later. */
function humanize(code: string, t: Translate): string {
  const words = code.trim().replace(/[_-]+/g, ' ').trim();
  if (!words) return t('skills.validation.event');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * severity → product-language badge. The daemon writes EXACTLY three event
 * severities: `pass` / `error` on a technical validation (routes/skills.py) and
 * `info` on a materialization event (workspace_adapters.py — a skill applied at
 * session spawn). All three are mapped here; `info` → a neutral "Info" badge so
 * the materialization path never renders a raw enum. Failure maps to "Needs
 * attention" — the SAME product label the catalog + detail surfaces use — never
 * permission/approval wording. `warn` + a humanized fallback stay as defensive
 * coverage for any severity a future daemon build might add.
 */
export function severityBadge(severity: string, t: Translate): SeverityBadge {
  switch (severity) {
    case 'pass':
    case 'ok':
      return { text: t('skills.validation.severity.passed'), tone: 'positive' };
    case 'error':
    case 'fail':
    case 'failed':
      return { text: t('skills.status.needsAttention'), tone: 'attention' };
    case 'warn':
    case 'warning':
      return { text: t('skills.validation.severity.warning'), tone: 'attention' };
    case 'info':
      return { text: t('skills.validation.severity.info'), tone: 'neutral' };
    default:
      return { text: humanize(severity, t), tone: 'neutral' };
  }
}

// ── reason_code → plain language ────────────────────────────────────────

// The frozen set of technical-validation reason codes the daemon emits
// (routes/skills.py), plus the materialization / contract-predicate codes the
// Runtime Validation surface is specced to explain in product language (spec
// v3 §8). Anything unmapped is humanized — a raw enum is never rendered.
// Copy for every code lives in the catalog under
// `skills.validation.reason.<code>` (THR-262 seq43 Option A: `invalid_slug` is
// the request-identity admission code, a HappyRanch rule).
const REASON_CODES: ReadonlySet<string> = new Set([
  'skill_md_empty',
  'missing_id',
  'missing_slug',
  'missing_name',
  'missing_version',
  'skill_md_no_heading',
  'skill_md_no_frontmatter',
  'skill_md_unclosed_frontmatter',
  'skill_md_malformed_frontmatter',
  'skill_md_frontmatter_not_mapping',
  'frontmatter_duplicate_key',
  'admission_field_not_allowed',
  'frontmatter_missing_name',
  'frontmatter_invalid_name',
  'frontmatter_name_slug_mismatch',
  'frontmatter_missing_description',
  'frontmatter_invalid_description',
  'frontmatter_invalid_license',
  'frontmatter_invalid_compatibility',
  'frontmatter_invalid_metadata',
  'invalid_references_type',
  'invalid_reference_value',
  'invalid_reference_filename',
  'invalid_assets_type',
  'invalid_asset_value',
  'invalid_asset_filename',
  'slug_collision',
  'invalid_slug',
  'system_contract_forbidden',
  'materialization_error',
  'contract_predicate_error',
  'next_session_materialization',
]);

/** reason_code → one plain-language line. Unknown codes are humanized so the
 *  raw enum jargon is never shown to an operator. */
export function reasonCodeLabel(code: string, t: Translate): string {
  return REASON_CODES.has(code)
    ? t(`skills.validation.reason.${code}` as MessageKey)
    : humanize(code, t) + '.';
}

// ── agent / source labels ───────────────────────────────────────────────

/** A null agent means the event came from a context-applied rule (e.g. a
 *  system contract shown to every agent), not a per-agent assignment — render
 *  a product label, NEVER a blank cell. */
export function agentLabel(agent: string | null, t: Translate): string {
  return agent ?? t('skills.validation.appliedByContext');
}

const SOURCE_COPY: Record<string, MessageKey> = {
  user_authored: 'skills.validation.source.custom',
  first_party: 'skills.validation.source.bundled',
  // A skill applied / loaded into a workspace at session spawn
  // (workspace_adapters.py insert_skill_validation_event source="materialization").
  // Product-safe label: contains no forbidden lifecycle/permission token
  // (materializ… / admit / permission / approve / grant / pending) and no
  // user-facing "active", so it passes the routed copy gate.
  materialization: 'skills.validation.source.appliedAtSpawn',
  // Dead keys — these are skill.source TYPES, never event `source` values, so
  // the daemon never emits them here. Kept only as defensive fallbacks so a
  // stray value renders a word, never a raw enum.
  system_contract: 'skills.validation.source.systemContract',
  runtime: 'skills.validation.source.runtime',
};

/** source → product-language label mirroring the catalog's Bundled/Custom
 *  vocabulary. */
export function sourceLabel(source: string, t: Translate): string {
  return Object.prototype.hasOwnProperty.call(SOURCE_COPY, source)
    ? t(SOURCE_COPY[source])
    : humanize(source, t);
}

// ── time formatting ─────────────────────────────────────────────────────

export interface EventTime {
  /** Compact relative age, e.g. "just now" / "5m" / "3h" / "2d". */
  relative: string;
  /** Full, unambiguous timestamp for the title/tooltip. */
  absolute: string;
}

/** Format an event's `created_at`. `nowMs` is passed in (not read from the
 *  clock) so the relative age is deterministic under test. The absolute
 *  tooltip uses the central `dateTime` display shape in the viewer's local
 *  time; the relative age is a catalog template. An unparseable timestamp is
 *  a daemon value and renders verbatim. */
export function formatEventTime(
  iso: string,
  nowMs: number,
  locale: Locale,
  t: Translate,
): EventTime {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return { relative: iso, absolute: iso };
  const absolute = formatDateShapeFor(locale, then, 'dateTime');
  const min = Math.floor((nowMs - then) / 60000);
  let relative: string;
  if (min < 1) relative = t('skills.validation.justNow');
  else if (min < 60) relative = t('skills.validation.ageMinutes', { count: min });
  else {
    const hr = Math.round(min / 60);
    if (hr < 24) relative = t('skills.validation.ageHours', { count: hr });
    else relative = t('skills.validation.ageDays', { count: Math.round(hr / 24) });
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
  /** Raw daemon severity token (machine value, used as a data attribute). */
  severityCode: string;
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
  locale: Locale,
  t: Translate,
): ValidationRow {
  return {
    id: event.id,
    skillId: event.skill_id,
    skillName: event.slug,
    agentLabel: agentLabel(event.agent, t),
    source: event.source,
    sourceLabel: sourceLabel(event.source, t),
    severityCode: event.severity,
    severity: severityBadge(event.severity, t),
    ok: event.ok,
    okLabel: t(event.ok ? 'skills.validation.severity.passed' : 'skills.validation.notPassed'),
    version: event.version,
    findings: event.findings ?? [],
    reasonLines: (event.reason_codes ?? []).map((code) => reasonCodeLabel(code, t)),
    time: formatEventTime(event.created_at, nowMs, locale, t),
  };
}

// ── filters → query params ──────────────────────────────────────────────

export type SourceFilter =
  | 'all'
  | 'user_authored'
  | 'first_party'
  | 'materialization';
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

// Derived from the REAL event-source domain the daemon writes
// ({user_authored, first_party, materialization}) — NOT mock-only values. A
// source the daemon never emits would be an unfilterable dead option, and a
// real source with no option (materialization) would leave those rows
// unfilterable. Labels are product-safe; "Applied at session spawn" mirrors the
// row badge and passes the routed copy gate.
export const SOURCE_OPTIONS: { value: SourceFilter; labelKey: MessageKey }[] = [
  { value: 'all', labelKey: 'skills.validation.source.all' },
  { value: 'user_authored', labelKey: 'skills.validation.source.custom' },
  { value: 'first_party', labelKey: 'skills.validation.source.bundled' },
  { value: 'materialization', labelKey: 'skills.validation.source.appliedAtSpawn' },
];

export const SEVERITY_OPTIONS: { value: SeverityFilter; labelKey: MessageKey }[] = [
  { value: 'all', labelKey: 'skills.validation.severity.all' },
  { value: 'pass', labelKey: 'skills.validation.severity.passed' },
  { value: 'error', labelKey: 'skills.status.needsAttention' },
  { value: 'warn', labelKey: 'skills.validation.severity.warning' },
  { value: 'info', labelKey: 'skills.validation.severity.info' },
];

export const TIME_OPTIONS: { value: TimeFilter; labelKey: MessageKey }[] = [
  { value: 'all', labelKey: 'skills.validation.time.all' },
  { value: '24h', labelKey: 'skills.validation.time.24h' },
  { value: '7d', labelKey: 'skills.validation.time.7d' },
  { value: '30d', labelKey: 'skills.validation.time.30d' },
];
