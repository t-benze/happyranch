/** Mirror of runtime/daemon/routes/audit.py */
import { request } from './client';
import type { AuditEntry } from './types';

export type { AuditEntry } from './types';

export const listAudit = (
  slug: string,
  params?: {
    task_id?: string;
    agent?: string;
    action?: string;
    since?: string;
    limit?: number;
    /** Keyset cursor for next page (from a prior response's next_cursor). */
    cursor?: string;
    /** Enrich Thread-scope entries with _thread_dream_id (A4 marker). */
    include_thread_origin?: boolean;
  },
): Promise<{ entries: AuditEntry[]; next_cursor?: string | null }> =>
  request(`/orgs/${slug}/audit`, {
    params: { ...params, include_thread_origin: params?.include_thread_origin ?? true },
  });
