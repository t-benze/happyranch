/**
 * Pure projections over `AuditEntry` lists for the Audit Traces tab.
 *
 * Cost aggregation MUST run over an unfiltered audit window — when the user
 * navigates from a thread, the audit query is narrowed to a single agent,
 * but the selected task's recall tree typically delegates to other agents
 * whose session_end rows would otherwise be excluded.
 */
import type { AuditEntry } from '@/lib/api/types';

export interface CostCell {
  tokens: number;
  usd?: number;
}

export interface TaskPickerRow {
  task_id: string;
  agent: string | null;
  latest: string;
}

function tokensFromPayload(p: Record<string, unknown>): number {
  const tu = p['token_usage'];
  if (tu && typeof tu === 'object' && 'total' in tu) {
    const t = (tu as Record<string, unknown>).total;
    if (typeof t === 'number') return t;
  }
  const tc = p['token_count'];
  return typeof tc === 'number' ? tc : 0;
}

function usdFromPayload(p: Record<string, unknown>): number | undefined {
  const tu = p['token_usage'];
  if (tu && typeof tu === 'object' && 'total_cost_usd' in tu) {
    const v = (tu as Record<string, unknown>).total_cost_usd;
    if (typeof v === 'number') return v;
  }
  return undefined;
}

export function projectCosts(entries: AuditEntry[]): Record<string, CostCell> {
  const out: Record<string, CostCell> = {};
  for (const e of entries) {
    if (e.action !== 'session_end' || !e.task_id) continue;
    const cell = out[e.task_id] ?? { tokens: 0 };
    cell.tokens += tokensFromPayload(e.payload);
    const u = usdFromPayload(e.payload);
    if (u != null) cell.usd = (cell.usd ?? 0) + u;
    out[e.task_id] = cell;
  }
  return out;
}

export function recentTaskIds(entries: AuditEntry[]): TaskPickerRow[] {
  const map = new Map<string, { agent: string | null; latest: string }>();
  for (const e of entries) {
    if (!e.task_id) continue;
    const existing = map.get(e.task_id);
    if (!existing || existing.latest < e.created_at) {
      map.set(e.task_id, { agent: e.agent, latest: e.created_at });
    }
  }
  return [...map.entries()]
    .map(([task_id, v]) => ({ task_id, ...v }))
    .sort((a, b) => (a.latest < b.latest ? 1 : -1));
}
