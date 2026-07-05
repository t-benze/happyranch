/** Mirror of src/daemon/routes/audit.py */
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
    /** Opaque keyset cursor from a prior response's `next_cursor`. Omit for
     *  the first page; the daemon AND-composes it with all other filters. */
    cursor?: string;
    /** Enrich Thread-scope entries with _thread_dream_id (A4 marker). */
    include_thread_origin?: boolean;
  },
): Promise<{ entries: AuditEntry[]; next_cursor?: string | null }> =>
  request(`/orgs/${slug}/audit`, {
    params: { ...params, include_thread_origin: params?.include_thread_origin ?? true },
  });
