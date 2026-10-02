/**
 * Display helpers for Usage v1 (THR-272 PR4).
 *
 * Every value here is formatting of a figure the daemon already computed —
 * windows, medians, rates, coverage and deltas all come from
 * `GET /usage/workload` and `GET /usage/efficiency`. Nothing in this module
 * derives a window, a percentage change or a baseline client-side.
 */
import { formatCount as formatLocaleCount, formatTokens } from '@/lib/format';

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;

/**
 * "Sep 22, 14:03" from a daemon `*_local` ISO string. Reads the wall-clock
 * parts written in the string (already rendered in the org timezone), so the
 * browser's own timezone never shifts the label.
 */
export function formatLocalStamp(local: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(local);
  if (!m) return local;
  const month = MONTHS[Number(m[2]) - 1];
  if (!month) return local;
  return `${month} ${Number(m[3])}, ${m[4]}:${m[5]}`;
}

export function formatWindow(window: { start_local: string; end_local: string }): string {
  return `${formatLocalStamp(window.start_local)} – ${formatLocalStamp(window.end_local)}`;
}

/** A UTC instant rendered in the response's timezone ("Sep 29, 14:03"). */
export function formatInstant(iso: string, timeZone: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone,
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(date);
  } catch {
    return iso;
  }
}

export function formatCount(n: number): string {
  return formatLocaleCount(n, 'en-US');
}

/** Token figures: compact, with the exact value kept for titles / AT. */
export function formatTokenValue(n: number): string {
  return formatTokens(n);
}

export function formatExactTokens(n: number): string {
  return n.toLocaleString('en-US', { maximumFractionDigits: 1 });
}

/** Recorded agent runtime: "4h 10m", "45m", "38s". */
export function formatDuration(totalSeconds: number): string {
  const s = Math.round(Math.abs(totalSeconds));
  if (s < 60) return `${s}s`;
  const minutes = Math.floor(s / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${minutes}m`;
  return `${hours}h ${String(rest).padStart(2, '0')}m`;
}

/** A server-supplied rate in [0, 1] as a whole percentage. */
export function formatRate(rate: number): string {
  const pct = rate * 100;
  if (pct > 0 && pct < 1) return '<1%';
  return `${Math.round(pct)}%`;
}

const MINUS = '−';

export function signed(text: string, negative: boolean): string {
  return `${negative ? MINUS : '+'}${text}`;
}

/** A server-supplied percent movement ("−8%", "+14%", "+0.4%"). */
export function formatPercentDelta(value: number): string {
  const abs = Math.abs(value);
  const text = abs < 10 ? String(Math.round(abs * 10) / 10) : String(Math.round(abs));
  return signed(`${text}%`, value < 0);
}

/**
 * Plain-language explanations for the daemon's `withheld_reason` codes.
 * Raw codes are never rendered.
 */
export function withheldExplanation(reason: string | null): string {
  switch (reason) {
    case 'usage_coverage_below_95_percent':
      return 'Comparison withheld: usage is known for under 95% of runs in one of the periods.';
    case 'unattributed_lifecycle_runs':
      return 'Comparison withheld: some runs have no CLI/model on record and could belong to this row.';
    case 'invalid_baseline':
      return 'Comparison withheld: there is no comparable value in one of the periods.';
    case 'reply_outcome_not_recorded':
      return 'Comparison withheld: the reply outcome was not recorded for some wakes.';
    default:
      return 'Comparison not available for this value.';
  }
}

export const ROW_LEVEL_WITHHELD_REASONS: ReadonlySet<string> = new Set([
  'usage_coverage_below_95_percent',
  'unattributed_lifecycle_runs',
]);
