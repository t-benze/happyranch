/**
 * URL <-> AuditFilters codec.
 *
 * Filters live in the page URL so deep links, refresh, and back/forward
 * all behave the same. `since` is a small enum of quick-pick tokens; the
 * daemon expects an ISO timestamp, so `sinceToISO` materializes the
 * wire-shape value at query time.
 */

export type SinceToken = '24h' | '7d' | 'all';

export interface AuditFilters {
  agent: string | null;
  action: string | null;
  since: SinceToken | null;
  task_id: string | null;
}

const SINCE_TOKENS: ReadonlyArray<SinceToken> = ['24h', '7d', 'all'];

function isSinceToken(s: string | null): s is SinceToken {
  return s !== null && (SINCE_TOKENS as readonly string[]).includes(s);
}

export function decodeFilters(params: URLSearchParams): AuditFilters {
  const sinceRaw = params.get('since');
  return {
    agent: params.get('agent') || null,
    action: params.get('action') || null,
    since: isSinceToken(sinceRaw) ? sinceRaw : null,
    task_id: params.get('task_id') || null,
  };
}

export function encodeFilters(f: AuditFilters): string {
  const p = new URLSearchParams();
  if (f.agent) p.set('agent', f.agent);
  if (f.action) p.set('action', f.action);
  if (f.since) p.set('since', f.since);
  if (f.task_id) p.set('task_id', f.task_id);
  return p.toString();
}

const DAY_MS = 24 * 60 * 60 * 1000;

export function sinceToISO(
  token: SinceToken | null,
  now: Date = new Date(),
): string | null {
  if (token === null || token === 'all') return null;
  const days = token === '24h' ? 1 : 7;
  return new Date(now.getTime() - days * DAY_MS).toISOString();
}
